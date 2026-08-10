#!/usr/bin/env bash
# End-to-end CLI smoke test for an already-installed agents-md-compiler.
#
# Asserts the exact state tokens and exit codes frozen in docs/cli-contract.md
# against a real filesystem. Both the working directory and the state root live
# under the disposable directory passed as the first argument, so this script
# never reads or writes anything else. It deliberately does not import the
# package: it exercises the installed console script the way an operator does.
#
# Usage: scripts/smoke.sh <workdir> <expected-version>

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <workdir> <expected-version>" >&2
  exit 64
fi

workdir=$1
expected_version=$2

# Windows runners expose `python`; Linux and macOS expose `python3`. Resolve once
# so the JSON field extraction below works identically on all three.
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "smoke: no python interpreter on PATH" >&2
  exit 69
fi

mkdir -p "$workdir"
workdir=$(cd "$workdir" && pwd)
export XDG_STATE_HOME="$workdir/state"
export HOME="$workdir/home"
export USERPROFILE="$HOME"
mkdir -p "$HOME/.codex"
cd "$workdir"

manifest="$workdir/bundle/agents-md.toml"
target="$HOME/.codex/AGENTS.md"
failures=0

note() { printf '\n== %s\n' "$*"; }

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

# Runs the CLI and captures stdout and the exit code without tripping `set -e`.
capture() {
  set +e
  out=$(agents-md-compiler "$@" 2>"$workdir/stderr.txt")
  rc=$?
  set -e
}

# Asserts an exit code only, for commands whose stdout is not a JSON envelope.
expect_rc() {
  local want=$1 label=$2
  shift 2
  capture "$@"
  if [ "$rc" -ne "$want" ]; then
    fail "$label: exit code $rc, wanted $want"
    printf '  stdout: %s\n' "$out" >&2
    printf '  stderr: %s\n' "$(cat "$workdir/stderr.txt")" >&2
  else
    printf 'ok   %-46s rc=%s\n' "$label" "$rc"
  fi
}

# Asserts both the documented state token and the documented exit code. Reading
# the token from the JSON envelope rather than the human text is deliberate: the
# envelope is the frozen machine contract.
expect_state() {
  local want_state=$1 want_rc=$2 label=$3
  shift 3
  capture "$@" --format json
  local got
  got=$(printf '%s' "$out" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["state"])' 2>/dev/null || echo PARSE_ERROR)
  if [ "$got" != "$want_state" ] || [ "$rc" -ne "$want_rc" ]; then
    fail "$label: state=$got rc=$rc, wanted state=$want_state rc=$want_rc"
    printf '  stdout: %s\n' "$out" >&2
    printf '  stderr: %s\n' "$(cat "$workdir/stderr.txt")" >&2
  else
    printf 'ok   %-46s state=%-18s rc=%s\n' "$label" "$got" "$rc"
  fi
}

field() {
  agents-md-compiler status --manifest "$manifest" --format json |
    "$PY" -c "import json,sys; print(json.load(sys.stdin)['$1'])"
}

note "version reporting"
capture --version
if [ "$out" != "$expected_version" ]; then
  fail "--version reported '$out', wanted '$expected_version'"
else
  printf 'ok   %-46s %s\n' "--version" "$out"
fi
expect_rc 0 "version subcommand" version
expect_rc 0 "--help" --help

note "scaffold"
expect_rc 0 "init" init --directory bundle --bundle-id smoke
for f in bundle/agents-md.toml bundle/modules/core.md bundle/modules/python.md; do
  [ -f "$f" ] || fail "init did not create $f"
done
# init must refuse to clobber. Any other behavior would make the tool unsafe to
# re-run in a populated directory.
expect_rc 1 "init refuses existing targets" init --directory bundle --bundle-id smoke

note "lock lifecycle"
expect_state LOCK_MISSING 2 "status before lock" status --manifest "$manifest"
expect_rc 0 "lock" lock --manifest "$manifest"
expect_rc 0 "lock --check on a current lock" lock --manifest "$manifest" --check
expect_rc 0 "validate" validate --manifest "$manifest"

note "render"
capture render --manifest "$manifest" --locked
rendered="$out"
for needle in '<!-- agents-md-compiler:generated format=2 -->' '<!-- bundle-id: smoke -->' \
  'module-begin id=core' 'module-end id=core' 'module-begin id=python' 'module-end id=python'; do
  case "$rendered" in
    *"$needle"*) printf 'ok   %-46s present\n' "$needle" ;;
    *) fail "rendered output is missing: $needle" ;;
  esac
done
# render must not create the target as a side effect.
[ -f "$target" ] && fail "render wrote the target"

note "install"
expect_state MISSING 2 "check before install" check --manifest "$manifest"
expect_state MISSING 2 "install dry run" install --manifest "$manifest"
[ -f "$target" ] && fail "the install dry run wrote the target"
expect_state CURRENT 0 "install --apply" install --manifest "$manifest" --apply
[ -f "$target" ] || fail "install --apply did not create the target"
expect_state CURRENT 0 "check after install" check --manifest "$manifest"
expect_state CURRENT 0 "status after install" status --manifest "$manifest"

# The installed bytes must equal the rendered bytes exactly.
printf '%s\n' "$rendered" >"$workdir/expected.md"
if cmp -s "$workdir/expected.md" "$target"; then
  printf 'ok   %-46s byte-identical\n' "installed bytes == rendered bytes"
else
  fail "the installed target does not match the rendered bytes"
fi

note "drift detection and repair"
printf 'operator edit\n' >>"$target"
expect_state DRIFTED 2 "check detects drift" check --manifest "$manifest"
expect_state CURRENT 0 "reinstall repairs drift" install --manifest "$manifest" --apply

note "rollback"
receipt=$(field latest_receipt)
[ -f "$receipt" ] || fail "no receipt at $receipt"
before=$(field target_sha256)
expect_rc 0 "rollback --apply" rollback --receipt "$receipt" --manifest "$manifest" --apply
after=$("$PY" - "$target" <<'EOF'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "ABSENT")
EOF
)
if [ "$after" = "$before" ]; then
  fail "rollback left the installed bytes in place"
else
  printf 'ok   %-46s %s -> %s\n' "rollback changed the target" "${before:0:12}" "${after:0:12}"
fi

note "unmanaged target refusal"
mkdir -p foreign/modules
cp bundle/modules/core.md bundle/modules/python.md foreign/modules/
sed \
  -e 's/^bundle_id = "smoke"$/bundle_id = "foreign"/' \
  -e 's|^default_target = "~/.codex/AGENTS.md"$|default_target = "AGENTS.md"|' \
  bundle/agents-md.toml >foreign/agents-md.toml
foreign_manifest="$workdir/foreign/agents-md.toml"
printf '# Hand written by the operator.\n' >"$workdir/foreign/AGENTS.md"
expect_rc 0 "lock the foreign bundle" lock --manifest "$foreign_manifest"
expect_state UNMANAGED_TARGET 3 "install refuses an unmanaged target" \
  install --manifest "$foreign_manifest" --apply
if grep -q 'Hand written by the operator' "$workdir/foreign/AGENTS.md"; then
  printf 'ok   %-46s untouched\n' "the unmanaged file"
else
  fail "the unmanaged file was modified"
fi
# --replace-unmanaged alone is not enough: overwriting a file the tool did not
# write also requires naming that file's exact current digest, so the operator
# cannot destroy bytes they have not looked at.
expect_state UNMANAGED_TARGET 3 "--replace-unmanaged alone is refused" \
  install --manifest "$foreign_manifest" --apply --replace-unmanaged
foreign_digest=$("$PY" - "$workdir/foreign/AGENTS.md" <<'EOF'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
EOF
)
expect_state CURRENT 0 "--replace-unmanaged with the exact digest" \
  install --manifest "$foreign_manifest" --apply --replace-unmanaged \
  --expect-target-sha256 "$foreign_digest"
# A stale digest must be refused even with --replace-unmanaged, because the file
# changed between the operator looking at it and this call.
printf '# Changed after the operator looked.\n' >"$workdir/foreign/AGENTS.md"
expect_state UNMANAGED_TARGET 3 "a stale --expect-target-sha256 is refused" \
  install --manifest "$foreign_manifest" --apply --replace-unmanaged \
  --expect-target-sha256 "$foreign_digest"

note "summary"
if [ "$failures" -eq 0 ]; then
  echo "smoke: all assertions passed"
else
  echo "smoke: $failures assertion(s) failed" >&2
  exit 1
fi

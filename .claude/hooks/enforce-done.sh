#!/usr/bin/env bash
# Stop hook: block "done" while this repository's gate is red for changed files.
#
# Properties this script must keep:
#   - Loop-guarded through stop_hook_active, so a permanently red gate cannot run
#     away.
#   - Cost-scoped: runs the gate only when the working tree actually changed.
#   - Portable: no jq, no python3, no GNU-only flags. Must parse and run under
#     macOS bash 3.2.57 and a modern Linux bash.
#   - Fails open. A missing gate runner warns the operator and never wedges the
#     session. This hook never edits a tracked file.
#
# Block mechanism: exit 2 with the reason on stderr. Per the Claude Code hooks
# reference, a Stop hook exiting 2 prevents the stop and feeds stderr back as the
# reason to act on, so no JSON encoder is required.
set -uo pipefail

if [ -t 0 ]; then
  input=""
else
  input="$(cat)"
fi

# Loop guard: if this stop attempt was already blocked once, allow it through.
case "$input" in
  *'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$root" 2>/dev/null || exit 0

# Cost scope: a clean tree has nothing to gate.
if git diff --quiet 2>/dev/null \
  && git diff --cached --quiet 2>/dev/null \
  && [ -z "$(git ls-files --others --exclude-standard 2>/dev/null)" ]; then
  exit 0
fi

# prek is this repository's gate runner. Absent means warn, never wedge.
if ! command -v prek >/dev/null 2>&1; then
  printf '{"systemMessage":"[enforce-done] prek not on PATH - gate NOT enforced this stop."}\n'
  exit 0
fi

# Gate the existing staged, unstaged, and untracked paths. No HEAD ref is used so
# this works before the first commit; --diff-filter=d drops deletions so prek is
# never handed a path that no longer exists.
files="$(mktemp)"
{
  git diff --name-only -z --diff-filter=d 2>/dev/null
  git diff --cached --name-only -z --diff-filter=d 2>/dev/null
  git ls-files --others --exclude-standard -z 2>/dev/null
} >"$files"

if [ ! -s "$files" ]; then
  rm -f "$files"
  exit 0
fi

log="$(mktemp)"
if xargs -0 prek run --files <"$files" >"$log" 2>&1; then
  rm -f "$files" "$log"
  exit 0
fi
rm -f "$files"

{
  echo "Definition-of-done gate is RED (prek run --files). Do NOT stop: fix the failures, rerun the gate, then stop."
  echo
  echo "Last gate output:"
  tail -c 1500 "$log"
} >&2
rm -f "$log"
exit 2

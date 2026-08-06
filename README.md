# agents-md-compiler

Compile an ordered set of Markdown policy modules into one deterministic,
verifiable global `AGENTS.md`. No runtime dependencies, no network access, no
shell invocation, and no LLM anywhere in the pipeline.

## Why this exists

Coding agents read their instruction files once, at startup. Codex selects a
single non-empty global file, builds its project instruction chain from the
project root through the startup working directory, and then stops looking.
Changing directories later does not add instructions to a running session, and
Codex treats a Claude-style `@path` import as ordinary Markdown rather than as an
include directive.

That leaves two options for a rule that must be available before task scope is
known: paste everything into one enormous hand-maintained file, or generate that
file from modular sources. Hand-maintaining it means the file drifts from the
sources, nobody can tell which version is live, and a careless edit silently
deletes a rule.

This tool takes the second option and makes it auditable:

- canonical Markdown modules stay the only editable policy sources;
- one reviewed manifest fixes the module set and its order;
- a lock records each source's resolved path, byte size, and SHA-256;
- rendering emits one self-contained file with stable provenance headers and the
  exact source bytes between generated markers;
- checking detects source drift, output drift, a missing target, shadowing, and
  an unmanaged target;
- installation is explicit, backed up, concurrency-safe, and atomic;
- rollback restores the exact prior bytes, and refuses if the target changed;
- verification inspects the model-visible startup input instead of trusting a
  model to say it read the rules.

It deliberately does not generate nested or project-level `AGENTS.md` files, and
it never detects your stack and writes rules for it. A repository may keep its own
root `AGENTS.md` for concrete project facts; this compiler leaves that file alone.

## Requirements

- Python 3.14 or newer.
- Linux, macOS, or Windows.
- No runtime dependencies. The package uses the standard library only.
- `codex` on `PATH` only for the optional `verify-codex` command.

## Install

```bash
# Recommended: install as an isolated tool.
uv tool install agents-md-compiler

# Or run without installing.
uvx agents-md-compiler --help

# Or into an existing environment.
pip install agents-md-compiler
```

Both invocation forms are supported and equivalent:

```bash
agents-md-compiler --help
python -m agents_md_compiler --help
```

## Five minutes end to end

Scaffold an example manifest and modules, then lock, render, check, and install.

```bash
# 1. Scaffold. The manifest targets ~/.codex/AGENTS.md by default.
agents-md-compiler init --directory ./policy

# 2. Create the selected target's parent if this is a new Codex home.
mkdir -p ~/.codex

# 3. Record exact source paths, sizes, and digests.
agents-md-compiler lock --manifest ./policy/global-agents.toml

# 4. Validate the manifest, lock, sources, and rendered structure.
agents-md-compiler validate --manifest ./policy/global-agents.toml

# 5. Look at the bytes before writing anything anywhere.
agents-md-compiler render --manifest ./policy/global-agents.toml --locked | head -20

# 6. Compare a fresh locked render against the target.
agents-md-compiler check --manifest ./policy/global-agents.toml

# 7. Preview the install. This writes nothing at all.
agents-md-compiler install --manifest ./policy/global-agents.toml

# 8. Install for real.
agents-md-compiler install --manifest ./policy/global-agents.toml --apply
```

Editing a canonical module after step 3 makes `check` exit 2 with `LOCK_STALE`
until you rerun `lock`. That coupling is the point: the reviewed manifest, the
lock, and the live file always agree or the tool tells you they do not.

## Choose where output goes

Use one of three destination controls according to the operation:

- Set manifest `default_target` for the persistent managed destination. A relative
  value resolves from the manifest directory. `~` expands to the user's home.
- Pass `--target PATH` to `validate`, `check`, `status`, `install`, `rollback`, or
  `verify-codex` to override the managed destination for one invocation. A relative
  command-line path resolves from the current working directory.
- Pass `render --locked --output PATH` to export one new standalone file. This
  never replaces an existing path and does not create its parent directory.

For example, preview and then install to an explicit managed target:

```bash
agents-md-compiler install \
  --manifest ./policy/global-agents.toml \
  --target ~/.codex/AGENTS.md

agents-md-compiler install \
  --manifest ./policy/global-agents.toml \
  --target ~/.codex/AGENTS.md \
  --apply
```

The selected target may have another file name, but only a real global
`AGENTS.md` can be verified through Codex startup input. `verify-codex` therefore
fails for an otherwise current custom target that Codex does not load globally.

## Safety behavior you should know before installing

**An existing target that this tool did not generate is never replaced
silently.** If the target exists but carries no recognized generated header,
`install --apply` refuses with `UNMANAGED_TARGET` and exit code 3. Adopting that
file requires both flags, and the digest must match what you captured from the
dry run:

```bash
# Capture the exact current bytes first.
shasum -a 256 ~/.codex/AGENTS.md

EXPECTED_TARGET_SHA256=<the 64-character digest you just captured>
agents-md-compiler install \
  --manifest ./policy/global-agents.toml \
  --apply --replace-unmanaged \
  --expect-target-sha256 "${EXPECTED_TARGET_SHA256}"
```

If the digest does not match, the install refuses rather than overwriting a file
that changed under you.

Every successful install writes an immutable backup and a receipt under the user
state root:

- Linux and macOS: `${XDG_STATE_HOME:-~/.local/state}/agents-md-compiler/`
- Windows: `%LOCALAPPDATA%/agents-md-compiler/`

Rollback takes a specific receipt and refuses if the target changed after
installation:

```bash
agents-md-compiler rollback --receipt <path/to/receipt.json> --apply
```

Backups are never deleted or rotated automatically. If installation created a
previously missing target, rollback moves the generated file into the state
directory instead of deleting it irrecoverably.

Other refusals worth knowing:

- `render --output PATH` writes only to a path that does not exist. Use `install`
  when you want replacement, backup, and rollback semantics.
- The selected output or target parent must already exist and must be a directory.
  Dry runs reject an unusable parent before describing the target as missing.
- A symlinked target is refused, never followed.
- A symlinked source is refused, with both the lexical and resolved paths
  reported.
- A non-empty global `AGENTS.override.md` beside the target is a `SHADOWED`
  failure, because Codex would load the override instead of the file you just
  installed.

If an install fails after atomically replacing the target but before all receipt
state is committed, recovery runs while the target lock is still held. It first
proves the target still contains the bytes written by that operation. It then
restores an existing predecessor from its verified backup, or preserves a newly
created generated file under the private state root and restores the target to
its prior missing state. If either proof fails, the command reports recovery as
failed and does not overwrite unknown bytes.

## Guarantees

Deterministic output:

- identical manifest bytes, lock bytes, format version, and source bytes produce
  identical output bytes;
- generated output contains no timestamp, host name, process ID, random value,
  temporary path, tool version, or working directory, so upgrading this tool
  cannot change your policy bytes;
- manifest order is output order, and rendering never depends on directory
  enumeration order.

Source preservation:

- every source is read as strict UTF-8 bytes and must use LF line endings with
  exactly one final LF;
- a UTF-8 BOM, a NUL byte, a CR byte, or invalid UTF-8 is rejected;
- accepted bytes are copied verbatim between generated markers, never trimmed,
  reflowed, lint-fixed, normalized, or summarized;
- a source containing a compiler marker line is rejected rather than escaped.

Provenance, not trust: digests prove that bytes did not change between locking
and rendering. They establish neither authorship nor safety. See
[`docs/security-model.md`](docs/security-model.md).

## JSON automation

Every command accepts `--format json` and prints exactly one JSON object to
stdout. Diagnostics stay on stderr, so a pipeline can parse stdout unconditionally.

```bash
agents-md-compiler status --manifest ./policy/global-agents.toml --format json
```

```json
{
  "command": "status",
  "ok": true,
  "schema_version": 1,
  "state": "CURRENT"
}
```

Drive automation from the exit code, and read `state` for the reason:

| Code | Meaning                                                                  |
|------|--------------------------------------------------------------------------|
| 0    | Succeeded; `CURRENT` when a target state applies                         |
| 1    | Invalid invocation, invalid manifest, lock, or source, or an I/O error   |
| 2    | Read-only difference: `LOCK_MISSING`, `LOCK_STALE`, `DRIFTED`, `MISSING` |
| 3    | Safety refusal: `SHADOWED`, `UNMANAGED_TARGET`, `CONCURRENT_CHANGE`      |

`--quiet` suppresses non-error stderr. It never suppresses JSON output or
requested render output.

## Codex verification, and its limits

`verify-codex` confirms that the installed bundle is actually visible to the
model at startup:

```bash
agents-md-compiler verify-codex --manifest ./policy/global-agents.toml
```

It resolves `codex` from `PATH` without a shell, records `codex --version`,
confirms the CLI exposes `debug prompt-input`, runs a non-interactive probe from a
disposable directory containing no project instruction file, parses the returned
JSON, and confirms that the generated header, every begin and end module marker,
and unique content sentinels from the first and last modules are all present.

What it does not do:

- send a model request or require API authentication;
- modify Codex configuration or copy policy files into the probe directory;
- claim semantic compliance. Marker presence proves the bytes reached the model's
  input. It does not prove the model obeyed them.

`codex debug prompt-input` is a debug interface, not a promised stable API. If the
installed Codex removes or changes it, this command reports `RUNTIME_UNVERIFIED`
with the exact command and the observed failure, and exits 1. It never falls back
to asking a model to summarize its own instructions.

## Documentation

| Document                                                   | Contents                                                        |
|------------------------------------------------------------|-----------------------------------------------------------------|
| [`docs/manifest-v1.md`](docs/manifest-v1.md)               | Manifest schema version 1, path bases, and every rejection rule |
| [`docs/rendered-format-v1.md`](docs/rendered-format-v1.md) | Exact output bytes, markers, and hashing boundaries             |
| [`docs/cli-contract.md`](docs/cli-contract.md)             | Commands, options, JSON envelopes, states, and exit codes       |
| [`docs/security-model.md`](docs/security-model.md)         | Trust boundaries, path and TOCTOU handling, and recovery        |
| [`AGENTS.md`](AGENTS.md)                                   | Project contract for agents working on this repository          |
| [`CONTRIBUTING.md`](CONTRIBUTING.md)                       | Setup, commit conventions, and release boundaries               |

## License

MIT. See [`LICENSE`](LICENSE).

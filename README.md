# agents-md-compiler

Compile ordered Markdown policy modules into one deterministic, verifiable
`AGENTS.md`. The target can be global, repository-local, nested, or another
explicit path. The runtime has no dependencies, performs no network access,
invokes no shell, and uses no LLM.

## The mental model

One manifest owns one ordered module set and one default target. The compiler
does not need a scope flag because Codex scope comes from the target path and the
startup working directory:

| Purpose           | Typical target            | Verification context           |
|-------------------|---------------------------|--------------------------------|
| Global policy     | `~/.codex/AGENTS.md`      | Isolated empty directory       |
| Repository policy | `<repo>/AGENTS.md`        | Repository root or descendant  |
| Nested policy     | `<repo>/<area>/AGENTS.md` | Target directory or descendant |
| Standalone export | Any new path              | No Codex visibility promise    |

The same `lock`, `validate`, `check`, `install`, `status`, and `rollback`
workflow applies to every target. `verify-codex` adds the startup directory
needed to prove that Codex actually discovers project and nested targets.

Codex loads one global instruction file and then builds a project instruction
chain from the project root toward its startup working directory. A closer file
is applied later. See the official
[Codex AGENTS.md guide](https://developers.openai.com/codex/guides/agents-md).

## Why compile instruction files

Canonical modules are easier to review and reuse than one large hand-maintained
file, but Codex needs a self-contained file at startup. This compiler connects
those needs without hiding provenance:

- canonical Markdown modules remain the only editable policy sources;
- a strict manifest fixes the source set, order, and target;
- a portable lock records each lexical source path, byte size, and SHA-256;
- rendering preserves every accepted source byte between generated markers;
- checking detects source drift, output drift, a missing target, shadowing, and
  unmanaged content;
- installation is explicit, atomic, backed up, and concurrency-safe;
- rollback restores exact prior bytes and refuses after an external target edit;
- runtime verification inspects Codex prompt input instead of asking a model what
  it thinks it read.

The tool does not discover repositories, detect stacks, select targets, or
generate multiple files from one manifest. It does not author, rewrite,
summarize, lint-fix, normalize, or deduplicate policy prose.

## Requirements and installation

- Python 3.14 or newer.
- Linux, macOS, or Windows.
- `codex` on `PATH` only for optional runtime visibility verification.

```bash
# Recommended: install as an isolated tool.
uv tool install agents-md-compiler

# Or run without installing.
uvx agents-md-compiler --help

# Or install into an existing environment.
pip install agents-md-compiler
```

Both invocation forms are equivalent:

```bash
agents-md-compiler --help
python -m agents_md_compiler --help
```

## Global quickstart

`init` defaults to the active global base file and prints the exact lock command
to run next.

```bash
# Creates policy/agents-md.toml and policy/modules/*.md.
agents-md-compiler init --directory ./policy --bundle-id personal-policy

# Create the target parent when this is a new Codex home.
mkdir -p ~/.codex

agents-md-compiler lock --manifest ./policy/agents-md.toml
agents-md-compiler validate --manifest ./policy/agents-md.toml
agents-md-compiler render --manifest ./policy/agents-md.toml --locked
agents-md-compiler check --manifest ./policy/agents-md.toml

# Preview writes nothing.
agents-md-compiler install --manifest ./policy/agents-md.toml

# Explicitly mutate the target.
agents-md-compiler install --manifest ./policy/agents-md.toml --apply

# Prove the active global file is visible without project instructions.
agents-md-compiler verify-codex --manifest ./policy/agents-md.toml
```

## Repository quickstart

Run this from the repository root. The target argument resolves from the current
directory. `init` serializes it relative to `.agents`, so the generated manifest
contains the portable value `../AGENTS.md`.

```bash
agents-md-compiler init \
  --directory ./.agents \
  --target ./AGENTS.md \
  --bundle-id project-policy

agents-md-compiler lock --manifest ./.agents/agents-md.toml
agents-md-compiler validate --manifest ./.agents/agents-md.toml
agents-md-compiler check --manifest ./.agents/agents-md.toml
agents-md-compiler install --manifest ./.agents/agents-md.toml
agents-md-compiler install --manifest ./.agents/agents-md.toml --apply

# Prove the repository file is visible from this startup directory.
agents-md-compiler verify-codex \
  --manifest ./.agents/agents-md.toml \
  --cwd .
```

For a nested target, use the same layout and pass a working directory equal to or
below the target's parent:

```bash
agents-md-compiler verify-codex \
  --manifest ./.agents/backend/agents-md.toml \
  --cwd ./backend/service
```

`CURRENT` means the selected target is byte-identical to a fresh locked render.
It does not claim that Codex discovers that target from every possible working
directory. A successful `verify-codex` makes the visibility claim only for its
reported startup directory.

## Normal edit workflow

1. Edit canonical module files, never the generated target.
2. Review the module and manifest changes.
3. Run `lock` explicitly to refresh source identity.
4. Run `validate` and review `render --locked` when needed.
5. Run `check` to see whether the selected target needs installation.
6. Preview `install`, then rerun it with `--apply`.
7. Run `verify-codex` with the applicable startup context.

Editing a module after locking produces `LOCK_STALE` and exit code 2. Read-only
commands never refresh a lock or install a target implicitly.

When `--manifest` is omitted, the CLI uses `./agents-md.toml`. It falls back to
legacy `./global-agents.toml` only when the neutral default is absent. If both
exist, the invocation is ambiguous and must use explicit `--manifest`.

## Choosing a destination

- Manifest `default_target` is the persistent managed destination. A relative
  value resolves from the manifest directory. A leading `~` expands through the
  standard home lookup.
- `--target PATH` overrides the managed destination for one invocation. A
  relative command-line path resolves from the current working directory.
- `render --locked --output PATH` writes one standalone file only when the path
  does not exist. It never replaces an existing file or creates its parent.

Codex visibility is promised only for paths Codex actually discovers. A custom
file name can still use compilation, checking, installation, and rollback, but a
runtime probe will not pass unless Codex includes those bytes through its normal
instruction discovery.

## Adopting an existing AGENTS.md

An existing file without a recognized generated header is unmanaged and is never
replaced silently. First preview the install and capture the current file digest.
Then provide both adoption flags:

```bash
shasum -a 256 ./AGENTS.md

EXPECTED_TARGET_SHA256=<captured-64-character-digest>
agents-md-compiler install \
  --manifest ./.agents/agents-md.toml \
  --apply \
  --replace-unmanaged \
  --expect-target-sha256 "${EXPECTED_TARGET_SHA256}"
```

A digest mismatch refuses the write. A successful adoption stores the exact
handwritten predecessor as a backup before replacing it.

## Safety and operational state

All target mutations require `--apply`. Dry runs create no target, backup,
receipt, deployment directory, or advisory lock.

New operational evidence is scoped by bundle ID and resolved target digest under
the user state root:

- Linux and macOS: `${XDG_STATE_HOME:-~/.local/state}/agents-md-compiler/`
- Windows: `%LOCALAPPDATA%/agents-md-compiler/`

Advisory locks live in one distribution-wide namespace and are keyed only by the
resolved protected path. Two different bundle IDs therefore coordinate when they
select the same target. Two targets using one bundle ID retain separate receipts,
backups, preserved files, and last-install state.

Rollback consumes one explicit install receipt:

```bash
agents-md-compiler rollback \
  --manifest ./.agents/agents-md.toml \
  --receipt <path/to/install-receipt.json> \
  --apply
```

Rollback refuses unless the target still matches the receipt's installed digest.
Backups are never deleted or rotated automatically. When an install created a
previously missing target, rollback moves the generated file into private state
instead of deleting it irrecoverably.

Other safety boundaries:

- sources and targets that are symbolic links are refused;
- target parents must already exist and be directories;
- a non-empty sibling `AGENTS.override.md` shadows a selected `AGENTS.md` and
  causes `SHADOWED`;
- a source may not alias the selected output;
- a source containing the compiler marker prefix is rejected;
- an unknown future rendered format is unmanaged and requires explicit adoption.

If a post-replacement step fails, recovery runs under the same target lock. It
first proves that the target still contains the operation's bytes, then restores
the predecessor from its verified backup or preserves a newly generated file and
restores prior absence.

## Format-1 migration

Current releases emit portable lock format 2 and neutral rendered format 2.

- A strict format-1 lock is migration input. Read-only commands report
  `LOCK_STALE`; explicit `lock` replaces it atomically with format 2.
- Exact format-1 and format-2 rendered targets are managed. A format-1 target is
  `DRIFTED` and upgrades through ordinary explicit install without unmanaged-file
  adoption flags.
- Upgrade installation backs up exact format-1 bytes. Receipt-based rollback can
  restore them when the format-2 target has not changed.
- Legacy bundle-only install receipts remain accepted for explicit validated
  rollback. New evidence is written only to target-qualified deployment state.

## Repository CI

Commit the manifest, canonical modules, portable lock, and generated target when
the repository chooses to track generated output. A read-only CI gate can run:

```bash
agents-md-compiler lock --manifest ./.agents/agents-md.toml --check
agents-md-compiler validate --manifest ./.agents/agents-md.toml
agents-md-compiler check --manifest ./.agents/agents-md.toml
```

`verify-codex` is an environment integration gate, not a deterministic unit gate.
Run it where the intended Codex executable and startup context are available.

## Determinism and preservation guarantees

- Equal manifest and source bytes produce equal format-2 lock and rendered bytes
  across checkout roots.
- Output contains no timestamp, host name, process ID, random value, temporary
  path, resolved source path, package version, or working directory.
- Manifest order is output order. Rendering does not enumerate source folders.
- Sources must be non-empty strict UTF-8 with LF line endings and exactly one
  final LF. BOM, NUL, CR, invalid UTF-8, and marker collisions are rejected.
- Accepted source bytes are copied exactly. The compiler never trims, reflows,
  normalizes, lint-fixes, summarizes, or deduplicates them.

Digests prove that bytes did not change between locking and rendering. They do
not establish authorship, correctness, or safety. See
[`docs/security-model.md`](docs/security-model.md).

## JSON automation

Every command accepts `--format json` and emits exactly one JSON object to stdout.
Diagnostics remain on stderr.

```bash
agents-md-compiler status \
  --manifest ./.agents/agents-md.toml \
  --format json
```

Drive automation from the exit code and inspect `state` for the reason:

| Code | Meaning                                                                  |
|------|--------------------------------------------------------------------------|
| 0    | Succeeded; `CURRENT` when a target state applies                         |
| 1    | Invalid invocation, input, I/O, or runtime verification failure          |
| 2    | Read-only difference: `LOCK_MISSING`, `LOCK_STALE`, `DRIFTED`, `MISSING` |
| 3    | Safety refusal: `SHADOWED`, `UNMANAGED_TARGET`, `CONCURRENT_CHANGE`      |

`--quiet` suppresses non-error stderr. It never suppresses JSON or requested
render output.

## Codex verification limits

`verify-codex` resolves `codex` from `PATH`, captures `codex --version`, confirms
that `debug prompt-input` is available, and searches the returned JSON for the
generated marker, every module boundary, and unique content sentinels from the
first and last modules.

For the active global base or override file, the probe runs from a disposable
empty directory. For a project or nested target, it runs from explicit `--cwd` or
the target parent. The project directory must be equal to or below the target
parent.

The command sends no model request, requires no API authentication, changes no
Codex configuration, and makes no semantic-compliance claim. Marker presence
proves visibility, not obedience. If the debug interface is missing or changed,
the state is `RUNTIME_UNVERIFIED` and the exact failing command is reported.

## Documentation

| Document                                                   | Contents                                          |
|------------------------------------------------------------|---------------------------------------------------|
| [`docs/manifest-v1.md`](docs/manifest-v1.md)               | Manifest schema, path bases, and rejection rules  |
| [`docs/rendered-format-v1.md`](docs/rendered-format-v1.md) | Frozen legacy lock and rendered format            |
| [`docs/rendered-format-v2.md`](docs/rendered-format-v2.md) | Current portable lock and neutral rendered format |
| [`docs/cli-contract.md`](docs/cli-contract.md)             | Commands, options, JSON, states, and exits        |
| [`docs/security-model.md`](docs/security-model.md)         | Trust, path, concurrency, and recovery boundaries |
| [`AGENTS.md`](AGENTS.md)                                   | Generated project contract for repository agents  |
| [`CONTRIBUTING.md`](CONTRIBUTING.md)                       | Development, tests, and release boundaries        |

## License

MIT. See [`LICENSE`](LICENSE).

# Manifest schema version 1

Frozen public contract. A change to any rule below requires a new
`schema_version`, updated documentation, golden-fixture review, and a release
note. See `CONTRIBUTING.md`.

TOML is the manifest format because Python 3.14 parses it with `tomllib` from the
standard library, so the compiler needs no runtime dependency to read it.

## Shape

```toml
schema_version = 1
bundle_id = "example-project"
default_target = "../AGENTS.md"

[[modules]]
id = "core"
source = "modules/core.md"

[[modules]]
id = "python"
source = "modules/python.md"
```

## Top-level keys

| Key              | Type            | Required | Rule                                            |
|------------------|-----------------|----------|-------------------------------------------------|
| `schema_version` | integer         | yes      | Must be exactly the integer `1`                 |
| `bundle_id`      | string          | yes      | Must match `^[a-z][a-z0-9-]{0,63}$`             |
| `default_target` | string          | yes      | Non-empty; resolved from the manifest directory |
| `modules`        | array of tables | yes      | Non-empty                                       |

Any other top-level key is a rejection. There is no `optional` module flag, no
`[options]` table, and no place to disable a module: every listed module is
mandatory. Adding a key later requires a new schema version.

## Module keys

| Key      | Type   | Required | Rule                                      |
|----------|--------|----------|-------------------------------------------|
| `id`     | string | yes      | Must match `^[a-z][a-z0-9-]{0,63}$`       |
| `source` | string | yes      | Non-empty path; resolved as defined below |

Any other module key is a rejection.

## Identifier pattern

`bundle_id` and every module `id` must match:

```text
^[a-z][a-z0-9-]{0,63}$
```

Lowercase ASCII letter first, then lowercase ASCII letters, digits, and hyphens,
64 characters maximum. The pattern is deliberately narrower than "any string"
because identifiers are interpolated into generated ASCII markers in the rendered
output. Validating the identifier is what makes marker injection structurally
impossible; the renderer never escapes an identifier, it refuses an invalid one.

## Type strictness

Types are checked exactly, not coerced:

- `schema_version = "1"` is rejected: a string is not an integer.
- `schema_version = true` is rejected. TOML distinguishes booleans from integers,
  and the compiler additionally rejects `bool` explicitly because Python treats
  `True` as equal to `1`.
- `schema_version = 1.0` is rejected: a float is not an integer.
- `schema_version = 2` is rejected: only version 1 exists.
- `modules = []` is rejected: the array must be non-empty.
- `modules = ["core"]` is rejected: elements must be tables, not strings.
- An empty or whitespace-only `bundle_id`, `id`, `source`, or `default_target` is
  rejected.

## Path resolution

Path bases are part of the public contract. They never depend on where the
process happens to be running, except where the table below says so.

| Value                                | Resolved against                  |
|--------------------------------------|-----------------------------------|
| `--manifest PATH` (relative)         | Process current working directory |
| Manifest `source` (relative)         | The manifest's own directory      |
| Manifest `default_target` (relative) | The manifest's own directory      |
| `--lock PATH` (relative)             | Process current working directory |
| `--target PATH` (relative)           | Process current working directory |
| `--output PATH` (relative)           | Process current working directory |
| `--receipt PATH` (relative)          | Process current working directory |

Resolving manifest values against the manifest directory is what makes a manifest
portable: moving the process working directory cannot change which files a
reviewed manifest selects.

`default_target` is deliberately scope-neutral. It may select the active global
Codex file, a repository-root `AGENTS.md`, a nested `AGENTS.md`, or another
explicit output. The schema does not infer repository boundaries or Codex scope.
Codex visibility depends on the selected path and the startup working directory,
and is checked separately by `verify-codex`.

A leading `~` or `~user` is expanded through the standard home-directory lookup.
Nothing else is expanded:

- `$HOME/policy.md` is a literal relative path with a `$HOME` directory
  component, not an environment variable reference.
- `%APPDATA%\policy.md` is likewise literal.
- No shell substitution, command substitution, or glob expansion is performed.

An absolute `source` is used as written. An explicit `../` component is allowed in
version 1 and resolves deterministically, because it is visible in the reviewed
manifest and version 1 defines no configured source root to escape from. A future
schema that adds a source root will also add traversal rejection.

Both forms of a path are retained where the distinction affects safety. The
lexical path is what the operator wrote; the resolved path is what the filesystem
call used. Diagnostics and receipts record both.

## Source requirements

Every resolved source must be:

- an existing path;
- a regular file, not a directory, device, socket, or FIFO;
- not a symbolic link, at any component of its own final path element;
- readable;
- non-empty;
- valid UTF-8;
- LF-only, with no CR byte anywhere;
- terminated by exactly one final LF;
- free of any UTF-8 BOM;
- free of any NUL byte;
- free of the compiler marker prefix `<!-- agents-md-compiler:`;
- representable as UTF-8 in its own path name.

A symlinked source is rejected rather than followed, and the diagnostic reports
both the lexical and the resolved path so an operator can see what the link
pointed at. Following it would let a link change swap policy content without
changing the reviewed manifest.

## Uniqueness

All four of these are rejections:

- a duplicate module `id`;
- a duplicate resolved source path, even when reached through different lexical
  paths;
- duplicate source content, meaning two modules whose bytes have the same
  SHA-256;
- a source whose resolved path equals the resolved output target, which would
  make the compiler read its own output.

Duplicate content is rejected because it is either an accident or an attempt to
pad the bundle, and because it would make a byte range ambiguous in the rendered
output. A future schema may define a reviewed exception; version 1 does not.

## Size safeguards

Bounded to keep a hostile or accidental input from exhausting memory. Defaults are
generous, and a violation names the limit and the observed size rather than
truncating:

| Safeguard          | Default             | Library parameter                 |
|--------------------|---------------------|-----------------------------------|
| Per-source bytes   | 4,194,304 (4 MiB)   | `BundleLimits.max_source_bytes`   |
| Total bundle bytes | 33,554,432 (32 MiB) | `BundleLimits.max_bundle_bytes`   |
| Module count       | 256                 | `BundleLimits.max_modules`        |
| Manifest bytes     | 1,048,576 (1 MiB)   | `BundleLimits.max_manifest_bytes` |

These are library-configurable through `BundleLimits`, not command-line flags, so
the CLI surface stays as documented in `docs/cli-contract.md`. For reference, the
seven-module bundle this tool was designed for totals 151,686 bytes.

## Lock coupling

The default lock path is the manifest path with `.lock.json` appended. For
`global-agents.toml` that is `global-agents.toml.lock.json`.

The lock records the exact manifest bytes' SHA-256. Editing the manifest at all,
including a comment or whitespace, changes that digest and makes the lock stale.
That is deliberate: it keeps the reviewed manifest and the lock coupled, so a
reviewer cannot approve a manifest change that was never locked.

Manifest schema version 1 is compatible with lock and rendered formats 1 and 2.
Current releases emit lock and rendered format 2. A format-1 lock is migration
input: it is parsed strictly, reported as `LOCK_STALE`, and replaced only by an
explicit `lock` command.

## Machine-readable schema

`src/agents_md_compiler/schemas/manifest-v1.schema.json` ships in the wheel as
package data. It describes the decoded manifest structure, so an editor or an
external validator can check a manifest after TOML decoding.

The runtime does not use it. The compiler's own parser is the authority, because
JSON Schema cannot express the filesystem, byte-level, and uniqueness rules above,
and a second partial authority would be worse than none.

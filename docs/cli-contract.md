# CLI contract

Frozen public contract. Commands, options, states, exit codes, stream behavior,
and JSON field meanings are stable within a major version. New optional fields may
be added to a JSON envelope in a minor release; an existing field's meaning never
changes in a patch release.

## Invocation forms

Both are supported and equivalent:

```bash
agents-md-compiler COMMAND [OPTIONS]
python -m agents_md_compiler COMMAND [OPTIONS]
```

`--help` on the program or any subcommand exits 0. `version` and `--help` require
no manifest, no lock, and no filesystem access beyond installed metadata.

## Commands

| Command        | Mutation | Contract                                                               |
|----------------|----------|------------------------------------------------------------------------|
| `init`         | yes      | Scaffold an example manifest and modules; refuse every existing target |
| `lock`         | yes      | Validate sources and atomically write the deterministic lock           |
| `validate`     | no       | Validate manifest, lock, sources, and rendered structure               |
| `render`       | optional | Emit bytes to stdout by default; write only with explicit `--output`   |
| `check`        | no       | Compare a fresh locked render with the resolved target                 |
| `status`       | no       | Report source, lock, target, override, and receipt state               |
| `install`      | yes      | Back up and atomically install, only with `--apply`                    |
| `rollback`     | yes      | Restore one receipt, only with `--apply` and a matching target digest  |
| `verify-codex` | no       | Inspect Codex model-visible startup input for every module marker      |
| `version`      | no       | Print the installed distribution version                               |

## Options

Accepted by every subcommand, and also before the subcommand. A value given after
the subcommand wins.

| Option                | Default | Meaning                   |
|-----------------------|---------|---------------------------|
| `--format text\|json` | `text`  | Output format             |
| `--quiet`             | off     | Suppress non-error stderr |

Path options, per command:

| Option             | Commands                                                                   | Default                         |
|--------------------|----------------------------------------------------------------------------|---------------------------------|
| `--manifest PATH`  | `lock`, `validate`, `render`, `check`, `status`, `install`, `verify-codex` | `./global-agents.toml`          |
| `--lock PATH`      | `lock`, `validate`, `render`, `check`, `status`, `install`, `verify-codex` | manifest path plus `.lock.json` |
| `--target PATH`    | `validate`, `check`, `status`, `install`, `verify-codex`, `rollback`       | manifest `default_target`       |
| `--output PATH`    | `render`                                                                   | stdout                          |
| `--directory PATH` | `init`                                                                     | `.`                             |
| `--receipt PATH`   | `rollback`                                                                 | required, no default            |

Command-specific flags:

| Flag                           | Commands              | Meaning                                                                 |
|--------------------------------|-----------------------|-------------------------------------------------------------------------|
| `--check`                      | `lock`                | Read-only: exit nonzero if a fresh lock would differ                    |
| `--locked`                     | `render`              | Require the on-disk lock to equal a freshly serialized lock             |
| `--bundle-id IDENT`            | `init`                | Bundle identifier for the scaffolded manifest                           |
| `--apply`                      | `install`, `rollback` | Perform the mutation; without it the command is a dry run               |
| `--replace-unmanaged`          | `install`             | Permit replacing a target with no recognized generated header           |
| `--expect-target-sha256 HEX64` | `install`             | Required with `--replace-unmanaged`; digest captured before the dry run |
| `--timeout SECONDS`            | `verify-codex`        | Prompt-input deadline; capability checks use an independent 60 seconds  |
| `--version`                    | top level             | Alias for the `version` command                                         |

`validate` resolves a target but deliberately does not report the target's install
state. Whether the bundle is installed is what `check` and `status` answer;
`validate` answers whether the inputs and the output structure are sound. The
target is resolved only so the manifest can be refused when a source aliases the
output. Its reportable states are therefore `CURRENT`, `LOCK_MISSING`, and
`LOCK_STALE`, plus the invalid-input states, and its `target_sha256` is always
`null`.

`--apply` is mandatory for every mutation. Without it, `install` and `rollback`
compute and report the complete plan, create nothing, and exit with the code for
the state they report. A dry run never converts a safety refusal into success.

## States

| State                | Meaning                                                        |
|----------------------|----------------------------------------------------------------|
| `CURRENT`            | Locked sources render exactly to the active target             |
| `DRIFTED`            | Active target exists but differs from a fresh locked render    |
| `MISSING`            | Active target does not exist                                   |
| `SHADOWED`           | A non-empty global `AGENTS.override.md` would replace it       |
| `INVALID_MANIFEST`   | Manifest syntax or schema is invalid                           |
| `INVALID_LOCK`       | Lock syntax, schema, or internal structure is invalid          |
| `LOCK_MISSING`       | Required lock does not exist                                   |
| `LOCK_STALE`         | Manifest or source bytes differ from the lock                  |
| `INVALID_SOURCE`     | A source violates a path, type, encoding, or content invariant |
| `UNMANAGED_TARGET`   | Existing target lacks a recognized generated header            |
| `CONCURRENT_CHANGE`  | Target changed after the operation's precondition was captured |
| `RUNTIME_UNVERIFIED` | Static state is valid but Codex prompt inspection did not pass |

### Precedence

Report the first applicable state:

1. manifest or source invalidity;
2. lock invalidity, absence, or staleness;
3. shadowing;
4. target missing, unmanaged, drifted, or current;
5. runtime verification, reported separately.

`CURRENT` is never reported when runtime verification was explicitly requested and
did not complete.

## Exit codes

| Code | Meaning                                                                                         |
|------|-------------------------------------------------------------------------------------------------|
| 0    | Operation succeeded; read-only state is `CURRENT` when a target state applies                   |
| 1    | Invalid invocation, `INVALID_MANIFEST`, `INVALID_LOCK`, `INVALID_SOURCE`, runtime, or I/O error |
| 2    | Read-only difference: `LOCK_MISSING`, `LOCK_STALE`, `DRIFTED`, or `MISSING`                     |
| 3    | Safety refusal: `SHADOWED`, `UNMANAGED_TARGET`, or `CONCURRENT_CHANGE`                          |

`RUNTIME_UNVERIFIED` exits 1, because runtime verification was explicitly
requested and did not succeed. A successful mutation exits 0 only after its
postcondition passes.

State-to-code mapping, exhaustively:

| State                | Code |
|----------------------|------|
| `CURRENT`            | 0    |
| `INVALID_MANIFEST`   | 1    |
| `INVALID_LOCK`       | 1    |
| `INVALID_SOURCE`     | 1    |
| `RUNTIME_UNVERIFIED` | 1    |
| `LOCK_MISSING`       | 2    |
| `LOCK_STALE`         | 2    |
| `DRIFTED`            | 2    |
| `MISSING`            | 2    |
| `SHADOWED`           | 3    |
| `UNMANAGED_TARGET`   | 3    |
| `CONCURRENT_CHANGE`  | 3    |

A successful `install --apply` reports `CURRENT` and exits 0. A successful
`rollback --apply` reports the restored target's state and exits 0. `MISSING` after
a successful rollback of a newly created target is a success, not a difference, and
also exits 0; the `ok` field, not the state, distinguishes an accomplished mutation
from a read-only observation.

## Streams

- stdout carries requested output only: rendered bytes, the version string, or
  exactly one JSON object.
- stderr carries progress and diagnostics, including every error message.
- `--quiet` suppresses non-error stderr. It never suppresses JSON output,
  requested render output, or an error message.
- `render` with no `--output` writes only rendered bytes to stdout, with nothing
  prepended or appended, so the output can be piped or hashed directly.
- Canonical policy content never appears in an error, a status report, a receipt,
  a lock, or a JSON envelope. Only `render` to stdout emits policy bytes.

## JSON envelopes

`--format json` prints exactly one JSON object to stdout, keys sorted, followed by
one LF. Every envelope contains at least:

```json
{
  "command": "check",
  "ok": true,
  "schema_version": 1,
  "state": "CURRENT"
}
```

| Field            | Type           | Meaning                                                                  |
|------------------|----------------|--------------------------------------------------------------------------|
| `command`        | string         | The subcommand name as invoked                                           |
| `ok`             | boolean        | `true` when the command achieved its purpose                             |
| `schema_version` | integer        | Envelope version; currently `1`                                          |
| `state`          | string or null | The reported state token, or `null` when no target or lock state applies |

`state` is `null` for `version` and for `init`, which evaluate neither a lock nor a
target. It is a string for every other command. `ok` is `false` whenever the exit
code is nonzero.

An error envelope adds `error`:

```json
{
  "command": "check",
  "error": {
    "kind": "InvalidSourceError",
    "message": "source is a symbolic link",
    "paths": {"lexical": "modules/core.md", "resolved": "/elsewhere/core.md"}
  },
  "ok": false,
  "schema_version": 1,
  "state": "INVALID_SOURCE"
}
```

`error.message` never contains policy content. `error.paths` is present only when
the failure concerns a specific path, and `resolved` is present only when it
differs from `lexical`.

### Command-specific fields

`lock`:

| Field             | Type    | Meaning                                      |
|-------------------|---------|----------------------------------------------|
| `lock_path`       | string  | Resolved lock path                           |
| `lock_sha256`     | string  | Digest of the canonical lock bytes           |
| `manifest_sha256` | string  | Digest of the manifest bytes                 |
| `modules`         | array   | `{id, sha256, size_bytes}` in manifest order |
| `written`         | boolean | `true` when the lock file was replaced       |

`validate`, `render`, `check`, `status`:

| Field             | Type           | Meaning                                           |
|-------------------|----------------|---------------------------------------------------|
| `bundle_id`       | string         | From the manifest                                 |
| `manifest_path`   | string         | Resolved manifest path                            |
| `lock_path`       | string         | Resolved lock path                                |
| `manifest_sha256` | string         | Digest of the manifest bytes                      |
| `lock_sha256`     | string         | Digest of the canonical lock bytes                |
| `output_sha256`   | string         | Digest of the freshly rendered bytes              |
| `output_bytes`    | integer        | Length of the freshly rendered bytes              |
| `modules`         | array          | `{id, sha256, size_bytes}` in manifest order      |
| `target_path`     | string or null | Resolved target, `null` when a command takes none |
| `target_sha256`   | string or null | Digest of the existing target, `null` if absent   |

`status` adds:

| Field              | Type           | Meaning                                                              |
|--------------------|----------------|----------------------------------------------------------------------|
| `override_path`    | string or null | Sibling `AGENTS.override.md` when the target is a global `AGENTS.md` |
| `override_present` | boolean        | `true` when that override exists and is non-empty                    |
| `state_root`       | string         | Resolved per-bundle state directory                                  |
| `receipt_count`    | integer        | Receipts recorded for this bundle                                    |
| `latest_receipt`   | string or null | Resolved path of the newest receipt                                  |
| `backup_count`     | integer        | Backups recorded for this bundle                                     |

`install` adds:

| Field             | Type           | Meaning                                                  |
|-------------------|----------------|----------------------------------------------------------|
| `applied`         | boolean        | `false` for a dry run                                    |
| `previous_state`  | string         | `MISSING`, `MANAGED`, or `UNMANAGED`                     |
| `previous_sha256` | string or null | Digest of the target before the write                    |
| `backup_path`     | string or null | Backup written, `null` for a dry run or an absent target |
| `backup_sha256`   | string or null | Digest of the backup                                     |
| `receipt_path`    | string or null | Receipt written, `null` for a dry run                    |
| `target_mode`     | string         | Octal permission bits applied, for example `0600`        |

`rollback` adds:

| Field             | Type           | Meaning                                                      |
|-------------------|----------------|--------------------------------------------------------------|
| `applied`         | boolean        | `false` for a dry run                                        |
| `receipt_path`    | string         | Resolved receipt used                                        |
| `restored_sha256` | string or null | Digest after restoration, `null` when the target was removed |
| `preserved_path`  | string or null | Where a generated target was moved when no backup existed    |
| `receipt_written` | string or null | Rollback receipt path                                        |

`verify-codex` adds:

| Field                | Type           | Meaning                                                 |
|----------------------|----------------|---------------------------------------------------------|
| `codex_path`         | string or null | Resolved executable                                     |
| `codex_version`      | string or null | Captured `codex --version` output                       |
| `capability_present` | boolean        | `debug prompt-input` is exposed                         |
| `markers_found`      | integer        | Module markers located in the prompt input              |
| `markers_expected`   | integer        | Module markers required                                 |
| `sentinels_found`    | integer        | First and last module content sentinels located         |
| `probe_command`      | array          | Exact argument vector used, for reproduction            |
| `failure`            | string or null | Observed failure when the state is `RUNTIME_UNVERIFIED` |

`init` adds:

| Field       | Type   | Meaning                          |
|-------------|--------|----------------------------------|
| `directory` | string | Resolved scaffold directory      |
| `created`   | array  | Resolved paths created, in order |

`version` adds:

| Field     | Type   | Meaning                               |
|-----------|--------|---------------------------------------|
| `version` | string | Installed version, or `0.0.0+unknown` |

## Version reporting

The version comes from `importlib.metadata.version("agents-md-compiler")`. A source
checkout with no installed distribution reports `0.0.0+unknown`. That value is
documented rather than fatal, because reading the version must never require an
install, and because the version deliberately never enters rendered output.

## Mutation preconditions

`render --output PATH` writes only to a path that does not exist. Every existing
path is refused, with no backup and no replacement, because `install` is the
command that owns replacement, backup, and rollback semantics.

`lock` may replace only the lock path resolved for that invocation. It acquires an
advisory lock, retains the pre-operation file state and digest, rechecks them
before replacement, refuses a concurrent change with `CONCURRENT_CHANGE`, and
writes atomically.

`install --apply` requires, in order:

1. a valid manifest, valid sources, and a lock that matches both;
2. no non-empty sibling `AGENTS.override.md` when the target is a global
   `AGENTS.md`;
3. a target that is absent, or managed, or explicitly adopted through both
   `--replace-unmanaged` and a matching `--expect-target-sha256`;
4. a target that is not a symbolic link;
5. an advisory lock on the target, after which identity and digest are rechecked;
6. a backup and a receipt recorded before success is reported.

`rollback --apply` requires a regular non-symlink receipt under the expected bundle
state root, a schema-valid receipt whose target and backup paths match the current
invocation and that state root, and a target whose current digest equals the
receipt's installed digest.

## Compatibility commitments

- Exit codes and state tokens are stable within a major version.
- A JSON field's meaning never changes in a patch release. New optional fields may
  appear in a minor release, so consumers must ignore unknown fields.
- Rendered output bytes change only through a new format version.
- Removing a command, an option, a state token, or a JSON field is a breaking
  change, announced with a deprecation first.

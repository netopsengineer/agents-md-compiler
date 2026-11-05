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
| `verify-codex` | no       | Verify one complete selected bundle in supported Codex startup input   |
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
| `--manifest PATH`  | `lock`, `validate`, `render`, `check`, `status`, `install`, `verify-codex` | discovery described below       |
| `--lock PATH`      | `lock`, `validate`, `render`, `check`, `status`, `install`, `verify-codex` | manifest path plus `.lock.json` |
| `--target PATH`    | `validate`, `check`, `status`, `install`, `verify-codex`, `rollback`       | manifest `default_target`       |
| `--output PATH`    | `render`                                                                   | stdout                          |
| `--directory PATH` | `init`                                                                     | `.`                             |
| `--receipt PATH`   | `rollback`                                                                 | required, no default            |

When `--manifest` is omitted, resolve `./agents-md.toml` first. If only the
legacy `./global-agents.toml` exists, use it. If both exist, refuse the ambiguous
invocation and require explicit `--manifest`. If neither exists, select
`./agents-md.toml` so the missing-path diagnostic names the current default.

Command-specific flags:

| Flag                           | Commands              | Meaning                                                                 |
|--------------------------------|-----------------------|-------------------------------------------------------------------------|
| `--check`                      | `lock`                | Read-only: exit nonzero if a fresh lock would differ                    |
| `--locked`                     | `render`              | Require the on-disk lock to equal a freshly serialized lock             |
| `--bundle-id IDENT`            | `init`                | Bundle identifier for the scaffolded manifest                           |
| `--target PATH`                | `init`                | Target written to the scaffold manifest                                 |
| `--apply`                      | `install`, `rollback` | Perform the mutation; without it the command is a dry run               |
| `--replace-unmanaged`          | `install`             | Permit replacing a target with no recognized generated header           |
| `--expect-target-sha256 HEX64` | `install`             | Required with `--replace-unmanaged`; digest captured before the dry run |
| `--timeout SECONDS`            | `verify-codex`        | Prompt-input deadline; capability checks use an independent 60 seconds  |
| `--cwd PATH`                   | `verify-codex`        | Codex startup directory for project or nested visibility                |
| `--version`                    | top level             | Alias for the `version` command                                         |

`validate` resolves a target but deliberately does not report the target's install
state. Whether the bundle is installed is what `check` and `status` answer;
`validate` answers whether the inputs and the output structure are sound. The
target is resolved only so the manifest can be refused when a source aliases the
output. Its reportable states are therefore `CURRENT`, `LOCK_MISSING`, and
`LOCK_STALE`, plus the invalid-input states, and its `target_sha256` is always
`null`.

`init` writes `agents-md.toml`. Its default target is the active global
`~/.codex/AGENTS.md`; an explicit relative `--target` is resolved from the
invocation working directory and serialized relative to the scaffold directory
when possible. The command prints the exact manifest-qualified `lock` command to
run next.

`--apply` is mandatory for every target mutation. Without it, `install` and `rollback`
compute and report the complete plan, create nothing, and exit with the code for
the state they report. A dry run never converts a safety refusal into success.

## States

| State                | Meaning                                                        |
|----------------------|----------------------------------------------------------------|
| `CURRENT`            | Locked sources render exactly to the selected target           |
| `DRIFTED`            | Selected managed target differs from a fresh locked render     |
| `MISSING`            | Selected target does not exist                                 |
| `SHADOWED`           | A non-empty sibling `AGENTS.override.md` would replace it      |
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

`CURRENT` is a byte-state claim about the selected target. It does not imply that
the target is in the Codex discovery chain for an arbitrary working directory.
Only a successful `verify-codex` invocation makes that visibility claim for its
reported probe directory.

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
  a state token in text mode, or exactly one JSON object.
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

| Field              | Type           | Meaning                                                      |
|--------------------|----------------|--------------------------------------------------------------|
| `override_path`    | string or null | Sibling override when the selected file is named `AGENTS.md` |
| `override_present` | boolean        | `true` when that override exists and is non-empty            |
| `state_root`       | string         | Target-qualified deployment state directory                  |
| `receipt_count`    | integer        | Receipts recorded for this bundle and target                 |
| `latest_receipt`   | string or null | Resolved path of the newest receipt                          |
| `backup_count`     | integer        | Backups recorded for this bundle                             |

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

| Field                         | Type            | Meaning                                                                             |
|-------------------------------|-----------------|-------------------------------------------------------------------------------------|
| `codex_path`                  | string or null  | Resolved executable                                                                 |
| `codex_version`               | string or null  | Captured `codex --version` output                                                   |
| `capability_present`          | boolean         | `debug prompt-input` is exposed                                                     |
| `markers_found`               | integer         | Expected marker lines found within the selected occurrence                          |
| `markers_expected`            | integer         | Generated header marker plus expected module boundaries                             |
| `sentinels_expected`          | integer         | Available unique first/last-module content sentinels                                |
| `sentinels_found`             | integer         | Available sentinels found within the selected occurrence                            |
| `probe_command`               | array           | Exact argument vector, except contaminated values are redacted as described below   |
| `probe_cwd`                   | string or null  | Startup directory, except a contaminated value is redacted as described below       |
| `verification_context`        | string          | `global` or `project`                                                               |
| `failure`                     | string or null  | Observed failure when the state is `RUNTIME_UNVERIFIED`                             |
| `verification_method`         | string or null  | `complete_bundle_bytes` when the complete verifier is used                          |
| `content_verified`            | boolean         | True only after complete selected-bundle verification                               |
| `selected_bundle_occurrences` | integer or null | All observed selected-ID candidates; null when extraction did not establish a count |
| `modules_expected`            | integer         | Modules in the compiled selection                                                   |
| `modules_verified`            | integer         | Selected modules actually verified, never inferred from expected count              |
| `overlap_scan_complete`       | boolean         | All relevant foreign candidates could be inspected                                  |
| `diagnostics`                 | array           | Ordered structured findings described below                                         |

The complete-content fields are additive under the minor-release compatibility
policy; `schema_version` remains 1. Marker and sentinel counters are retained as
legacy diagnostics, not success predicates. A short or repetitive module can pass
without a usable sentinel. Failures retain executable/version, command, startup
context, and partial verification information already observed; unavailable
evidence is not replaced by an inferred success count.

`PROBE_CONTAMINATED` is the sole exception to exact probe-command and
startup-directory reporting. If an expected marker or sentinel occurs in a
command argument or startup directory, the affected reported value is the literal
`[REDACTED]`. Other captured command, path, and version facts are retained. The
failure description never echoes the matched marker or sentinel prose.

Each runtime diagnostic has this shape. Identity and location fields are null
when the finding does not establish them.

| Field             | Type           | Meaning                                                  |
|-------------------|----------------|----------------------------------------------------------|
| `code`            | string         | Stable finding category                                  |
| `severity`        | string         | `error`, `warning`, or `info`                            |
| `message`         | string         | Safe explanation without policy bodies or sentinel prose |
| `bundle_id`       | string or null | Primary bundle identity                                  |
| `module_id`       | string or null | Primary module identity                                  |
| `sha256`          | string or null | Observed primary body digest when established            |
| `other_bundle_id` | string or null | Related bundle identity                                  |
| `other_module_id` | string or null | Related module identity                                  |
| `other_sha256`    | string or null | Observed related body digest when established            |
| `location`        | object or null | Primary evidence location                                |
| `other_location`  | object or null | Related evidence location                                |

An evidence location contains `json_path` (for example,
`$[0].content[0].text`), `role`, `byte_start`, and `byte_end`. Offsets are
zero-based UTF-8 byte offsets in the original decoded text field, including any
producer wrapper: the start is inclusive and the end exclusive. They are not
character offsets or positions in the serialized JSON.

| Code                           | Severity | Meaning                                                                            |
|--------------------------------|----------|------------------------------------------------------------------------------------|
| `UNSUPPORTED_EVIDENCE`         | error    | Unsupported envelope, malformed relevant field, or unusable instruction encoding   |
| `SELECTED_BUNDLE_MISSING`      | error    | No selected bundle identity was established in accepted instruction evidence       |
| `DUPLICATE_SELECTED_BUNDLE`    | error    | Multiple exact copies of the selected bundle                                       |
| `AMBIGUOUS_IDENTITY`           | error    | Conflicting same-ID candidates or ambiguous selected identity/boundaries           |
| `MALFORMED_BOUNDARIES`         | error    | Invalid, missing, duplicated, nested, or unmatched selected structure              |
| `MODULE_ORDER`                 | error    | Selected modules do not appear in compiled order                                   |
| `CONTENT_MISMATCH`             | error    | Selected header identity or observed complete content differs from the compilation |
| `MALFORMED_FOREIGN_BUNDLE`     | warning  | A foreign candidate cannot establish overlap; inspection is incomplete             |
| `CROSS_BUNDLE_CONTENT_OVERLAP` | warning  | Complete module bodies are byte-identical across distinct bundle IDs               |
| `MODULE_ID_REUSE`              | info     | Distinct bundles reuse a module ID with different complete bodies                  |
| `OVERLAP_SCAN_LIMIT`           | warning  | The 1,000 pairwise-finding limit stopped overlap inspection                        |

Existing capability and probe failure categories remain available. Text mode
prints the state on stdout and findings on stderr. `--quiet` suppresses warnings
and informational findings, never errors. JSON mode includes diagnostics in the
normal single-object envelope, including when `--quiet` is set.

`init` adds:

| Field          | Type   | Meaning                          |
|----------------|--------|----------------------------------|
| `directory`    | string | Resolved scaffold directory      |
| `created`      | array  | Resolved paths created, in order |
| `target_path`  | string | Resolved scaffold target         |
| `next_command` | array  | Exact next lock argument vector  |

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

`lock` may replace only the lock path resolved for that invocation. It acquires a
distribution-wide advisory lock keyed only by the resolved lock path, retains the
pre-operation file state and digest, rechecks them before replacement, refuses a
concurrent change with `CONCURRENT_CHANGE`, and writes atomically.

`install --apply` requires, in order:

1. a valid manifest, valid sources, and a lock that matches both;
2. no non-empty sibling `AGENTS.override.md` when the selected target is named
   `AGENTS.md`;
3. a target that is absent, or managed, or explicitly adopted through both
   `--replace-unmanaged` and a matching `--expect-target-sha256`;
4. a target that is not a symbolic link;
5. a distribution-wide advisory lock keyed only by the resolved target, after
   which identity and digest are rechecked;
6. a backup and receipt below the bundle-and-target deployment root before
   success is reported.

`rollback --apply` requires a regular non-symlink receipt under either the current
bundle-and-target deployment root or the legacy bundle-only root, a schema-valid
receipt whose target and backup paths match the current invocation and the root
that contains the receipt, and a target whose current digest equals the receipt's
installed digest. New evidence is always written to the current deployment root.

`verify-codex` first requires static `CURRENT`. If the selected target resolves to
the active Codex home `AGENTS.md` or `AGENTS.override.md`, it uses an empty
disposable startup directory and reports context `global`. Otherwise it reports
context `project` and uses explicit `--cwd` or the target parent. The selected
project directory must exist and be a directory. A successful result proves only
that the rendered bytes appeared in supported Codex startup instruction evidence
for that directory. It does not prove model compliance.

## Complete runtime verification

The supported adapter accepts a root JSON array of response items. It examines
`type="message"` entries and typed `content` elements with
`type="input_text"` and string `text`. System/developer content and recognized
AGENTS instruction wrappers in user messages are eligible. Arbitrary user text,
assistant/tool output, metadata, and dictionary keys are not instruction evidence.
Non-message response items, `output_text`, and supported image/audio content are
ignored. Unsupported root envelopes and malformed relevant message/content fields
report `RUNTIME_UNVERIFIED`; no recursive fallback is allowed.
The inspected producer revision and synthetic-fixture provenance are recorded in
[`tests/fixtures/README.md`](../tests/fixtures/README.md#codex).

Every accepted field is separate. JSON decoding followed by strict UTF-8 encoding
is the only permitted conversion. The user wrapper is recognized without trimming
or normalizing its enclosed instruction text. Whitespace, line endings, Unicode,
final newlines, and Markdown remain byte-significant. Fragments split across
fields cannot be combined into a successful verification.

Generated headers are located at line boundaries and candidates are selected by
bundle ID. Every selected-ID candidate is counted before a success is chosen.
Exactly one structurally valid candidate must match the compiled format,
manifest digest, lock digest, module order, observed body lengths and digests,
separators, and complete rendered span. Module occurrences must be unique and
boundaries matched. Two exact copies fail as `DUPLICATE_SELECTED_BUNDLE`; a
current copy plus a stale or altered same-ID candidate fails as
`AMBIGUOUS_IDENTITY`. A good copy cannot hide another selected-ID candidate.

Host prose may surround a complete bundle. Compiler markers are attributed
through the containing field or next bundle header, so extra markers after an
intact prefix fail even after intervening prose. Relocated content cannot repair
missing or altered content within a module's boundaries.

Unrelated valid bundles do not change selected integrity. Complete module bodies
are indexed by observed digest and compared byte-for-byte across distinct bundle
IDs, even when their module IDs differ. Identical content produces
`CROSS_BUNDLE_CONTENT_OVERLAP` with both identities, digest, and evidence
locations. The policy is **warning, still `CURRENT`**, with exit code 0 when the
selected bundle verifies. Equal IDs with different bodies produce informational
`MODULE_ID_REUSE`, not overlap or selected-bundle duplication. There is no fuzzy
similarity, paragraph deduplication, rewriting, or configurable overlap-failure
mode.

To bound pairwise diagnostic output from hostile evidence, the overlap scan stops
at 1,000 overlap/ID-reuse findings, adds an `OVERLAP_SCAN_LIMIT` warning, and sets
`overlap_scan_complete` to false. Findings already established are retained.
Selected-bundle integrity remains independent, so this warning does not change an
otherwise verified `CURRENT` result. The limit is not a configurable
overlap-failure policy.

A malformed foreign candidate produces `MALFORMED_FOREIGN_BUNDLE` and sets
`overlap_scan_complete` to false; it never establishes overlap. Selected
verification fails only when that malformed structure makes selected identity or
boundaries ambiguous. Complete selected verification and complete overlap
inspection are therefore separate result claims.

## Format migration

- The compiler emits lock and rendered format 2.
- A strict format-1 lock is accepted as migration input but is always
  `LOCK_STALE`; only explicit `lock` replaces it.
- Exact rendered formats 1 and 2 are managed targets. A format-1 target is
  `DRIFTED` and upgrades through ordinary explicit install.
- Unknown rendered formats remain unmanaged and require explicit adoption.
- Existing `global-agents.toml` workflows remain available through explicit paths
  and the unambiguous legacy default fallback.

## Compatibility commitments

- Exit codes and state tokens are stable within a major version.
- A JSON field's meaning never changes in a patch release. New optional fields may
  appear in a minor release, so consumers must ignore unknown fields.
- Rendered output bytes change only through a new format version.
- Removing a command, an option, a state token, or a JSON field is a breaking
  change, announced with a deprecation first.

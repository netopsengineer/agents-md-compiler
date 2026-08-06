# AGENTS.md

Project contract for `agents-md-compiler`. Agent-facing. Human explanation lives
in `README.md` and `CONTRIBUTING.md`; do not move operator prose into this file.

## Purpose and boundaries

Compile an ordered set of local Markdown policy modules into one deterministic,
self-contained global `AGENTS.md`, then check, install, roll back, and verify it.

In scope:

- strict manifest parsing, deterministic path resolution, source validation;
- explicit lock refresh and locked verification;
- format-version-1 rendering that preserves accepted source bytes exactly;
- read-only state comparison against a target;
- explicit, atomic, backed-up, concurrency-safe installation and rollback;
- inspection of Codex model-visible startup input.

Out of scope, and never to be added without a new reviewed contract:

- templating, macros, variable substitution, conditional rendering;
- recursive `@include` processing or any reliance on `@path` expansion;
- remote HTTP, Git, or registry sources; glob-based source discovery;
- repository stack detection; nested or project `AGENTS.md` generation;
- LLM authoring, rewriting, summarizing, or deduplicating policy;
- implicit installation during `render`, `check`, `status`, or `verify-codex`;
- automatic deletion or rotation of backups;
- runtime network access or shell invocation.

Never edit `~/.codex/AGENTS.md`, `~/.codex/config.toml`, or a canonical policy
source as a side effect of work on this package.

## Architecture

Pure computation is separated from filesystem mutation:

```text
parse -> validate -> resolve -> lock-check -> render -> compare
                                                         |
                                          explicit mutation boundary only
```

| Module            | Responsibility                                                    |
|-------------------|-------------------------------------------------------------------|
| `models.py`       | Immutable slotted dataclasses and the `BundleState` token set     |
| `errors.py`       | Public exception taxonomy; every error carries a state token      |
| `paths.py`        | Path-base resolution, tilde expansion, user state root            |
| `hashing.py`      | SHA-256 over bytes and streams                                    |
| `manifest.py`     | Strict TOML parsing with unknown-key rejection                    |
| `sources.py`      | Source reading, byte invariants, re-stat verification             |
| `lockfile.py`     | Deterministic lock generation, parsing, and comparison            |
| `rendering.py`    | Format-version-1 rendering and output self-validation             |
| `state.py`        | Read-only state computation and precedence                        |
| `locking.py`      | Cross-platform advisory file locking                              |
| `atomic.py`       | Same-directory temporary write, fsync, atomic replace             |
| `receipts.py`     | Install and rollback receipt schema, read, write, validation      |
| `installation.py` | Mutating install and rollback flows behind explicit preconditions |
| `codex.py`        | Codex capability detection and prompt-input verification          |
| `cli.py`          | Argument parsing, output formatting, exit-code mapping            |

The parser and renderer never write files. The installer accepts already
validated rendered bytes plus an explicit target precondition.

## Public contracts

Four documents are frozen contracts. Change them only through the documented
format-version procedure:

- `docs/manifest-v1.md`
- `docs/rendered-format-v1.md`
- `docs/cli-contract.md`
- `docs/security-model.md`

The typed Python API is `BundleManifest`, `BundleLock`, `BundleState`,
`RenderedBundle`, `compile_bundle`, `load_manifest`, and `load_lock` from
`agents_md_compiler`, plus `install_bundle` and `rollback_install` from
`agents_md_compiler.installation`. Do not return untyped dictionaries from the
library API; the CLI serializes typed results.

JSON schemas in `src/agents_md_compiler/schemas/` ship as package data. They
document the manifest, lock, and receipt shapes for editors and external
validators. The runtime does not use them to validate input.

## Development commands

```bash
uv sync --locked
uv run pytest
uv run pyright
uv run ruff check .
uv run ruff format --check .
uv run pydoclint src/agents_md_compiler
uv run bandit -c pyproject.toml -r src
uv run prek run --all-files --show-diff-on-failure --color always
```

`uv run prek run --all-files` is the aggregate local gate and mirrors the CI
`gate` job exactly. Both invocation forms must work:

```bash
uv run agents-md-compiler --help
uv run python -m agents_md_compiler --help
```

For Markdown changes, run the table formatter, the fixing linter, the
non-fixing linter, and a final Unicode scan on each edited path.

## Invariants

Determinism:

- identical manifest bytes, lock bytes, format version, and source bytes produce
  identical output bytes;
- generated output contains no timestamp, host name, process ID, random value,
  temporary path, package version, or working directory;
- manifest order is output order; rendering never consults directory enumeration
  order;
- a second render with unchanged inputs is byte-identical.

Canonical preservation:

- read every source as strict UTF-8 bytes;
- require LF line endings and exactly one final LF;
- reject UTF-8 BOM, NUL, CRLF, and invalid UTF-8;
- preserve every accepted byte exactly between generated markers;
- never trim, reflow, lint-fix, normalize, or rewrite source prose.

Safe mutation:

- `validate`, `render` to stdout, `check`, `status`, and `verify-codex` are
  read-only;
- `render --output` refuses every existing path;
- `install` requires `--apply`; replacing an unmanaged target additionally
  requires `--replace-unmanaged` and `--expect-target-sha256`;
- never follow a target symlink;
- acquire a per-target advisory lock, then recheck target identity and digest;
- write a same-directory temporary file, flush, `fsync`, atomically replace,
  and `fsync` the directory where supported;
- record an exact backup and receipt before reporting success.

## Coverage requirement

100 percent line and branch coverage for the import package, enforced by
`fail_under = 100`. Never lower the floor, never `xfail` a failing test, and
never add a coverage pragma to hide testable code. A new `pragma: no cover`
requires a comment naming the platform or runtime condition that makes the branch
unreachable under test, and a reviewer decision recorded in the pull request.

## Fixtures and golden files

- Valid and invalid fixtures live in `tests/fixtures/`. Every documented
  rejection branch has at least one fixture that triggers it.
- Golden rendered output and golden lock files encode format version 1. A diff in
  a golden file is a format change, never an incidental test update.
- Never regenerate a golden file to make a test pass. A rendered format change
  requires all of: a new format version or a proven backward-compatible
  correction, updated format documentation, explicit golden diff review,
  migration and rollback analysis, a release note, and full integration plus
  active prompt verification.
- Fixtures that must contain CRLF, BOM, NUL, or invalid UTF-8 are written by test
  code as bytes, not committed as text, so repository hygiene hooks cannot
  silently repair them.

## External version verification

Never write a dependency version, hook revision, or action SHA from memory.

- Re-resolve every version live before changing dependency or workflow
  configuration.
- Check both `/releases/latest` and `/tags` for every upstream repository; they
  diverge.
- Pin every GitHub Action and hook repository to an immutable commit SHA with a
  comment naming the verified tag. Dereference annotated tags to the commit.
- Run an advisory scan for every exact selected version and record the result.
- Record every finding in `docs/dependency-verification.md` with source URLs and
  the verification date. Workflow and hook pin evidence, effective job
  permissions, and the reason each conditional gate is blocking or advisory live
  in `docs/ci-evidence.md`.
- `scripts/check_pins.py` runs in the aggregate gate and fails the build when any
  workflow `uses:` or third-party hook `rev:` is not an immutable commit SHA
  carrying its tag. Do not satisfy it by adding a comment to a mutable tag;
  re-resolve the SHA.

## Packaging invariants

- `src/` layout, `uv_build` backend, static `project.version`, committed
  `uv.lock`, no runtime dependencies.
- The wheel contains only the import package, `py.typed`, the JSON schemas, the
  license, and backend metadata.
- Neither distribution contains tests, docs, examples, the execution plan, the
  session goal file, a personal manifest, an absolute personal path, canonical
  policy content, a backup, or a receipt.
- Build with `uv build --no-sources`, then validate metadata with `twine check`,
  inspect both archive listings, build a wheel from the sdist and compare
  normalized contents, and install the wheel into a clean Python 3.14
  environment before trusting it.

## Release gates

Ordered, and no gate may be skipped:

1. local aggregate gate green from a clean checkout;
2. CI required checks green on the exact commit;
3. artifact gates green, including installed-wheel smoke tests;
4. `SEMANTIC_RELEASE_ENABLED` false or absent until `v0.1.0` exists;
5. version `0.1.0` published only through the `workflow_dispatch` bootstrap path,
   with operator approval on the protected `pypi` environment after the exact
   downloaded artifact passes local gates;
6. no checkout, dependency install, or rebuild in the publish job;
7. OIDC Trusted Publishing with PEP 740 attestations and no long-lived token;
8. tag and GitHub release created only after publication succeeds, idempotently;
9. never move or replace an existing tag.

## Failure and recovery

| Failure                                    | Required action                                                                                                                                            |
|--------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Any required check fails or is unavailable | Report it explicitly; the state is not reached. Do not weaken the check.                                                                                   |
| Source drift during an operation           | Abort with `INVALID_SOURCE`; emit no partial output                                                                                                        |
| Lock disagrees with manifest or sources    | Exit 2 with `LOCK_STALE`; refresh the lock through `lock`, never by hand                                                                                   |
| Target changed after precondition capture  | Exit 3 with `CONCURRENT_CHANGE`; never overwrite                                                                                                           |
| Non-empty global override present          | Exit 3 with `SHADOWED`; resolve the override explicitly                                                                                                    |
| Post-install gate failure                  | Preserve evidence, confirm the target still matches the receipt, run receipt-based rollback, verify the restored digest, stop in `ROLLED_BACK`             |
| Codex debug command missing or changed     | Return `RUNTIME_UNVERIFIED` with the exact command and observed failure; never invent a log parser and never ask a model to summarize its own instructions |
| Golden file mismatch                       | Treat as a format change and follow the format-version procedure                                                                                           |

Rollback restores exact prior bytes only when the target still matches the
receipt's installed digest. Backups are never deleted automatically.

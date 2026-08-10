# Target-Agnostic AGENTS.md Execution Plan

## Contract

Execute this plan to generalize `agents-md-compiler` from one global target to
one explicitly selected global, repository, or nested `AGENTS.md` target per
manifest. Treat this file as the canonical execution contract for the change.

Apply repository `AGENTS.md`, the Modern Python Engineering Standard revision
recorded in `.modern-python-standards.toml`, and the frozen public contracts.
Change a frozen contract only through the migration steps below. Preserve
unrelated worktree changes. Do not edit a global Codex file or canonical external
policy source.

Complete phases in order. Mark a phase complete only after its acceptance checks
pass. On a required failure, preserve evidence, restore the last verified state
when mutation occurred, and stop at the failed gate.

## Goal

Deliver a deterministic, typed, standard-library-only compiler that safely
maintains any explicitly selected `AGENTS.md` target and provides the same lock,
check, install, backup, rollback, and runtime-visibility guarantees for global,
repository, and nested deployments.

Dogfood the result in this repository:

- store the canonical project contract outside the generated target;
- compile the canonical source into the root `AGENTS.md`;
- commit the project manifest and portable lock;
- prove the generated target is byte-current;
- prove Codex sees the generated target from the repository root.

## Accepted decisions

1. Keep one manifest, one ordered module set, and one persistent default target.
2. Keep manifest schema version 1. `default_target` already represents an
   arbitrary target and `--target` remains an explicit invocation override.
3. Do not add a manifest `scope` field. Target location and verification working
   directory determine Codex scope.
4. Do not discover repositories, stacks, manifests, modules, or targets by glob.
5. Do not generate multiple targets from one manifest.
6. Keep source prose byte-exact. Do not rewrite, normalize, lint-fix, summarize,
   or deduplicate canonical modules.
7. Introduce lock format version 2. Store the manifest-selected lexical source
   path, digest, and size. Do not store an expanded absolute source path in
   deterministic lock bytes.
8. Introduce rendered format version 2 with the neutral title
   `# Agent Instructions`.
9. Recognize rendered formats 1 and 2 as compiler-managed. Emit only format 2.
   Treat unknown future formats as unmanaged.
10. Parse lock format 1 only for migration. Report it as stale and require the
    explicit `lock` command to produce format 2. Never rewrite it during a
    read-only command.
11. Place advisory locks in one distribution-wide lock directory keyed only by
    the resolved protected path. Bundle identity must not affect coordination.
12. Store new receipts, backups, preserved files, and last-install state under a
    deployment directory keyed by bundle ID and resolved target digest.
13. Keep legacy bundle-only receipt roots readable for explicit rollback. Do not
    move, delete, or rewrite legacy evidence automatically.
14. Define `CURRENT` as selected-target byte equality. Use `verify-codex` to prove
    visibility for a concrete Codex startup working directory.
15. For an active global target, keep the isolated empty-directory runtime probe.
    For a project target, default the probe working directory to the target parent
    and allow `--cwd` to select a descendant startup directory explicitly.
16. Keep sibling override detection for every selected file named `AGENTS.md`.
    A non-empty `AGENTS.override.md` in the same directory is `SHADOWED`.
17. Use `agents-md.toml` as the new neutral default manifest name. When no
    `--manifest` is supplied, use it first, fall back to legacy
    `global-agents.toml`, and refuse ambiguity when both exist.
18. Add `init --target PATH`. Resolve a relative CLI target from the invocation
    working directory and serialize a portable manifest-relative target.
19. Keep installation and rollback explicit. No read-only command may mutate a
    target, lock, receipt, backup, or state directory.
20. Preserve exit-code meanings and existing state-token meanings. Add JSON fields
    only when their meaning is new and additive.

## Compatibility matrix

| Existing artifact       | New behavior                                            |
|-------------------------|---------------------------------------------------------|
| Manifest schema 1       | Accepted unchanged                                      |
| Lock format 1           | Parsed, reported `LOCK_STALE`, refreshed only by `lock` |
| Lock format 2           | Canonical writable and verifiable format                |
| Rendered format 1       | Managed legacy target, normally `DRIFTED`               |
| Rendered format 2       | Current emitted format                                  |
| Unknown rendered format | `UNMANAGED_TARGET`; explicit adoption required          |
| Legacy bundle receipt   | Explicit rollback remains available                     |
| New deployment receipt  | Written and loaded from target-qualified state          |
| `global-agents.toml`    | Legacy implicit fallback when neutral default is absent |
| `agents-md.toml`        | New implicit default                                    |

## Architecture

```text
manifest + source bytes
        |
        v
portable lock v2 -> deterministic render v2 -> selected target comparison
                                                |
                              explicit mutation boundary
                                                |
                    shared target lock + deployment evidence
                                                |
                           target-aware Codex visibility probe
```

Use existing modules and typed dataclasses. Add the smallest new value or helper
needed to make deployment identity, lock migration, managed format recognition,
and verification context explicit. Do not add a framework, registry, service
container, or runtime dependency.

## Phase 1: Baseline and contracts

Status: `COMPLETE`

1. Record a clean working-tree baseline and run `uv run pytest`.
2. Reconfirm official Codex global and project discovery behavior.
3. Update repository `AGENTS.md` scope, architecture, invariants, failure actions,
   fixtures, packaging rules, and dogfood rules.
4. Add format-2 lock and rendered-format contract documents. Keep the version-1
   documents as immutable history.
5. Update the CLI and security contracts with target-independent terminology,
   migration behavior, shared locking, deployment state, and project verification.

Acceptance:

- public changes have an explicit migration and rollback path;
- no Python file has changed before the contract changes are reviewable;
- all changed Markdown passes repository Markdown checks and Unicode validation.

## Phase 2: Shared locks and deployment state

Status: `COMPLETE`

1. Add a distribution-wide advisory-lock root below the user state root.
2. Make lock refresh, install, and rollback derive lock paths from only the
   resolved protected path and the shared lock root.
3. Add a deployment state directory derived from validated bundle ID and target
   path digest.
4. Write new operational evidence only below that deployment directory.
5. Filter status counts to the selected deployment.
6. Accept explicit legacy receipts from the legacy bundle state root while
   applying the same schema, bundle, target, backup containment, and digest checks.

Acceptance:

- two bundle IDs targeting one path derive the same advisory lock;
- two targets using one bundle ID have separate operational history;
- dry runs create no shared lock or deployment directory;
- legacy receipts remain rollback-capable and cannot escape their legacy root.

## Phase 3: Portable lock format 2

Status: `COMPLETE`

1. Add lock format 2 models, parser, serializer, schema, and comparison rules.
2. Replace `resolved_source` in canonical lock bytes with the exact lexical source
   value selected by the manifest.
3. Retain resolved paths in runtime snapshots and diagnostics only.
4. Parse version 1 strictly enough to classify it as stale migration input.
5. Make `lock` replace a current version-1 lock only after the existing
   concurrency precondition passes.
6. Prove equal manifest and source bytes in different checkout roots produce
   identical version-2 lock and rendered bytes.

Acceptance:

- lock version 2 is byte-deterministic and path-portable;
- every documented invalid version-2 branch has a fixture or direct test;
- version-1 input is never treated as current or rewritten implicitly;
- JSON schemas accept all valid fixtures and reject all invalid fixtures.

## Phase 4: Neutral rendered format 2

Status: `COMPLETE`

1. Emit `# Agent Instructions` and `format=2`.
2. Self-validate version-2 bytes structurally.
3. Recognize exact version-1 and version-2 header positions as managed.
4. Treat a version-1 installed target as managed drift that can be upgraded by a
   normal explicit install without unmanaged-adoption flags.
5. Add golden format-2 output with an explicit reviewed diff from format 1.
6. Test upgrade, backup, rollback, downgrade refusal, and unknown-future-format
   handling.

Acceptance:

- accepted source bytes remain exact inside module markers;
- a version-1 target upgrades through ordinary install and rolls back exactly;
- a future unknown format is never overwritten without explicit adoption.

## Phase 5: Target-aware Codex verification

Status: `COMPLETE`

1. Resolve the active Codex home from `CODEX_HOME` or the default home.
2. Classify a selected global base or override target only by its resolved path
   under the active Codex home.
3. Add `--cwd PATH` to `verify-codex`.
4. Use a disposable empty directory for active global verification.
5. For project verification, use explicit `--cwd` or the target parent.
6. Require the project probe directory to exist and be a directory.
7. Preserve marker and first/last content-sentinel checks.
8. Report the effective probe directory and verification context in typed results
   and JSON.
9. Return `RUNTIME_UNVERIFIED` with the exact command and failure when Codex omits,
   truncates, duplicates, or cannot inspect the bundle.

Acceptance:

- global verification remains isolated from project instructions;
- repository and nested targets can pass from an applicable startup directory;
- a startup directory outside the target's discovery chain does not pass;
- no model request or shell invocation is introduced.

## Phase 6: Neutral CLI and documentation

Status: `COMPLETE`

1. Add neutral manifest discovery with ambiguity refusal and legacy fallback.
2. Add `init --target` and write portable target text into the scaffold manifest.
3. Replace global-only scaffold prose, IDs, module names, help, public API
   docstrings, package description, examples, and README positioning.
4. Print the exact next lock command after `init`.
5. Document parallel global and project quickstarts that converge on the same
   lock, validate, check, install, and verify workflow.
6. Document safe adoption of an existing handwritten target.
7. Document repository CI checks for a committed manifest, lock, modules, and
   generated target.
8. Keep arbitrary custom output support but promise Codex visibility only for
   paths the runtime actually discovers.

Acceptance:

- a new user can scaffold either use case without editing the generated target;
- legacy explicit commands remain accepted;
- no operator-facing text incorrectly describes project overrides as global-only.

## Phase 7: Repository dogfood cutover

Status: `COMPLETE`

1. Create `.agents/agents-md.toml` targeting `../AGENTS.md`.
2. Copy the current project contract bytes exactly to
   `.agents/modules/project-contract.md` before replacing the root target.
3. Update that canonical module, not the generated root target, for the new
   project contract established in Phase 1.
4. Generate `.agents/agents-md.toml.lock.json` with lock format 2.
5. Render and compare before mutation.
6. Capture the unmanaged root target digest.
7. Run an install dry run, then explicitly adopt the root target with the captured
   digest and `--apply`.
8. Verify `check`, `status`, and `verify-codex --cwd .`.
9. Confirm `CLAUDE.md` continues importing the generated root target.

Acceptance:

- root `AGENTS.md` is generated format 2;
- the canonical module is the only editable project-contract source;
- manifest, lock, canonical source, and generated target are tracked inputs;
- no global Codex file or configuration changed;
- dogfood check and runtime visibility pass from the repository root.

Rollback:

- if any post-install dogfood gate fails, preserve the receipt path;
- confirm the target still matches the receipt installed digest;
- run receipt-based rollback with `--apply`;
- verify the original unmanaged target digest is restored;
- stop and report the failed gate.

## Phase 8: Validation and completion

Status: `COMPLETE`

Run, in order:

```bash
uv sync --locked
uv run pytest
uv run pyright
uv run ruff check .
uv run ruff format --check .
uv run pydoclint src/agents_md_compiler
uv run bandit -c pyproject.toml -r src
uv run agents-md-compiler lock --manifest .agents/agents-md.toml --check
uv run agents-md-compiler validate --manifest .agents/agents-md.toml
uv run agents-md-compiler check --manifest .agents/agents-md.toml
uv run agents-md-compiler verify-codex --manifest .agents/agents-md.toml --cwd .
uv run prek run --all-files --show-diff-on-failure --color always
```

For every changed Markdown path, run the repository table formatter,
`markdownlint-cli2 --fix`, the non-fixing linter, inspect the diff, and run the
final Unicode scan. Run the artifact build and installed-wheel gates because the
public API, package data, CLI, and schemas change.

Completion requires:

- 100 percent line and branch coverage;
- strict Pyright with zero errors;
- all format, lint, docstring, security, schema, package, and aggregate gates pass;
- invocation through both the console script and `python -m` passes;
- version-1 migration, project verification, shared locking, deployment state,
  and dogfood behavior have direct tests;
- one final full pass makes no additional edits;
- every unavailable or failed external runtime check is reported explicitly.

## Execution record

- Completed: 2026-08-10.
- Tests: 693 passed with 100 percent line and branch coverage.
- Python: Ruff check and format, strict Pyright, public type completeness,
  pydoclint, and Bandit passed.
- Dependencies: no version changed; OSV returned no advisories for all 12 exact
  build and direct development versions.
- Artifacts: Twine strict validation and archive inspection passed; the wheel and
  sdist each contained 28 files, and all 28 wheel members matched the direct
  build.
- Installed wheel: the clean CPython 3.14.6 smoke suite passed for global and
  repository targets, drift, rollback, and unmanaged adoption.
- Dogfood: lock, validation, target check, status, and Codex prompt-input
  verification all returned `CURRENT`; Codex found all three required markers
  and the content sentinel from the repository root.
- Aggregate: every configured `prek` hook passed, including Markdown, Python,
  package, security, immutable-pin, and GitHub Actions checks.
- Unicode: all 44 changed text files were scanned. The only candidates are the
  pre-existing exact non-ASCII path fixture in `tests/test_lockfile.py`.

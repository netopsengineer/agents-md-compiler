# Runtime bundle verification execution plan

## Authority, scope, and baseline

Implement issues [31](https://github.com/netopsengineer/agents-md-compiler/issues/31)
and [32](https://github.com/netopsengineer/agents-md-compiler/issues/32) together.
The user selected this implementation plan on 2026-11-05. The initial endpoint
was a validated, independently reviewed working-tree change. A subsequent user
instruction authorizes committing as `netopsengineer`, pushing a feature branch,
opening a PR, repairing CI for at most five cycles, merging only after CI is
green (admin bypass permitted), and monitoring follow-on CI after merge.
Installation, manual package publication, issue closure, and unrelated external
messages remain outside scope. Existing automatic release workflows may run
following the authorized merge; monitor their outcome without bypassing gates.

Baseline: clean `8e3ea03cbac412245f337d9950c91250f9b0c95c`, verified at entry.
Preserve Python >=3.14, dependencies, toolchain, public command signatures,
schema version, state tokens, exit codes, manifest/lock formats, rendered golden
bytes, installation behavior, personal policies, and Codex configuration.

Required sources: supplied global/project instructions, `CONTRIBUTING.md`,
`docs/cli-contract.md`, `docs/security-model.md`, both rendered-format contracts,
and the canonical Python standard at
`/Users/joshuawatkins/Vaults/main-remote-sync/docs/ai_knowledgebase/prompt_engineering_templates/agent-policy/python/modern-python-standards.md`.
Before Python work, load sections 0-3 and complete applicable rule sections and
cross-references directly. Apply task scope and library/CLI profiles. If missing,
block dependent Python work. Historical plans are context, not active procedure.

## Selected implementation contract

- Accept a root JSON array of response items. Inspect message `input_text`
  content only: system/developer instructions and producer-wrapped AGENTS user
  instructions. Ignore assistant/tool/metadata/key strings and supported image
  and audio content. Reject malformed relevant fields and unsupported envelopes.
- Keep fields separate with JSON location, role, and byte offsets. Decode JSON,
  then strictly encode UTF-8; never normalize or concatenate evidence fields.
- Recognize the exact producer AGENTS wrapper without trimming enclosed bytes.
  Producer evidence is `openai/codex` tag `rust-v0.166.0`, files
  `codex-rs/core/src/prompt_debug.rs`, `codex-rs/protocol/src/models.rs`, and
  `codex-rs/core/src/context/user_instructions.rs`, plus the fragment serializer.
  Synthetic fixtures are not authenticated captures or universal compatibility.
- Locate headers at line boundaries, count every selected-ID candidate, then
  validate selected format/manifest/lock identity, ordered module boundaries,
  observed byte counts/digests, separators, uniqueness, and entire generated span
  against `RenderedBundle.data`. Reuse `validate_rendered` invariants without
  changing its public interface or rendered bytes.
- Two exact selected copies fail as duplicate; a current plus stale/altered
  selected-ID candidate fails as ambiguous. Never choose only the good copy.
- Attribute compiler tokens through the field or next header. Host prose may
  surround a bundle; extra/nested/unmatched markers after an intact prefix fail,
  even after prose. Out-of-boundary content cannot repair a module.
- Malformed foreign bundles produce a diagnostic and incomplete overlap scan;
  they establish no overlap. Fail selected verification when their structure
  makes selected identity/boundaries ambiguous.
- Compare complete observed module bodies across distinct bundle IDs. Index by
  digest, confirm bytes. `CROSS_BUNDLE_CONTENT_OVERLAP` is a warning and still
  permits `CURRENT`; equal IDs with different bodies produce informational
  `MODULE_ID_REUSE`. Report both identities, digests, and evidence locations.
  No fuzzy matching, rewriting, or configurable failure policy.
- Retain static CURRENT prerequisites, startup-directory selection, contamination
  checks, deadlines, output limits, and read-only behavior. Visibility does not
  establish model compliance.

## Interface and ownership

Keep evidence adapter, parser, typed results, and CLI integration under the root
integration agent. Extend `RuntimeVerification` with defaulted fields:
`verification_method` (`complete_bundle_bytes`), `content_verified`,
`selected_bundle_occurrences` (nullable), `modules_expected`, `modules_verified`,
`overlap_scan_complete`, and ordered typed `diagnostics`.

Diagnostics carry code, severity, bundle/module IDs, observed digests, and primary
and related evidence locations (JSON path, role, byte offsets). Keep policy bodies
and sentinel prose out of reports. Failure categories distinguish unsupported
evidence, missing identity, duplicates, ambiguous identity, malformed boundaries,
module order, and content mismatch. Preserve captured executable/version/probe
context and actual counts on failure; never infer successful module counts.

Markers/sentinels are legacy diagnostics scoped to a selected occurrence, not
success predicates. Sentinel selection is optional for short/repetitive modules
and must not run again while reporting a failure. JSON fields are additive under
the minor-release policy. Do not manually bump the package version. Text stdout
retains state; stderr carries findings under existing quiet rules.

Delegation after recording this contract:

1. Regression worker owns dedicated new test files and non-overlapping fixtures.
   Load the Python standard directly; establish pre-fix issue failures and verify
   through the verifier and subprocess/CLI interface.
2. Documentation worker owns README, CLI/security contracts, fixture provenance,
   and user-facing change documentation, using this interface contract.
3. Root owns production, existing tests/shared fixtures, integration, and this
   progress record. Workers must preserve others' changes.
4. Independent reviewer inspects the integrated diff in isolated context, attempts
   false positives/negatives, checks compatibility/provenance, and returns
   actionable findings without production edits. Resolve findings and recheck.

## Acceptance and validation

Cover intact, short/repetitive, removed/changed middle, reordered/duplicated/
missing/relocated modules, first/last truncation, extra post-prefix markers,
wrong size/digests/headers/boundaries, unrelated bundles in one/multiple fields,
ID reuse, overlap with different IDs, duplicate/ambiguous selected identities,
unsupported/untrusted placements, split fields, multibyte bodies, CRLF/Unicode/
whitespace/final-LF changes, malformed foreign bundles, and nondisclosure.
Characterize preserved probe/capability/timeout/output/static/context failures
before structural refactoring. Demonstrate issue regressions fail at baseline.

Run targeted regressions, full tests with 100% line/branch coverage, aggregate
`uv run prek run --all-files --show-diff-on-failure --color always`, edited-path
Markdown table/fix/non-fix passes, diff inspection, and final Unicode scans.
Inspect tool edits and rerun affected checks. Build with `uv build --no-sources`,
run metadata/archive gates, compare normalized sdist-built wheel, and run clean
Python 3.14 installed-wheel smoke/type checks and both CLI invocation forms.
Verify protected files/formats/goldens unchanged.

Attempt read-only repository `verify-codex --manifest .agents/agents-md.toml
--cwd .`. Record command and outcome separately from synthetic tests. If the
host probe remains unavailable, report the failure, acceptance boundary, and
resumption condition; do not change configuration or invent alternative evidence.
Local implementation completion requires local/artifact/review gates. Runtime
integrated readiness additionally requires successful live host evidence.

## Progress and recovery

- Entry: baseline clean; issues fetched read-only and match the selected plan.
- Loaded Python standard revision 2026.09.1. Decisions: preserve floor/toolchain
  (`PY-PROJ-003`, `PY-PROJ-007`); isolate pure evidence validation from subprocess
  I/O (`PY-DESIGN-002`, `PY-CORR-001`); preserve API/CLI contracts except the
  authorized additive and correctness changes (`PY-API-002`, `PY-OBS-003`).
- Producer files fetched directly through GitHub. User instruction placement is
  a user-role `input_text` field with `# AGENTS.md instructions` and INSTRUCTIONS
  wrapper. No successful local live probe is claimed.
- Baseline characterization: `uv run pytest --no-cov tests/test_codex.py
  tests/test_cli.py` passed 142 tests before production edits. The regression
  worker then ran the initial `tests/test_runtime_content.py`: two failures
  established the missing-middle false success (#31) and shared-module false
  rejection (#32). Tests replaced only probe I/O, using producer-shaped evidence.
- Integration: added pure typed `runtime_evidence.py`, connected complete-content
  verification to existing subprocess/CLI behavior, retained failure metadata,
  made sentinels optional, and converted the synthetic mock to the supported
  root array. Existing arbitrary-string extraction assertions were replaced with
  rejection/typed-field checks; probe/context/static-state coverage was retained.
- Regression worker completed 90 verifier/CLI cases in its two owned new files:
  `uv run pytest --no-cov tests/test_runtime_content.py
  tests/test_runtime_content_cli.py` passed. Both original regressions now pass.
  Worker Ruff, strict Pyright, and final Unicode checks passed on those files.
- Documentation worker completed README, CLI/security contracts, fixture
  provenance, and unreleased changelog notes. Edited-path table formatting,
  fixing/non-fixing Markdown lint, diff inspection, and Unicode prefilters passed.
  Follow-up documents bounded overlap findings and contamination redaction.
- First integrated full suite: 902 tests passed, coverage gate failed at 99.56%.
  Missing branches were malformed adapter fields and diagnostics; root added
  `tests/test_runtime_evidence.py` and CLI negative-path checks. Strict Pyright
  passed. Pydoclint initially rejected the exception factory documentation;
  direct typed exception construction resolved it and pydoclint passed.
- Additional bounds: at 1,000 overlap/ID-reuse findings, emit OVERLAP_SCAN_LIMIT
  and mark the overlap scan incomplete without failing selected integrity.
  Index by digest and bundle identity to avoid repeated same-identity pair work.
  Redact only contaminated command/directory values as `[REDACTED]` to prevent
  sentinel disclosure (`PY-SEC-003`, `PY-SEC-010`, `PY-OBS-003`).
- Independent review found F1: a foreign orphan end before an open start could
  produce false CURRENT because the fallback compared token totals. Root
  reproduced two failing CLI variants, replaced totals with ordered,
  identity-aware unmatched-start tracking, and added four consumer regressions.
  Independent replay rejects all four open-boundary siblings and preserves both
  closed-boundary controls. No surviving material reproduced findings remain.
- The final mock reads bytes then decodes UTF-8, avoiding Python text-mode CRLF
  conversion. The new CLI regression rejects CRLF-corrupted evidence. Independent
  fixture-only re-review verified this behavior and found no material findings.
- Implementation, local validation, artifacts, and independent review are
  complete. Live host integration remains unavailable as described below.
  No publication, policy installation, issue update, or external message occurred.

## Final tested state and evidence

Tested on Darwin with CPython 3.14.8, the locked development environment, and
installed `uv` 0.13.7. Git HEAD remains the baseline; this is an uncommitted
working-tree implementation. Dependencies, package version, tool configuration,
rendered formats/goldens, manifest/lock formats, installation implementation,
`.agents/`, and root `AGENTS.md` are unchanged. The added `.gitignore` exception
makes this requested execution record visible despite the existing `docs/*` rule.

The final tree inventory, excluding this evolving evidence record itself, is
`/private/tmp/amc-runtime-artifacts-wfyb6_oz/tested-tree-inventory.json`, SHA-256
`6bacc7cde4e500800fb050d3b6348da4394699af66db1ee2218e8be93cabacea`.
All production Python bytes were compared with the independently reviewed
remediated snapshot and the built wheel; both comparisons passed.

| Gate                     | Final result                                                                                          | Evidence                                                                 |
|--------------------------|-------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| Locked environment       | `uv sync --locked` passed                                                                             | 55 resolved packages, 47 checked                                         |
| Full tests               | 922 passed; 100% line and branch coverage                                                             | `/private/tmp/amc-runtime-artifacts-wfyb6_oz/pytest.log`                 |
| Aggregate                | `uv run prek run --all-files --show-diff-on-failure --color always` passed every hook                 | `/private/tmp/amc-runtime-artifacts-wfyb6_oz/aggregate.log`              |
| New-file direct gates    | Ruff, strict Pyright, pydoclint passed; new modules/tests are also included by full pytest/Pyright    | Explicit new paths checked because `--all-files` uses tracked paths      |
| Dependency audit         | `uv audit --frozen` passed                                                                            | No known vulnerabilities or adverse project statuses in 54 packages      |
| Build and metadata       | `uv build --no-sources --out-dir .../dist`, `twine check --strict`, archive inspection passed         | Artifact root below                                                      |
| Reproducibility          | Sdist-built and direct-built wheels match all 29 regular members                                      | `scripts/inspect_dist.py compare`                                        |
| Installed package        | Clean Python 3.14.8 environment; public imports, both invocation forms, and `scripts/smoke.sh` passed | Artifact root `fresh/` and `smoke/`                                      |
| Installed typing         | 100% completeness; 805 known public symbols, zero ambiguous/unknown                                   | Pyright resolved `fresh/lib/python3.14/site-packages/agents_md_compiler` |
| Static repository target | Explicit `check --manifest .agents/agents-md.toml` returned CURRENT                                   | Protected paths and golden files have no Git diff                        |
| Independent review       | F1 resolved; no surviving material reproduced findings                                                | Review and fixture addendum below                                        |
| Markdown and Unicode     | Edited-path table formatter, fixing/non-fixing lint, final byte/code-point scan                       | Final pass recorded with handoff                                         |
| Live Codex adapter       | UNAVAILABLE: probe returned exit 1                                                                    | Separate boundary below                                                  |

The first aggregate attempt was sandbox-blocked when a hygiene hook opened
`.agents/agents-md.toml`; the authorized filesystem-approved run succeeded.
An intermediate aggregate run found a pydoclint exception-documentation mismatch;
typed exception construction fixed it and every final hook passed. No gate was
weakened or waived. Initial coverage deficits were filled by behavioral tests.

The first installed-package type checks did not resolve the clean environment.
Inspection of installed Pyright showed its verifytypes path does not copy the
interpreter/config settings used by those attempts. Selecting the clean
environment's Python through PATH fixed resolution, and the output explicitly
identified the wheel's installed `py.typed`. This was a check-environment repair,
not a package change (`PY-PKG-005`, `PY-TYPE-008`).

Artifact root: `/private/tmp/amc-runtime-artifacts-wfyb6_oz`.

- Wheel: `dist/agents_md_compiler-1.0.1-py3-none-any.whl`, SHA-256
  `2575d8d56f883ad416298a90cc6c4ce6dcd0549471746c566da12d2fb26c7957`.
- Sdist: `dist/agents_md_compiler-1.0.1.tar.gz`, SHA-256
  `63765f12b0158b209f9e80da4364bba76064001271ff07e01142a7e202448586`.
- `inventory.json` records sizes and digests. Archive listings were inspected;
  no tests, plans, policy bundles, receipts, backups, or personal absolute paths
  ship. These local artifacts retain the unchanged package version and are not
  published release candidates.

Independent review evidence:

- `/private/tmp/amc-runtime-review-y1_6smai/evidence/review.md` records full-diff
  inspection, independent head/base CLI reproductions, sibling controls,
  remediation replay, producer-blob verification, and limitations.
- `/private/tmp/amc-runtime-review-y1_6smai/evidence/fixture-addendum.md` records
  the final two-file fixture delta, independent CRLF subprocess control, and
  passing CLI regression.
- Remediated source inventory SHA-256:
  `d0501eec387b13ebeb1cd010f3bcec86870f93f8f92e6d18684c3d58ebf36449`.
- Final fixture-review inventory SHA-256:
  `944862df00608c9ee9ba398ca55dea64fe5c6dae7162c22f2e798c98ba6e380e`.
- Review used isolated snapshots and independently collected evidence. The
  reviewer disclosed exposure to assignment progress/repair descriptions and
  did not use those assertions as correctness evidence. This is independent
  execution/review, not a claim of wholly blind evaluation.

## Acceptance mapping

| Issue / requirement                        | Executable evidence                                                                           | Observed result                                                                         |
|--------------------------------------------|-----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| #31 intact bundle, no-sentinel modules     | `tests/test_runtime_content.py`: supported roles, surrounding prose, short/repetitive modules | Pass with complete content verified                                                     |
| #31 complete bodies and placement          | Same file: corruption matrix, post-prefix tokens, split fields, UTF-8/normalization           | Removed/altered/reordered/relocated/truncated content fails                             |
| #31 evidence trust and no compliance claim | Same file: arbitrary JSON placement and malformed envelope cases; CLI/security docs           | Unsupported shapes fail; metadata/user/tool strings cannot establish success            |
| #32 independent bundle identity            | Same file: unrelated bundles and selected multiplicity in one/multiple fields                 | Unrelated bundles pass; duplicate/ambiguous selected IDs fail                           |
| #32 overlap and module-ID reuse            | Same file plus `tests/test_runtime_content_cli.py`                                            | Exact bodies warn and remain CURRENT; different bodies with equal IDs are informational |
| #32 malformed foreign boundaries           | New CLI boundary-order variants and independent F1 replay                                     | Ambiguous open boundaries fail; closed foreign controls pass                            |
| Reporting and retained failures            | `tests/test_runtime_evidence.py`, new CLI tests, existing Codex/CLI tests                     | Metadata preserved, no sentinel/body disclosure, quiet/JSON semantics preserved         |
| Static/probe/runtime safeguards            | Existing tests plus invalid JSON, surrogate, size, context, and CRLF cases                    | Capability, deadline, output, shadowing, and context failures remain explicit           |

## Live runtime boundary and resumption

Executed read-only from the repository root:

```bash
uv run agents-md-compiler verify-codex --manifest .agents/agents-md.toml --cwd . --format json
```

The selected target matched its compiled bytes. Executable
`/opt/homebrew/bin/codex` reported `codex-cli 0.166.0`, and capability detection
succeeded. The exact prompt probe was:

```bash
/opt/homebrew/bin/codex debug prompt-input 'agents-md-compiler runtime visibility probe'
```

Observed result: probe exit 1; verifier exit 1 with RUNTIME_UNVERIFIED,
content_verified=false, selected_bundle_occurrences=null, and preserved
executable/version/project-directory metadata. No usable prompt-input document
was obtained. The planning-stage Operation not permitted observation is historical;
this run establishes only the captured nonzero exit, not its internal cause.

Successful live startup integration is the remaining external dependency. Resume
that boundary when the installed host can return supported prompt-input JSON
from this working directory, then rerun the exact read-only verifier command.
Do not infer success from synthetic tests, change Codex configuration, invent a
fallback parser, or ask a model to report its own instructions. No successful
live integration or model-compliance claim is made.

Before a handoff or interruption, update this section with tested state, command,
result, evidence locator, ownership, remaining dependencies, and next action.
After interruption, reconcile actual files and pending processes before resuming;
retain valid results and do not repeat uncertain mutations. No personal policy
or installed target writes are authorized.

## Git integration and CI continuation

Owner: root integration agent. User authorization extends the endpoint as stated
above. CI repair budget: 0 of 5 cycles used. A cycle consists of a corrective
change or evidence-backed rerun followed by assessment of the affected CI.
Stop after five unsuccessful repair cycles; never merge a failing head.

The `netopsengineer` account was verified through the GitHub API, with admin
access to `netopsengineer/agents-md-compiler`. Its public email matches the local
Git email. Use that account explicitly for GitHub operations and use
`netopsengineer` as the commit author name. Leave the workstation's default
GitHub account and personal Git configuration unchanged.

Next actions: create the feature branch, stage only the reviewed change set,
commit with hooks enabled, push, open the PR, and monitor its exact head checks.
Record the PR/head/run identities and repair count here before any interruption.
After green CI, merge and monitor main plus release-preparation follow-on runs.
Preserve the live-runtime limitation above in the PR and final report.

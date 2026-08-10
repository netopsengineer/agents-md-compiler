# CI, security, and release workflow evidence

Verification record for the workflows in `.github/`. Every external reference was
re-resolved live immediately before the workflows were written, and again after, as a
check that nothing moved during authoring. Point-in-time tables elsewhere are evidence
of a past verification, not permission to skip a current one.

Action-pin verification date: 2026-08-07. Release and delivery evidence updated
2026-08-07. The tagless release-preparation path was reverified on 2026-08-10.
Tooling: `gh api` against
`repos/{owner}/{repo}/releases/latest`, `/tags`, and `/git/refs/tags/{tag}`, with
annotated tags resolved through `/git/tags/{object}` to their commit.

## Action pins

Ten unique action references across 58 use sites. Every pinned SHA equals the commit
its `# frozen:` comment names, and every pin is the newest release and newest tag for
its repository at the verification date.

| Action                                            | Tag       | Commit SHA                                 | Use sites | Live verdict |
|---------------------------------------------------|-----------|--------------------------------------------|-----------|--------------|
| `step-security/harden-runner`                     | `v2.20.1` | `b09bb98e06d4d774595224525879c09bc6e98c40` | 21        | match        |
| `actions/checkout`                                | `v7.0.1`  | `3d3c42e5aac5ba805825da76410c181273ba90b1` | 17        | match        |
| `astral-sh/setup-uv`                              | `v9.0.0`  | `c771a70e6277c0a99b617c7a806ffedaca235ff9` | 11        | match        |
| `actions/upload-artifact`                         | `v7.0.1`  | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | 2         | match        |
| `actions/download-artifact`                       | `v8.0.1`  | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | 2         | match        |
| `actions/create-github-app-token`                 | `v3.2.0`  | `bcd2ba49218906704ab6c1aa796996da409d3eb1` | 1         | match        |
| `actions/dependency-review-action`                | `v5.0.0`  | `a1d282b36b6f3519aa1f3fc636f609c47dddb294` | 1         | match        |
| `google/osv-scanner-action`                       | `v2.3.8`  | `9a498708959aeaef5ef730655706c5a1df1edbc2` | 1         | match        |
| `pypa/gh-action-pypi-publish`                     | `v1.14.2` | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` | 1         | match        |
| `python-semantic-release/python-semantic-release` | `v10.6.1` | `39dd2052f2ce8282a5d932c31d58a2ca06d2550e` | 1         | match        |

`actions/dependency-review-action` is not in the Phase 1 table because Phase 1 did not
anticipate needing it. It was resolved and advisory-checked at the same time as the
others: OSV.dev returned no advisories for it.

Two of the pinned actions reference further software by a mutable identifier inside
their own implementation, which this project cannot pin from the outside:

- `google/osv-scanner-action`'s scanner step runs the container image
  `ghcr.io/google/osv-scanner-action:v2.3.8`, a tag rather than a digest.
- the OSV reusable workflow calls `actions/download-artifact@v8`, a major-version tag.

Both are upstream decisions inside a SHA-pinned entry point. Pinning the entry point
is what this repository controls, and it is pinned.

### Enforcement rather than inspection

`scripts/check_pins.py` runs as the `check-pins` hook in the aggregate gate. It fails
the build when any workflow `uses:` or any third-party hook `rev:` is not a 40-hex
commit SHA carrying the tag it came from. This exists because an automated dependency
update can rewrite a pin to a mutable tag, and Dependabot's `pre-commit` ecosystem is
not documented to preserve SHA pins. The check is structural and offline; whether a
SHA still corresponds to its tag is the live verification recorded above.

The same gate checks `.github/dependabot.yml` and rejects any active `labels` key.
GitHub's documented default is operationally safer here: Dependabot creates its
default `dependencies` and ecosystem labels when they do not exist. Custom labels
would require separate repository provisioning that this repository does not perform.
The checked-in configuration therefore cannot name a label that is absent from a new
or existing repository.

For `release.yml`, the same gate requires the exact-push package eligibility
check, stage-only python-semantic-release inputs, an exact staged-path allowlist,
a non-force protected-main push, the complete gate on the exact prepared commit,
the minimal OIDC publish boundary, publication-success ordering in the shared
finalizer, and a verification-only recovery path. It also rejects a checkout,
dependency install, or build command in the privileged publish job.

Negative-path evidence, run against synthetic files outside the repository:
a mutable `uses:` tag, a mutable hook `rev:`, and a SHA with no tag comment each
produced exit code 1 with the offending path and line named. On 2026-08-07, synthetic
bare, quoted, and flow-mapping Dependabot `labels` keys were rejected while a
commented example was ignored. Regression tests also remove each critical release
control and inject a checkout into `publish`; every mutation is rejected. The
repository configuration passed with seven files checked.

## Effective permissions

Every workflow declares `permissions: contents: read` at the top level, so any job
without its own block is read-only. Elevations, in full:

| Workflow            | Job                 | Permissions                                                 | Why                                                  |
|---------------------|---------------------|-------------------------------------------------------------|------------------------------------------------------|
| `release.yml`       | `publish`           | `id-token: write` only                                      | Mints the OIDC token PyPI exchanges for upload       |
| `release.yml`       | `finalize`          | `contents: write`                                           | Creates the release tag and the GitHub release       |
| `security-scan.yml` | `osv-scanner`       | `actions: read`, `contents: read`, `security-events: write` | Uploads SARIF to code scanning                       |
| `security-scan.yml` | `dependency-review` | `contents: read`, `pull-requests: write`                    | Posts the failure summary comment                    |
| `auto-fix.yml`      | `fix`               | `contents: write`                                           | Pushes the mechanical fix commit to a feature branch |

Properties this table establishes:

- `id-token: write` occurs exactly once in the repository, in the publish job.
- The publish job declares no other scope, so its token has no repository read or
  write access at all.
- The `semantic-release` job inherits `contents: read` and performs its
  protected-branch write with a per-run GitHub App token instead, which is why it does
  not appear above.
- No job uses `pull_request_target`.

## Release trust boundary

The publish job contains no checkout, no dependency installation, no build step, and
no cache. It downloads the artifact the build job uploaded, re-hashes every file
against the `inventory.json` the build job wrote, refuses any digest or artifact-set
change, and only then uploads. The bytes published are therefore the bytes that passed
the gate.

`skip-existing` is deliberately `false`: a name that already exists on the index means
the release already happened, and succeeding quietly would hide that.

The bootstrap and semantic paths create the tag and GitHub release only after PyPI
accepts the upload. The finalizer refuses to move an existing tag. A separate
`recover` dispatch verifies the exact prepared main commit and the public PyPI wheel
and sdist bytes, digests, and Trusted Publisher provenance, then creates only missing
GitHub state. It never rebuilds or republishes.

## PyPI Trusted Publisher

The pending publisher was verified through the authenticated `netopsengineer` PyPI
session before publication. The first accepted upload on 2026-08-07 converted it to
an active project publisher with this exact identity:

| Field            | Value                                 |
|------------------|---------------------------------------|
| Project          | `agents-md-compiler` (active)         |
| Publisher        | GitHub                                |
| Owner/repository | `netopsengineer/agents-md-compiler`   |
| Workflow         | `release.yml`                         |
| Environment      | `pypi`                                |
| Authentication   | OIDC Trusted Publishing; no API token |

The account publishing page now lists `agents-md-compiler` under active publishers
and reports that no pending publishers are configured. Public PyPI provenance for
both release files independently records the same repository, workflow, and
environment.

## Validation results

Run locally on 2026-08-06 after the approved uv 0.12.2 update, then reproduced by
GitHub Actions on exact commit
`7278d871753990643c0fe2494cfd5d46740b7deb`.

| Check                                                | Result                                                |
|------------------------------------------------------|-------------------------------------------------------|
| `uv run prek run --all-files`                        | 23 hooks passed, 0 failed, 0 skipped                  |
| actionlint, including its embedded shellcheck        | passed on all five workflows                          |
| zizmor, default persona                              | passed                                                |
| zizmor, `--persona=pedantic`                         | no findings, 3 suppressed                             |
| `check-yaml` over every workflow and Dependabot file | passed                                                |
| `check_pins.py`                                      | 6 files checked, every pin an immutable SHA           |
| Tag-comment resolution against the live API          | 10 of 10 references match, covering 51 use sites      |
| `scripts/inspect_dist.py inspect`                    | wheel 27 members, sdist 27 members, no forbidden path |
| Wheel from sdist vs wheel from tree                  | 27 shared members, byte-identical whole files         |
| `uv run twine check --strict`                        | both artifacts passed                                 |

Exact-commit GitHub Actions results:

| Workflow        | Run ID        | Trigger             | Result  | Material evidence                                                                                                 |
|-----------------|---------------|---------------------|---------|-------------------------------------------------------------------------------------------------------------------|
| `validate`      | `31128951491` | `workflow_dispatch` | success | aggregate gate, 100 percent coverage, strict types, three golden platforms, three installed-wheel smoke platforms |
| `security-scan` | `31128951886` | `workflow_dispatch` | success | Bandit, actionlint, zizmor, full-history gitleaks, OSV lock/repository scan, SARIF upload                         |

The dependency-review job was skipped as designed because the security run was not
a pull request event. The advisory Python 3.15 job succeeded. The validation run
uploaded artifact `8975201094` as `validate-dist`; its retention deadline is
2026-08-13.

Artifact inventory from both the fresh local build and the exact downloaded CI
artifact:

| Artifact                                    | Size  | SHA-256                                                            |
|---------------------------------------------|-------|--------------------------------------------------------------------|
| `agents_md_compiler-0.1.0-py3-none-any.whl` | 75895 | `1c2a667d133ff06c9650736a7d890a8b7613c977cefa9e64ad970fef5bfe3bb7` |
| `agents_md_compiler-0.1.0.tar.gz`           | 66105 | `ad28009e55e1546af0a31dbafc8e074ea9a4393b2a5dc404337f739d4072b802` |

The wheel was installed with cache disabled into a clean CPython 3.14.6
environment. The environment contained only `agents-md-compiler==0.1.0`, both
documented invocation forms worked, all 51 public API names imported, and
`scripts/smoke.sh` reported `smoke: all assertions passed`.

### Automatic delivery and release acceptance

PR 1 proved automatic pull request delivery after GitHub recovered. Its exact head
was `512a3c77ccf737ab6de8fd08deec4c69832f8527`; validation run `31129983690`
and security run `31129984567` succeeded, including dependency review and all 12
strict required checks. The pull request was squash-merged as protected-main commit
`b6a3832f9abf10e5c713ec879cdd39ff4759ad58`.

Automatic push delivery on that commit also succeeded:

| Workflow        | Run ID        | Result  | Notes                                                                |
|-----------------|---------------|---------|----------------------------------------------------------------------|
| `validate`      | `31185800696` | success | Attempt 2 passed every job after the transient runner failure below  |
| `security-scan` | `31185801938` | success | Push security jobs passed; dependency review skipped by event design |
| `release`       | `31185799319` | success | Disabled semantic path resolved to an intentional no-op              |

Attempt 1 of validation run `31185800696` failed before checkout only on the macOS
runner because the downloaded `astral-sh/setup-uv` `action.yml` was empty or invalid.
The same immutable action SHA succeeded in the PR, its upstream blob was valid, and
the failed-job rerun passed without a repository change. This was runner-side action
download corruption, not a product or workflow defect.

The first bootstrap dispatch, run `31186319883`, safely completed the gate,
bootstrap preconditions, artifact build, metadata inspection, archive inspection,
wheel smoke test, and immutable artifact upload. GitHub then skipped `publish` and
`finalize-bootstrap`: its implicit success guard propagated the deliberately skipped
semantic branch even though the explicit build prerequisite succeeded. No PyPI file,
tag, or GitHub release was created.

PR 2 fixed both transitions by using `always()` with explicit successful
prerequisites. Validation run `31186784378` and security run `31186786399` passed on
exact head `a2340c2790e4c692e25c0199d84a832c65beb6d4`, including dependency review,
all three golden platforms, all three clean installed-wheel smoke platforms, and all
12 required checks. Its protected squash merge produced commit
`9a38c9f4f79395890f4660e6d5d9e43bc7d88b1e`.

The exact merge commit then passed automatic validation run `31186960325`, security
run `31186960371`, and disabled semantic no-op run `31186959315` before bootstrap was
retried.

Protected bootstrap run `31187111987` succeeded end to end on that commit. Artifact
`8997280019`, named `release-dist`, was downloaded before environment approval. Its
inventory matched the two files byte for byte. Strict metadata validation, archive
inspection, a no-cache wheel rebuild from the exact sdist, 27-member wheel
equivalence, a clean CPython 3.14.6 install, all 51 public imports, and the complete
CLI/install/drift/rollback/unmanaged-target smoke suite passed locally. Only then was
environment `pypi` approved by `netopsengineer`.

The publish job re-downloaded the artifact, re-verified both inventory digests,
removed only `inventory.json`, and published through OIDC Trusted Publishing with
attestations enabled. It contained no checkout, dependency installation, rebuild, or
cache. PyPI accepted the wheel at `2026-08-07T14:26:59.685378Z` and the sdist at
`2026-08-07T14:27:01.111958Z`. Finalization then created `v0.1.0` and the GitHub
release; the tag points to
`9a38c9f4f79395890f4660e6d5d9e43bc7d88b1e`.

Public endpoints: [PyPI 0.1.0](https://pypi.org/project/agents-md-compiler/0.1.0/)
and [GitHub v0.1.0 release](https://github.com/netopsengineer/agents-md-compiler/releases/tag/v0.1.0).

Public PyPI JSON reports the exact approved sizes and SHA-256 values shown above,
`requires_python` as `>=3.14`, and neither file yanked. The Integrity API exposes one
attestation bundle per file with this publisher identity:

| Field       | Value                               |
|-------------|-------------------------------------|
| Kind        | GitHub                              |
| Repository  | `netopsengineer/agents-md-compiler` |
| Workflow    | `release.yml`                       |
| Environment | `pypi`                              |

`pypi-attestations` 0.0.30 was resolved live from PyPI, its GitHub latest release
and latest tag both resolved to `v0.0.30`, and OSV returned zero advisories for that
exact version. It cryptographically verified both public distribution URLs against
`https://github.com/netopsengineer/agents-md-compiler`.

A separate no-cache install of `agents-md-compiler==0.1.0` from
`https://pypi.org/simple` into clean CPython 3.14.6 had no `direct_url.json`, imported
all 51 public names, and passed the complete smoke suite. This proves the public index
served the tested package rather than a repository path or warm cache.

After all publication checks passed, repository variable
`SEMANTIC_RELEASE_ENABLED` was set to `true`. Recovery run `31188014306` selected the
semantic path on the tagged commit, repeated the release gate, and reported
`released=false version=0.1.0 tag=v0.1.0` with the notice that no release-worthy
commit existed. Its build, publish, and finalization jobs were skipped. The first
enabled semantic run was therefore an idempotent no-op.

### GitHub Actions incident

GitHub recorded direct `PushEvent` entries from actor `netopsengineer` for the
initial commit and subsequent corrections on `refs/heads/main`, but created no check
suite and no workflow run for any push. GitHub also recorded the `opened`
`PullRequestEvent` for PR 1 at
`989601cc49332045e093017c8f3c0828bf0abac4`, again with no check suite or workflow
run.

Repository checks found all workflows active, Actions enabled with all actions
allowed, the default branch set to `main`, and both `validate.yml` and
`security-scan.yml` configured for `push.branches: [main]`. The commit messages have
no skip directive. Git authentication used username `netopsengineer` with a `gho_`
OAuth credential, not the recursion-suppressed repository `GITHUB_TOKEN`. The
repository Actions UI displayed no disablement, fork-approval, or policy warning.
Both affected workflows were disabled and immediately re-enabled through the
supported workflow API, then read back as active before the next PR synchronization
event.

The [GitHub Actions incident](https://www.githubstatus.com/incidents/qcvjkzcs7j74)
explains the missing runs. At 2026-08-06T22:18:09Z, GitHub reported Actions in a
major outage and stated that webhook triggers remained throttled, with many push and
pull request events not triggering new workflow runs.

GitHub later marked the incident resolved and its status summary returned all systems
operational. Automatic PR runs `31129983690`, `31129984567`, `31186784378`, and
`31186786399`, plus automatic main-push runs `31185799319`, `31185800696`,
`31185801938`, `31186959315`, `31186960325`, and `31186960371`, prove that both event
classes recovered. Status: `RESOLVED`; automatic delivery is no longer blocked.

## Decisions taken on evidence

**The TestPyPI rehearsal is declined for this remediation.** The operator made
this decision on 2026-08-05 because no TestPyPI project or TestPyPI Trusted
Publisher is configured. The bootstrap workflow therefore retains its direct
protected-environment publication path to PyPI. This is an explicit decision not
to apply the `PY-PKG-009` staging-registry recommendation, not evidence that a
staging upload or install succeeded. Reconsider it before changing publication
metadata or identity after the required TestPyPI infrastructure exists.

**`uv audit` is advisory, not blocking.** uv 0.12.2 prints `warning: uv audit is
experimental and may change without warning`. The execution plan gates this command on
its audited interface being verified stable, and that condition is not met. The
blocking advisory gate is the `osv-scanner` job, which covers the same resolved
dependency set: `uv.lock` has been a supported OSV-Scanner lockfile since scanner
v2.0.0. Promote `uv audit` to blocking when it leaves preview.

**Python 3.15 is an advisory job.** The newest 3.15 build published through
`actions/python-versions` at the verification date was `3.15.0-beta.4`, so 3.15 is
prerelease and its job is `continue-on-error`. When 3.15 reaches final release this
must be reclassified as a required check rather than left as a permanently ignorable
signal. The job also asserts that its ephemeral resolution did not rewrite `uv.lock`.

**Gitleaks runs twice, for different reasons.** The pinned pre-commit hook runs
`gitleaks git --pre-commit --staged`, which is correct at commit time but scans nothing
when no change is staged, so a passing hook in an `--all-files` CI run would be false
assurance. `security-scan.yml` therefore installs the pinned gitleaks release, verified
against the SHA-256 from the published checksums file, and scans both the full commit
history and the working tree.

**The publish job uses `egress-policy: audit`, not `block`.** Block mode requires an
explicit endpoint allowlist. The destinations used by Trusted Publishing and PEP 740
attestation signing are not documented by `pypa/gh-action-pypi-publish`, and guessing
them would risk failing a release for an unrelated reason. Tighten to `block` using the
endpoint list observed in the first audited publish run. Harden-runner also supports
block mode only on Linux, so the macOS and Windows matrix jobs skip the step entirely
rather than appear to enforce something the platform does not support.

**Metadata is Core Metadata 2.4.** Read from `METADATA` inside the built wheel. This
withdraws an earlier claim that twine 7.0.0 was required for 2.5 acceptance, corrected
in `docs/dependency-verification.md`, and it settles `PY-PKG-011`:
`[project].import-names` is conditioned on a backend emitting Core Metadata 2.5, so it
does not apply here.

## GitHub release infrastructure

Verified through the active `netopsengineer` session on 2026-08-07:

- public repository `netopsengineer/agents-md-compiler` exists;
- `compiler-release-bot` is owned by `netopsengineer`, has Metadata read and
  Contents read/write permissions only, and is installed only on this
  repository;
- repository variable `RELEASE_APP_CLIENT_ID` and repository secret
  `RELEASE_APP_PRIVATE_KEY` exist; the workflow uses no deprecated App ID input;
- legacy secret `RELEASE_APP_ID` remains unused so this configuration-only change
  does not delete repository state before its post-merge no-op release test;
- protected environment `pypi` requires review by `netopsengineer`;
- active repository ruleset `protect-main` has ID `20529695` and targets only
  `refs/heads/main`;
- repository variable `SEMANTIC_RELEASE_ENABLED` is `true`, set only after the
  published files, provenance, tag, release, and public install passed;
- secret scanning and push protection are enabled.

The active ruleset blocks deletion and force pushes, requires linear history,
squash-only pull requests, one current CODEOWNER approval, resolved review threads,
and 12 strict GitHub Actions checks. The `netopsengineer` user bypass is limited to
pull requests. The `compiler-release-bot` integration is the only always bypass, so
the release workflow can create its reviewed semantic-release commit without giving
the App administration or workflow permissions.

Every repository, App, secret, and environment operation used the active
`netopsengineer` account. The configured Tribe account remained inactive and was not
used.

## GitHub Actions annotation remediation

Successful v0.1.1 runs exposed two non-blocking annotation classes caused by local
workflow configuration:

- release run `31200451494` passed legacy `app-id` to
  `actions/create-github-app-token`, whose pinned metadata deprecates that input;
- validation run `31200685700`, security run `31200686600`, and release run
  `31200451494` had concurrent setup-uv jobs attempt to reserve identical cache
  keys.

The App-token input now reads `vars.RELEASE_APP_CLIENT_ID` through the supported
`client-id` field. The user-supplied value came from the authenticated private App
settings. The private key remains in `secrets.RELEASE_APP_PRIVATE_KEY`; job
permissions, App permissions, repository scope, and token lifetime are unchanged.

Cache ownership is explicit:

| Event or platform                          | Cache writer                         | Other jobs                                      |
|--------------------------------------------|--------------------------------------|-------------------------------------------------|
| Push, pull request, or validation dispatch | Linux `validate` gate                | Restore-only                                    |
| Scheduled security scan                    | Linux security static-analysis job   | Cache disabled for the audit-only job           |
| macOS and Windows validation               | Golden job for the matching platform | Smoke jobs keep the package cache disabled      |
| Release and auto-fix                       | None                                 | Restore-only; subsequent validation owns writes |
| Isolated Python prerelease signal          | None                                 | Cache disabled                                  |

This keeps dependency-cache reuse without same-key write races. It does not change
an Action version, immutable SHA, effective permission, dependency resolution, or
release artifact. Live release, tag, input-schema, and OSV evidence is recorded in
`docs/dependency-verification.md`.

PR 5 and protected-main commit
`b2c182af3ed20540fac2054963e9e1f37e632b3c` proved the deprecation and cache-race
fixes. Every pull request check had zero annotations. Post-merge validation run
`31205466450`, security run `31205466852`, and release run `31205466214` passed.
The release used the App Client ID successfully and reached the intended semantic
no-op, with build and publication skipped. Its sole annotation was the repository's
own `::notice` no-op message, not an Action warning. All repository-authored workflow
notices now use ordinary log output so successful no-op, auto-fix, and bootstrap paths
do not create check annotations.

## v0.1.2 release-policy correction

Protected-main commit `ab18ba209626ef178bde095f4165254366db41f7`
removed the session-only `goal.md`. It changed no import-package source, packaged
schema, license, readme, or project metadata. Its `fix` Conventional Commit type
nevertheless caused python-semantic-release to prepare version commit
`c6b12097f20fa5bbac251c5960e0d4dc3cee5eca` and publish v0.1.2 in release run
[`31209049372`](https://github.com/netopsengineer/agents-md-compiler/actions/runs/31209049372).
The package publication was unnecessary because the pushed change set contained no
published package input.

The same run exposed a separate ordering defect. The v0.1.2 annotated tag was
created at `2026-08-07T18:56:37Z`, and the GitHub release was published at
`2026-08-07T18:56:44Z`. PyPI accepted the wheel at
`2026-08-07T19:00:39.247691Z` and the sdist at
`2026-08-07T19:00:40.760264Z`. Publication ultimately succeeded, but the public
tag and release existed about four minutes before the registry accepted the
artifacts. That violated the ordered release contract even though the run ended
green. The evidence is preserved in the
[v0.1.2 release](https://github.com/netopsengineer/agents-md-compiler/releases/tag/v0.1.2)
and [PyPI v0.1.2 JSON](https://pypi.org/pypi/agents-md-compiler/0.1.2/json).

The corrected workflow applies these controls:

- Automatic eligibility classifies every path in the exact GitHub push range.
  Only `LICENSE`, `README.md`, `pyproject.toml`, and paths under
  `src/agents_md_compiler/` can enable automatic package publication.
- Repository-only pushes resolve to a successful no-op before the release gate.
  An intentional repository-only publication requires an explicit `release`
  dispatch and operator-selected semantic level.
- The prepared `chore(release): VERSION` push resolves to a no-op. Its originating
  run alone owns the exact-commit gate, build, publication, and finalization, so a
  second workflow run cannot prepare another version while the first is incomplete.
- python-semantic-release may stage only `CHANGELOG.md`, `pyproject.toml`, and
  `uv.lock`. Its pinned action receives `commit: false`, `tag: false`,
  `push: false`, and `vcs_release: false`.
- The workflow rejects every other staged, unstaged, or untracked path, checks the
  stable version and unchanged remote main, creates the canonical release commit,
  and pushes it without force through the short-lived Release App token.
- The exact prepared version commit must pass the aggregate gate before the
  artifact is built or published.
- The shared `finalize` job depends on successful PyPI publication before creating
  either the tag or GitHub release.
- A `recover` dispatch handles the narrow case where PyPI accepted the artifacts
  but GitHub finalization failed. It verifies the exact prepared main commit and
  public wheel and sdist bytes, digests, and Trusted Publisher provenance, then
  finalizes without rebuilding or republishing.
- `scripts/check_pins.py` and mutation tests enforce these boundaries offline.

No dependency version or immutable Action SHA changed. Pre-merge verification on
2026-08-07 passed the complete 24-hook aggregate gate. The exact inline recovery
verifier also downloaded both public v0.1.2 files, matched their PyPI SHA-256
digests, and matched each attested subject and Trusted Publisher identity. A fresh
0.1.2 sdist and wheel passed strict metadata and archive-content inspection; the
sdist-derived and direct wheels matched across all 27 members; and the wheel passed
the complete smoke suite in clean CPython 3.14.6. Post-merge operational evidence
is not claimed by this pre-merge correction record.

## v1.0.0 tagless output failure and repair

Merge commit `ad6d39961a0a5bb01cb6e8b926621d4372a441de` passed every
required pull request check. Release run
[`31435748482`](https://github.com/netopsengineer/agents-md-compiler/actions/runs/31435748482)
then classified the breaking change as version 1.0.0, passed the source-commit
gate, and invoked the pinned python-semantic-release v10.6.1 action with
`commit: true`, `tag: false`, and `push: true`.

The action created and pushed prepared commit
`daa600d2e4c937eabfca6d0229ee41409f8c27eb`, then failed while serializing its
GitHub Actions outputs with `some required outputs were not set: commit_sha`.
The pinned implementation assigns `commit_sha` only inside its tag-creation
branch, while its persistent output mode requires that value after a commit.
Disabling tags therefore made the action fail after its protected-main write.
The commit changed only `project.version` and the root package version in
`uv.lock` from 0.1.2 to 1.0.0; it did not change `CHANGELOG.md`.

The failure happened before artifact build or publication. PyPI remained at
0.1.2, and no `v1.0.0` tag or GitHub Release existed. The prepared commit's
independent validate, security, and release no-op runs all passed. Recovery was
not dispatched because the recovery contract applies only after successful PyPI
publication.

An exact retry-state probe added the reviewed fix commit after `daa600d2`, then
ran v10.6.1 with action-equivalent stage-only flags and no credentials. It again
computed 1.0.0 and emitted the expected release notes, but staged no file because
the failed commit had already written both version sources. The workflow's
release-without-staged-changes guard would correctly reject that state.

The reviewed repair therefore restores only `project.version` and the root
package version in `uv.lock` to the last public tag, 0.1.2. This restoration is
permitted only because live checks proved that PyPI 1.0.0, tag `v1.0.0`, and the
GitHub Release were absent. The failed commit remains in history. On the repaired
run, stage-only semantic-release must rewrite those exact two paths to 1.0.0 and
the repository-owned step must create a fresh prepared commit from them.

A decisive second retry-state probe used that restoration. Pinned v10.6.1
reported `released=true`, `version=1.0.0`, and an empty `commit_sha`; left `HEAD`
unchanged; staged exactly `pyproject.toml` and `uv.lock`, with only the two
0.1.2-to-1.0.0 version changes; left no unstaged or untracked file; and created
no tag. The staged-path, version, and whitespace validations all accepted the
result. No push was attempted.

The repaired path keeps the current immutable v10.6.1 pin but uses its supported
stage-only mode. The repository-owned step validates the exact staged files and
version, proves remote main did not change, creates the canonical prepared
commit, pushes without force, confirms the remote commit identity, and rechecks
that no release tag exists. The prepared commit must still pass the full gate
before build and publication, and finalization remains post-PyPI only.

Live re-verification on 2026-08-10 found v10.6.1 as both the latest upstream
release and newest tag. Its annotated tag resolves to the existing immutable
workflow SHA `39dd2052f2ce8282a5d932c31d58a2ca06d2550e`. OSV returned no
advisory for the selected action or PyPI package. No dependency or Action pin
changed in this repair.

An isolated no-push probe ran the exact v10.6.1 package with action-equivalent
flags against `ad6d39961a0a5bb01cb6e8b926621d4372a441de`. Stage-only
execution reported `released=true`, `version=1.0.0`, and an empty `commit_sha`;
left `HEAD` unchanged; staged only `pyproject.toml` and `uv.lock`; left no
unstaged files; and created no local tag. This reproduces the otherwise
problematic empty output while proving that the repository-owned preparation
step can safely consume the stage-only result before any remote write.

## Deferred non-blocking controls

| Item                      | Status and dependency                                             |
|---------------------------|-------------------------------------------------------------------|
| Dependabot auto-merge     | Deliberately disabled until all separate prerequisites are tested |
| TestPyPI rehearsal        | Operator-declined; no TestPyPI project or publisher is configured |
| Publish egress block mode | Needs an observed and reviewed complete endpoint allowlist        |
| Blocking `uv audit`       | Waits for the upstream interface to leave experimental status     |

These controls are not release gates under the recorded decisions. Required local,
pull request, protected-main, artifact, publication, provenance, public-install, and
semantic no-op gates are green.

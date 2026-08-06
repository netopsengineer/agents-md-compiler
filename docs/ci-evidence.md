# CI, security, and release workflow evidence

Verification record for the workflows in `.github/`. Every external reference was
re-resolved live immediately before the workflows were written, and again after, as a
check that nothing moved during authoring. Point-in-time tables elsewhere are evidence
of a past verification, not permission to skip a current one.

Verification date: 2026-08-06. Tooling: `gh api` against
`repos/{owner}/{repo}/releases/latest`, `/tags`, and `/git/refs/tags/{tag}`, with
annotated tags resolved through `/git/tags/{object}` to their commit.

## Action pins

Ten unique action references across 51 use sites. Every pinned SHA equals the commit
its `# frozen:` comment names, and every pin is the newest release and newest tag for
its repository at the verification date.

| Action                                            | Tag       | Commit SHA                                 | Use sites | Live verdict |
|---------------------------------------------------|-----------|--------------------------------------------|-----------|--------------|
| `step-security/harden-runner`                     | `v2.20.1` | `b09bb98e06d4d774595224525879c09bc6e98c40` | 18        | match        |
| `actions/checkout`                                | `v7.0.1`  | `3d3c42e5aac5ba805825da76410c181273ba90b1` | 14        | match        |
| `astral-sh/setup-uv`                              | `v9.0.0`  | `c771a70e6277c0a99b617c7a806ffedaca235ff9` | 10        | match        |
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

Negative-path evidence, run against synthetic files outside the repository:
a mutable `uses:` tag, a mutable hook `rev:`, and a SHA with no tag comment each
produced exit code 1 with the offending path and line named.

## Effective permissions

Every workflow declares `permissions: contents: read` at the top level, so any job
without its own block is read-only. Elevations, in full:

| Workflow            | Job                  | Permissions                                                 | Why                                                  |
|---------------------|----------------------|-------------------------------------------------------------|------------------------------------------------------|
| `release.yml`       | `publish`            | `id-token: write` only                                      | Mints the OIDC token PyPI exchanges for upload       |
| `release.yml`       | `finalize-bootstrap` | `contents: write`                                           | Creates the release tag and the GitHub release       |
| `security-scan.yml` | `osv-scanner`        | `actions: read`, `contents: read`, `security-events: write` | Uploads SARIF to code scanning                       |
| `security-scan.yml` | `dependency-review`  | `contents: read`, `pull-requests: write`                    | Posts the failure summary comment                    |
| `auto-fix.yml`      | `fix`                | `contents: write`                                           | Pushes the mechanical fix commit to a feature branch |

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

The bootstrap path creates the tag and the GitHub release only after PyPI accepts the
upload, and refuses to move a tag that already exists. Re-running after a transient
GitHub failure is therefore safe and does not republish.

## Validation results

Run on 2026-08-06 against the staged initial tree after the approved uv 0.12.2
update. GitHub Actions must reproduce these results on the exact commit selected
for bootstrap publication.

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

Artifact inventory from the fresh local build:

| Artifact                                    | Size  | SHA-256                                                            |
|---------------------------------------------|-------|--------------------------------------------------------------------|
| `agents_md_compiler-0.1.0-py3-none-any.whl` | 75895 | `1c2a667d133ff06c9650736a7d890a8b7613c977cefa9e64ad970fef5bfe3bb7` |
| `agents_md_compiler-0.1.0.tar.gz`           | 66105 | `ad28009e55e1546af0a31dbafc8e074ea9a4393b2a5dc404337f739d4072b802` |

The wheel was installed with cache disabled into a clean CPython 3.14.6
environment. The environment contained only `agents-md-compiler==0.1.0`, both
documented invocation forms worked, all 51 public API names imported, and
`scripts/smoke.sh` reported `smoke: all assertions passed`.

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

Verified through the active `netopsengineer` session on 2026-08-06:

- public repository `netopsengineer/agents-md-compiler` exists;
- `compiler-release-bot` is owned by `netopsengineer`, has Metadata read and
  Contents read/write permissions only, and is installed only on this
  repository;
- repository secrets `RELEASE_APP_ID` and `RELEASE_APP_PRIVATE_KEY` exist;
- protected environment `pypi` requires review by `netopsengineer`;
- repository variable `SEMANTIC_RELEASE_ENABLED` is absent;
- secret scanning and push protection are enabled.

Every repository, App, secret, and environment operation used the active
`netopsengineer` account.

## Not verified, and why

| Item                                                   | Blocking dependency                                        |
|--------------------------------------------------------|------------------------------------------------------------|
| GitHub Actions workflow results                        | Requires the initial commit and push                       |
| Branch protection and required status checks           | Requires check contexts from the first workflow run        |
| Pending PyPI Trusted Publisher identity                | Requires authenticated PyPI project access                 |
| Dependabot auto-merge                                  | Deliberately disabled until all prerequisites are tested   |
| SARIF upload to code scanning                          | Requires the first public-repository security workflow run |
| First semantic-release run proving an idempotent no-op | Requires `v0.1.0` to exist                                 |

Because no workflow has run, every statement above is about workflow definitions,
pins, permissions, and locally reproduced gates. It does not claim that a GitHub
Actions run has succeeded.

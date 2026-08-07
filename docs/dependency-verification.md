# Dependency verification

Phase 1 record. Every version, commit SHA, and advisory result below was
resolved live on the recorded date. Nothing here comes from model memory or from
the execution plan's point-in-time tables, which are treated as evidence to
re-check rather than as permission to skip verification.

- Project dependency verification date: 2026-08-06
- One-off release verifier and public identity update: 2026-08-07
- Tools used: `curl` against PyPI and npm JSON endpoints, `gh api` against the
  GitHub REST API, `osv_scan.py` against `api.osv.dev`, `WebFetch` against
  official documentation and release notes
- GitHub API account: `netopsengineer` (read-only dependency queries)

## Runtime floor

| Claim                         | Source                                  | Finding                            |
|-------------------------------|-----------------------------------------|------------------------------------|
| Python 3.14 is supported      | <https://devguide.python.org/versions/> | Status `bugfix`, EOL `2030-10`     |
| Python 3.15 status            | <https://devguide.python.org/versions/> | Status `prerelease`, EOL `2031-10` |
| Python 3.14 available locally | `uv python list`                        | `cpython-3.14.6-macos-aarch64`     |
| Python 3.15 available locally | `uv python list`                        | `cpython-3.15.0b4` (download)      |

Decision: keep `requires-python = ">=3.14"`. No runtime-floor decision is
needed. The Phase 8 Python 3.15 job remains an allow-failure prerelease signal
because 3.15 is still prerelease; reclassify it once 3.15 reaches `bugfix`.

## Selected Python package versions

Every version is the newest release on PyPI and agrees with the upstream
repository's newest release tag. Both `/releases/latest` and `/tags` were checked
for every repository.

| Package                   | Selected  | PyPI released        | Upstream newest tag | Plan table | Delta   |
|---------------------------|-----------|----------------------|---------------------|------------|---------|
| `uv` (CLI, not a dep)     | `0.12.2`  | 2026-08-05T19:20:52Z | `0.12.2`            | `0.12.1`   | upgrade |
| `uv-build` / `uv_build`   | `0.12.2`  | 2026-08-05T19:20:38Z | `0.12.2`            | `0.12.1`   | upgrade |
| `jsonschema`              | `4.26.0`  | 2026-01-07T13:41:05Z | `v4.26.0`           | missing    | add     |
| `prek`                    | `0.4.12`  | 2026-08-03T11:28:09Z | `v0.4.12`           | `0.4.12`   | none    |
| `pydoclint`               | `0.9.1`   | 2026-07-03T08:17:07Z | `0.9.1`             | `0.9.1`    | none    |
| `pyright`                 | `1.1.411` | 2026-06-25T02:14:04Z | `v1.1.411`          | `1.1.411`  | none    |
| `pytest`                  | `9.1.1`   | 2026-06-19T10:58:31Z | `9.1.1`             | `9.1.1`    | none    |
| `pytest-cov`              | `7.1.0`   | 2026-03-21T20:11:14Z | `v7.1.0`            | `7.1.0`    | none    |
| `pytest-mock`             | `3.15.1`  | 2025-09-16T16:37:25Z | `v3.15.1`           | `3.15.1`   | none    |
| `pytest-xdist`            | `3.8.0`   | 2025-07-01T13:30:56Z | `v3.8.0`            | `3.8.0`    | none    |
| `ruff`                    | `0.16.1`  | 2026-07-30T19:36:13Z | `0.16.1`            | `0.16.1`   | none    |
| `bandit`                  | `1.9.4`   | 2026-02-25T06:44:13Z | `1.9.4`             | `1.9.4`    | none    |
| `python-semantic-release` | `10.6.1`  | 2026-07-06T06:14:33Z | `v10.6.1`           | `10.6.1`   | none    |
| `twine`                   | `7.0.0`   | 2026-07-27T15:58:59Z | `7.0.0`             | `6.2.0`    | upgrade |

`pytest-dev/pytest-cov` publishes no GitHub releases; its newest tag `v7.1.0`
matches PyPI. `microsoft/pyright` release `1.1.411` matches the
`RobertCraigie/pyright-python` wrapper release `v1.1.411`. GitHub returns the
`/tags` array unsorted for `pytest-dev/pytest`, `astral-sh/ruff`, and
`pypa/twine`, so the release endpoint is authoritative for those three and the
tag list was used only to confirm the selected version exists as a tag.

### twine: 6.2.0 -> 7.0.0

- Risk level: DEPRECATION
- Verified via: `curl https://pypi.org/pypi/twine/json`,
  `gh api repos/pypa/twine/releases/latest`, and
  <https://raw.githubusercontent.com/pypa/twine/main/docs/changelog.rst>
- What changed: 7.0.0 removes support for the never-standardized Core Metadata
  2.0 and adds support for uploading packages whose metadata version is 2.5.
  Other changes are bug fixes: explicit UTF-8 when reading `.pypirc`,
  subdependencies in `--version` output, a `rich` bump for environment-specific
  hangs, and gentler handling of non-standard index HTTP responses.
- Breaking changes: yes, for artifacts declaring Core Metadata 2.0 only. This
  project emits no such artifact.
- Migration steps: version bump only.
- Security advisories: none found for `twine@7.0.0` via the OSV batch scan.
- Recommendation: select 7.0.0 because it is the current release with no
  advisories and no migration cost for this project.
- Correction, recorded 2026-08-04 after Phase 8 built the real artifacts: an
  earlier draft of this entry claimed metadata 2.5 acceptance was load-bearing
  here. It is not. `uv_build` 0.12.1 emits `Metadata-Version: 2.4` for this
  project, confirmed by reading `METADATA` out of the built wheel, so twine 6.2.0
  would also have accepted the artifact. The 7.0.0 selection stands on being
  current; the metadata-2.5 rationale was wrong and is withdrawn. The same
  evidence settles `PY-PKG-011`: `[project].import-names` is conditioned on a
  backend that emits Core Metadata 2.5, so it does not apply to this project yet.
- Your call: none required. The plan explicitly authorizes replacing its
  point-in-time twine pin when Phase 1 verifies a newer safe release, and this
  record makes the replacement consistent everywhere the version appears.
- Follow-up: the exact Core Metadata version emitted by `uv_build` 0.12.1 is not
  asserted here. Phase 9 reads it from the built artifact and records it.

### uv and uv-build: 0.12.1 -> 0.12.2

- Risk level: ROUTINE
- Verified via: the PyPI JSON API, both GitHub `/releases/latest` and `/tags`,
  the exact `0.12.2` tag, official uv release notes and build-backend
  documentation, and the OSV API on 2026-08-06.
- What changed: CPython patch-version availability, diagnostics, preview
  features, compatibility fixes, performance improvements, and documentation.
- Breaking changes: none documented for this repository's commands or build
  configuration.
- Migration steps: move the build-system lower bound, workflow `UV_VERSION`
  values, and semantic-release build pin together; then rerun the lock, local,
  CI, and artifact gates.
- Security advisories: none found for `uv@0.12.2` or `uv-build@0.12.2` through
  the OSV batch scan.
- Recommendation: select 0.12.2 because it is the current patch release and the
  official build-backend documentation now recommends
  `uv_build>=0.12.2,<0.13`.
- Your call: approved by the operator on 2026-08-06.

### jsonschema: missing record corrected

`jsonschema` was already selected and locked at 4.26.0, but the prior evidence
table omitted it. Live re-verification found PyPI 4.26.0, upstream tag
`v4.26.0`, and no OSV advisory for the selected version. The omission is
corrected without changing the dependency.

### Every other package: already current

Confirmed current against both PyPI and the upstream release and tag endpoints.
No changelog delta exists between the plan's recorded version and the selected
version, so no migration analysis applies.

`prek` 0.4.12 was inspected because the baseline project used 0.4.11. Its
release notes list enhancements (a `--require-group` parameter, fast-path and
builtin hook alignment, stable verbose file ordering, `uv` installs from the
Astral CDN with checksum verification, `prek install --force` bypassing external
hook paths), performance work, one bug fix (full object IDs in diff snapshots),
and documentation. No breaking change and no migration step applies to this
project's use.

### One-off PEP 740 verifier

`pypi-attestations` is not a project dependency and was not added to
`pyproject.toml` or `uv.lock`. Version 0.0.30 was selected only for independent
post-publication verification of the two release files.

| Check                        | Source                                                                     | Finding                                                  |
|------------------------------|----------------------------------------------------------------------------|----------------------------------------------------------|
| Latest PyPI version          | <https://pypi.org/pypi/pypi-attestations/json>                             | `0.0.30`                                                 |
| Latest GitHub release        | <https://api.github.com/repos/pypi/pypi-attestations/releases/latest>      | `v0.0.30`, published 2026-07-28                          |
| Latest GitHub tag            | <https://api.github.com/repos/pypi/pypi-attestations/tags?per_page=5>      | `v0.0.30`                                                |
| Tag ref                      | <https://api.github.com/repos/pypi/pypi-attestations/git/ref/tags/v0.0.30> | direct commit `845bfac2f2912912fb2d1ab96775ac75708279c4` |
| Exact-version advisory query | <https://api.osv.dev/v1/query>                                             | PyPI `pypi-attestations` 0.0.30, zero advisories         |

Both GitHub `/releases/latest` and `/tags` agree with PyPI. The tag is lightweight
and points directly to the recorded commit, so no annotated tag object required
dereferencing. The exact invocation was isolated through
`uvx --from pypi-attestations==0.0.30`; it returned `OK` for both public
`agents-md-compiler` 0.1.0 distribution URLs when constrained to repository identity
`https://github.com/netopsengineer/agents-md-compiler`.

## Build backend decision

| Claim                                    | Source                                                | Finding                                                                                                                    |
|------------------------------------------|-------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Recommended `uv_build` requirement       | <https://docs.astral.sh/uv/concepts/build-backend/>   | `requires = ["uv_build>=0.12.2,<0.13"]`                                                                                    |
| uv 0.12.0 backend configuration breakage | <https://github.com/astral-sh/uv/releases/tag/0.12.0> | "no breaking changes to the configuration"                                                                                 |
| uv 0.12.2 breakage                       | <https://github.com/astral-sh/uv/releases/tag/0.12.2> | No breaking changes documented                                                                                             |
| Backend file-selection keys              | <https://docs.astral.sh/uv/concepts/build-backend/>   | `module-name`, `module-root`, `source-include`, `source-exclude`, `wheel-exclude`, `data`, `namespace`, `default-excludes` |

Decision: `requires = ["uv_build>=0.12.2,<0.13"]` with `build-backend = "uv_build"`.
The baseline project's `>=0.11.28,<0.12` bound is deliberately not copied; the uv
0.12.0 notes instruct projects with an upper bound to admit 0.12. The baseline
project is not modified by this work.

The documented `source-exclude` and `wheel-exclude` keys are the mechanism for
keeping `codex-global-agents-compiler-execution-plan.md` and `goal.md` in Git but
out of both distributions. Phase 2 configures them and Phase 9 verifies the
actual archive contents rather than trusting the configuration.

uv 0.12.0 breaking changes were reviewed against this project's command set
(`uv lock`, `uv sync --locked`, `uv run`, `uv build`, `uv audit`, `uvx`). None
applies: the relevant items concern `uv init` layout defaults (this project uses
the new packaged default), sdist/wheel archive formats and compression, wheels
with interpreter-named entry points, prerelease resolution mode, `--require-hashes`
enforcement, `pylock.toml` validation, `SSL_CERT_FILE` strictness, `uv run`
script-relative project discovery, `uv venv --clear`, and `uv add` path
preservation.

## Audit interface

`uv audit` is directly invocable in the selected uv 0.12.2 without a feature
gate, but its interface remains experimental. The command warns that it may
change without notice. `uv audit --help` documents `--frozen`, `--locked`,
`--output-format text|json|sarif`, and dependency-group selectors. This project
uses `uv audit --frozen`, which is the canonical default in `PY-SEC-006`; CI
keeps that command advisory while the blocking OSV job covers the same lock, as
recorded in `docs/ci-evidence.md`. Reverified by running both `uv audit --help`
and `uv audit --frozen` on uv 0.12.2 on 2026-08-06.

## Advisory results

One batched OSV query covered every exact selected version. The scanner queries
`api.osv.dev`, which aggregates GHSA, PYSEC, and CVE data.

### harden-runner: v2.20.0 -> v2.20.1

**Risk level:** ROUTINE

**Verified via:** GitHub REST API
[`/releases/latest`](https://api.github.com/repos/step-security/harden-runner/releases/latest),
[`/tags`](https://api.github.com/repos/step-security/harden-runner/tags), and
[`/git/ref/tags/v2.20.1`](https://api.github.com/repos/step-security/harden-runner/git/ref/tags/v2.20.1),
plus the OSV batch scanner on 2026-08-05.

**What changed:** `v2.20.1` adds AWS CodeBuild-hosted runner support and
implicitly permits single-label internal domains in block mode. This repository
uses audit mode, so the block-mode behavior does not change its configured
enforcement.

**Breaking changes:** None documented for this repository's use. The release is
a direct patch upgrade from `v2.20.0`.

**Migration steps:** Replace all 18 workflow use sites with commit
`b09bb98e06d4d774595224525879c09bc6e98c40`, update each frozen-tag comment,
and rerun the workflow, pin, and aggregate gates.

**Security advisories:** The OSV batch scan returned five advisories for manual
GitHub Action range review. Every affected range is fixed before `v2.20.0`, so
neither `v2.20.0` nor `v2.20.1` is affected. The exact ranges remain recorded
below.

**Recommendation:** Upgrade to the current patch release because it has no
applicable advisory or migration cost for this repository.

**Your call:** Approved by the operator on 2026-08-05.

| Scope                             | Specs | Result                    |
|-----------------------------------|-------|---------------------------|
| Selected PyPI dependency versions | 14    | 0 with advisories         |
| GitHub Actions pins               | 12    | 3 requiring manual review |
| Hook tool releases                | 6     | 0 with advisories         |
| npm hook packages                 | 2     | 0 with advisories         |

PyPI, clean: `uv@0.12.2`, `uv-build@0.12.2`, `jsonschema@4.26.0`,
`prek@0.4.12`, `pydoclint@0.9.1`, `pyright@1.1.411`, `pytest@9.1.1`,
`pytest-cov@7.1.0`, `pytest-mock@3.15.1`, `pytest-xdist@3.8.0`,
`ruff@0.16.1`, `bandit@1.9.4`, `python-semantic-release@10.6.1`,
`twine@7.0.0`.

Hook tools, clean: `go:github.com/gitleaks/gitleaks@v8.30.1`,
`go:github.com/rhysd/actionlint@v1.7.12`, `cargo:zizmor@1.29.0`,
`pypi:zizmor@1.29.0`, `pypi:pre-commit-hooks@6.0.0`,
`pypi:sync-pre-commit-deps@0.0.5`.

npm, clean: `markdownlint-cli2@0.23.2`, `markdown-table-formatter@1.7.0`.

### GitHub Actions advisories reviewed against the selected pins

OSV keys Action advisories on release semver and cannot range-match a pinned
tag, so each listed advisory was compared to the selected pin by hand.

| Advisory                                 | Severity | Affected range                  | Pin       | Verdict      |
|------------------------------------------|----------|---------------------------------|-----------|--------------|
| `GHSA-46g3-37rh-v698` / `CVE-2026-32947` | MODERATE | fixed 2.16.0                    | `v2.20.1` | not affected |
| `GHSA-g699-3x6g-wm3g` / `CVE-2026-32946` | MODERATE | fixed 2.16.0                    | `v2.20.1` | not affected |
| `GHSA-cpmj-h4f6-r6pq` / `CVE-2026-25598` | MODERATE | fixed 2.14.2                    | `v2.20.1` | not affected |
| `GHSA-mxr3-8whj-j74r` / `CVE-2025-32955` | MODERATE | introduced 0.12.0, fixed 2.12.0 | `v2.20.1` | not affected |
| `GHSA-g85v-wf27-67xc` / `CVE-2024-52587` | LOW      | fixed 2.10.2                    | `v2.20.1` | not affected |
| `GHSA-cxww-7g56-2vh6`                    | HIGH     | introduced 4.0.0, fixed 4.1.3   | `v8.0.1`  | not affected |
| `GHSA-vxmw-7h4f-hqxh`                    | LOW      | fixed 1.13.0                    | `v1.14.2` | not affected |

The first five apply to `step-security/harden-runner`, the sixth to
`actions/download-artifact`, and the seventh to `pypa/gh-action-pypi-publish`.
Every fixed version precedes the selected pin, so no unresolved applicable
advisory remains. All other scanned Actions returned clean.

## Immutable pre-commit hook revisions

Each `rev` is the commit SHA that the named tag resolves to. Annotated tags were
dereferenced through `repos/{repo}/git/tags/{sha}` to reach the commit; the
`objtype` column records what the tag ref pointed at.

| Hook repository                   | Tag        | objtype | Commit SHA                                 |
|-----------------------------------|------------|---------|--------------------------------------------|
| `pre-commit/pre-commit-hooks`     | `v6.0.0`   | commit  | `3e8a8703264a2f4a69428a0aa4dcb512790b2c8c` |
| `pre-commit/sync-pre-commit-deps` | `v0.0.5`   | commit  | `497f8ebf9d58162bb5898e3973a633d31d511169` |
| `DavidAnson/markdownlint-cli2`    | `v0.23.2`  | commit  | `b82a6c8896e491b9cb377a99ff3412131920681b` |
| `astral-sh/ruff-pre-commit`       | `v0.16.1`  | commit  | `39d9ac5938dadb73df0564a45f163e25ff9fa6e2` |
| `jsh9/pydoclint`                  | `0.9.1`    | commit  | `cf8f4d0b81f933ebc5414acadcf7acdab705baa1` |
| `RobertCraigie/pyright-python`    | `v1.1.411` | commit  | `392b6ba8e54be6d603e02e9f8d601d27b7a48d12` |
| `PyCQA/bandit`                    | `1.9.4`    | commit  | `92ae8b82fb422a639f0ed8d99e96cea769594e08` |
| `gitleaks/gitleaks`               | `v8.30.1`  | commit  | `83d9cd684c87d95d656c1458ef04895a7f1cbd8e` |
| `rhysd/actionlint`                | `v1.7.12`  | commit  | `914e7df21a07ef503a81201c76d2b11c789d3fca` |
| `zizmorcore/zizmor-pre-commit`    | `v1.29.0`  | commit  | `451b56af716f9f0d0c2b816503a3fd0cf8b036fa` |

`zizmorcore/zizmor-pre-commit` `v1.29.0` was published 2026-08-01 and matches
the upstream `zizmorcore/zizmor` release `v1.29.0`. The baseline project's
`v1.28.0` pin is superseded.

The `ruff-pre-commit` tag `v0.16.1` matches the `ruff` version selected in
`[dependency-groups]`, so the hook and the direct `uv run ruff` command enforce
the same rule set.

## Immutable GitHub Action revisions

| Action                                            | Tag       | objtype | Commit SHA                                 |
|---------------------------------------------------|-----------|---------|--------------------------------------------|
| `step-security/harden-runner`                     | `v2.20.1` | commit  | `b09bb98e06d4d774595224525879c09bc6e98c40` |
| `actions/checkout`                                | `v7.0.1`  | commit  | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `astral-sh/setup-uv`                              | `v9.0.0`  | commit  | `c771a70e6277c0a99b617c7a806ffedaca235ff9` |
| `actions/setup-python`                            | `v7.0.0`  | commit  | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact`                         | `v7.0.1`  | commit  | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `actions/download-artifact`                       | `v8.0.1`  | commit  | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` |
| `actions/github-script`                           | `v9.0.0`  | tag     | `3a2844b7e9c422d3c10d287c895573f7108da1b3` |
| `actions/create-github-app-token`                 | `v3.2.0`  | commit  | `bcd2ba49218906704ab6c1aa796996da409d3eb1` |
| `python-semantic-release/python-semantic-release` | `v10.6.1` | tag     | `39dd2052f2ce8282a5d932c31d58a2ca06d2550e` |
| `pypa/gh-action-pypi-publish`                     | `v1.14.2` | tag     | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` |
| `google/osv-scanner-action`                       | `v2.3.8`  | commit  | `9a498708959aeaef5ef730655706c5a1df1edbc2` |
| `dependabot/fetch-metadata`                       | `v3.1.0`  | commit  | `25dd0e34f4fe68f24cc83900b1fe3fe149efef98` |

`pypa/gh-action-pypi-publish` `v1.14.2` supersedes the baseline project's
`v1.14.1`. Every Action written into a workflow must carry the SHA above plus a
comment naming the tag in this table. Phase 8 reverifies these SHAs immediately
before enabling bootstrap dispatch, because a tag can be moved after this record
is written.

## Dependabot label verification

Verified on 2026-08-07 against GitHub's current
[Dependabot customization documentation][dependabot-label-docs].
When the `labels` option is omitted, Dependabot applies the `dependencies` and
ecosystem labels and creates those default labels if they do not exist. A custom
`labels` option overrides that behavior and introduces a separate repository-label
provisioning dependency.

The live
[repository label inventory][repository-labels]
contained the nine standard repository labels and no Dependabot-created labels.
The repository had no current or historical Dependabot pull request, so the first
Dependabot update must be allowed to create its defaults. `.github/dependabot.yml`
therefore omits `labels`, and the aggregate `check-pins` gate rejects any future
active `labels` key. This control changes no package version, Action pin, or runtime
dependency, so release and tag resolution and an OSV advisory query are not
applicable to this finding.

| Claim                                     | Tool                 | Source                                               | Finding                                    |
|-------------------------------------------|----------------------|------------------------------------------------------|--------------------------------------------|
| Default labels are created automatically  | Official GitHub docs | [Official label behavior][dependabot-label-docs]     | Omit `labels` to retain automatic creation |
| Current repository label inventory        | GitHub public page   | [Repository labels][repository-labels]               | Nine standard labels; no Dependabot labels |
| Current Dependabot pull request inventory | GitHub public search | [Dependabot pull requests][dependabot-pull-requests] | No open or closed Dependabot pull requests |
| Dependency or Action version change       | Local diff           | Configuration and checker changes                    | None                                       |
| Package advisory scan requirement         | Dependency inventory | No package or Action version was introduced          | Not applicable                             |

[dependabot-label-docs]: https://docs.github.com/en/enterprise-cloud@latest/code-security/tutorials/secure-your-dependencies/customizing-dependabot-prs#labeling-pull-requests-with-custom-labels
[dependabot-pull-requests]: https://github.com/netopsengineer/agents-md-compiler/pulls?q=is%3Apr+author%3Aapp%2Fdependabot
[repository-labels]: https://github.com/netopsengineer/agents-md-compiler/labels

## Public identity

| Check                                         | Method                                                          | Result                                      |
|-----------------------------------------------|-----------------------------------------------------------------|---------------------------------------------|
| `agents-md-compiler` on PyPI                  | `GET https://pypi.org/pypi/.../json`                            | HTTP 200, version 0.1.0 published           |
| `agents_md_compiler` normalized form on PyPI  | `GET https://pypi.org/pypi/.../json`                            | same active project                         |
| `codex-global-agents-compiler` on PyPI        | `GET https://pypi.org/pypi/.../json`                            | HTTP 404, unclaimed                         |
| `netopsengineer/agents-md-compiler` on GitHub | `gh api repos/netopsengineer/agents-md-compiler`                | HTTP 200, public repository exists          |
| Owner account exists                          | `gh api repos/netopsengineer/agent-skill-description-optimizer` | public repository owned by `netopsengineer` |

PyPI normalizes `-` and `_` to the same project name. Both spellings were HTTP 404
before the pending Trusted Publisher was configured and were rechecked before
bootstrap. Protected OIDC publication created the project on 2026-08-07; both now
resolve to the active 0.1.0 project. `codex-global-agents-compiler` is a distinct
normalized name and remains unclaimed.

The GitHub query ran under the active `netopsengineer` account. The repository
was created and configured under that owner on 2026-08-06.

## Acceptance criteria

| Criterion                                                         | Result                                          |
|-------------------------------------------------------------------|-------------------------------------------------|
| No external version or Action SHA came from model memory          | pass                                            |
| Both release and tag sources checked for each dependency          | pass                                            |
| Every exact selected dependency has a recorded advisory result    | pass                                            |
| Every Action and hook uses an immutable SHA with a verified tag   | pass                                            |
| Dependabot retains automatic default-label creation               | pass on 2026-08-07                              |
| No unresolved version placeholder remains                         | pass                                            |
| Package-name availability rechecked before Phase 15               | pass; project created by protected OIDC publish |
| One-off PEP 740 verifier version and advisory status live-checked | pass on 2026-08-07                              |

State reached: `DEPENDENCIES_VERIFIED`.

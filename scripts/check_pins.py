"""Assert immutable automation pins and release workflow boundaries.

Eight things must hold for this repository's supply chain (PY-CI-003, PY-SEC-009):

- every ``uses:`` in a workflow names a 40-hex commit SHA, with a trailing comment
  recording which tag that SHA came from; and
- every third-party ``rev:`` in ``.pre-commit-config.yaml`` is a 40-hex commit SHA
  carrying a ``# frozen: <tag>`` comment; and
- ``.github/dependabot.yml`` omits custom ``labels`` so GitHub creates and applies
  Dependabot's default labels without repository provisioning; and
- every Dependabot ecosystem belongs to one daily lockstep group with the required
  seven-day supply-chain cooldown; and
- every ``setup-uv`` step installs the exact ``uv`` package from ``uv.lock``; and
- the project carries one Dependabot-owned exact ``uv`` bootstrap requirement; and
- the auto-merge workflow binds a verified Dependabot pull request to the exact
  successfully validated head without checking out pull-request content; and
- ``release.yml`` preserves the package-change eligibility gate, separates release
  preparation from public finalization, and keeps publication minimal.

These controls are easy to satisfy by hand and easy to lose by accident. An automated
dependency update that rewrites a pin to a mutable tag would silently undo the
guarantee, and a mutable tag can be repointed at different code after review. This
check turns that from something a reviewer has to notice into a gate.

Scope: offline and structural. It proves each pin *is* an immutable SHA carrying a
tag comment and proves the configured automation boundaries remain present. It does
not prove a SHA still corresponds to its tag, which requires network access and is
recorded in docs/dependency-verification.md.

Line-oriented matching is deliberate rather than a YAML parse: tag evidence lives in
comments, while the protected workflow fragments and forbidden Dependabot key are
unambiguous on active YAML lines.
"""

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# `uses: owner/repo[/path]@ref` or `uses: ./local/action`, with an optional comment.
USES = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<target>[^\s#]+)\s*(?:#\s*(?P<comment>.*?))?\s*$"
)

# `rev: ref` with an optional comment, as written under a pre-commit `repo:` entry.
REV = re.compile(r"^\s*rev:\s*(?P<ref>[^\s#]+)\s*(?:#\s*(?P<comment>.*?))?\s*$")

LOCAL_REPO = re.compile(r"^\s*-?\s*repo:\s*local\s*$")

# A tag comment must name a version. `# frozen: v1.2.3` and `# v1.2.3` both qualify;
# a bare `# see below` does not.
VERSION_IN_COMMENT = re.compile(r"v?\d+\.\d+")

# Custom labels override Dependabot's defaults and must be provisioned separately.
# Quoted keys and flow mappings are accepted YAML and must not bypass the gate.
DEPENDABOT_LABELS = re.compile(r"(?:^|[^A-Za-z0-9_-])(?:labels|['\"]labels['\"])\s*:")

WORKFLOW_JOB = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")

SETUP_UV_TARGET = "astral-sh/setup-uv@"
UV_VERSION_FILE = re.compile(r"^\s+version-file:\s*uv\.lock(?:\s*#.*)?$")
UV_VERSION_INPUT = re.compile(r"^\s+version:\s*\S+")
EXACT_UV_REQUIREMENT = re.compile(r'^[ \t]*"uv==\d+\.\d+\.\d+",[ \t]*$')


def check_workflow(path: Path) -> list[str]:
    """Check every ``uses:`` in one workflow file.

    A local action reference (``./path``) is exempt: it resolves inside this
    repository at the commit under test, so it is already immutable.

    Args:
        path: Workflow file to check.

    Returns:
        Human-readable findings, empty when every reference is pinned.
    """
    findings: list[str] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = USES.match(line)
        if match is None:
            continue
        target = match.group("target")
        comment = match.group("comment") or ""
        location = f"{path}:{number}"
        if target.startswith("./"):
            continue
        if "@" not in target:
            findings.append(f"{location}: `{target}` names no ref at all")
            continue
        _, _, ref = target.rpartition("@")
        if not COMMIT_SHA.match(ref):
            findings.append(
                f"{location}: `{target}` is pinned to `{ref}`, which is not a "
                "40-hex commit SHA"
            )
            continue
        if not VERSION_IN_COMMENT.search(comment):
            findings.append(
                f"{location}: `{target}` is pinned to a SHA but its comment "
                f"({comment!r}) does not record the tag it came from"
            )
    return findings


def check_setup_uv_workflow(path: Path) -> list[str]:
    """Require each setup-uv step to consume the canonical lockfile version.

    Args:
        path: Workflow file to check.

    Returns:
        Human-readable findings, empty when every setup-uv step reads ``uv.lock``.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[str] = []
    for index, line in enumerate(lines):
        match = USES.match(line)
        if match is None or not match.group("target").startswith(SETUP_UV_TARGET):
            continue
        indentation = len(line) - len(line.lstrip())
        block: list[str] = []
        for following in lines[index + 1 :]:
            if following.strip():
                following_indentation = len(following) - len(following.lstrip())
                if following_indentation < indentation:
                    break
            block.append(following)
        location = f"{path}:{index + 1}"
        version_files = sum(bool(UV_VERSION_FILE.match(item)) for item in block)
        if version_files != 1:
            findings.append(
                f"{location}: setup-uv must specify exactly one `version-file: "
                "uv.lock` input"
            )
        if any(UV_VERSION_INPUT.match(item) for item in block):
            findings.append(
                f"{location}: setup-uv must not override the lock with `version:`"
            )
    return findings


def check_pre_commit(path: Path) -> list[str]:
    """Check every third-party ``rev:`` in a pre-commit configuration.

    Hooks under ``repo: local`` have no ``rev`` and are skipped. A ``rev`` is
    attributed to the most recent ``repo:`` line above it.

    Args:
        path: Configuration file to check.

    Returns:
        Human-readable findings, empty when every rev is pinned.
    """
    findings: list[str] = []
    local = False
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if LOCAL_REPO.match(line):
            local = True
            continue
        if re.match(r"^\s*-?\s*repo:\s*\S+", line):
            local = False
            continue
        match = REV.match(line)
        if match is None:
            continue
        location = f"{path}:{number}"
        if local:
            findings.append(f"{location}: a `repo: local` entry must not carry a rev")
            continue
        ref = match.group("ref")
        comment = match.group("comment") or ""
        if not COMMIT_SHA.match(ref):
            findings.append(
                f"{location}: rev `{ref}` is not a 40-hex commit SHA. Update hooks "
                "with `prek autoupdate` (this repository sets update.freeze) rather "
                "than accepting a mutable tag"
            )
            continue
        if not VERSION_IN_COMMENT.search(comment):
            findings.append(
                f"{location}: rev `{ref}` is a SHA but its comment ({comment!r}) "
                "does not record the tag it came from"
            )
    return findings


def check_dependabot(path: Path) -> list[str]:
    """Require labels, one daily lockstep group, and supply-chain cooldowns.

    Omitting the key preserves GitHub's documented behavior: Dependabot creates
    its default dependency and ecosystem labels when they do not exist. Custom
    labels require separate repository provisioning and are outside this project's
    configuration contract.

    Args:
        path: Dependabot configuration file to check.

    Returns:
        Human-readable findings, empty when automatic update ownership is intact.
    """
    findings: list[str] = []
    active_lines: list[str] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        active, _, _ = line.partition("#")
        active_lines.append(active)
        if DEPENDABOT_LABELS.search(active):
            findings.append(
                f"{path}:{number}: custom Dependabot `labels` are forbidden; "
                "omit the key so GitHub creates and applies its default labels"
            )
    ecosystems = sum("package-ecosystem:" in line for line in active_lines)
    daily_schedules = sum("interval: daily" in line for line in active_lines)
    lockstep_assignments = sum(
        "multi-ecosystem-group: all-dependencies" in line for line in active_lines
    )
    all_patterns = sum('patterns: ["*"]' in line for line in active_lines)
    cooldowns = sum("cooldown:" in line for line in active_lines)
    cooldown_days = sum("default-days: 7" in line for line in active_lines)
    if not ecosystems or daily_schedules != 1:
        findings.append(
            f"{path}: the lockstep group must define exactly one daily schedule; "
            f"found {daily_schedules}"
        )
    if lockstep_assignments != ecosystems or all_patterns != ecosystems:
        findings.append(
            f'{path}: every package ecosystem must use `patterns: ["*"]` and '
            "`multi-ecosystem-group: all-dependencies`"
        )
    if cooldowns != ecosystems or cooldown_days != ecosystems:
        findings.append(
            f"{path}: every package ecosystem must define a seven-day cooldown"
        )
    return findings


def check_dependabot_auto_merge(path: Path) -> list[str]:
    """Check the trusted Dependabot auto-merge workflow boundary.

    Args:
        path: Auto-merge workflow file to check.

    Returns:
        Human-readable findings, empty when identity, revision, and privilege
        controls remain intact.
    """
    text = path.read_text(encoding="utf-8")
    required = (
        "schedule:",
        'cron: "11,41 * * * *"',
        "workflow_dispatch:",
        "permissions: {}",
        "contents: write",
        "pull-requests: write",
        "gh api --paginate",
        'select(.user.login == "dependabot[bot]")',
        '\'.user.login == "dependabot[bot]" and',
        '.user.type == "Bot"',
        '.state == "open"',
        ".draft == false",
        '.base.ref == "main"',
        ".head.repo.full_name == $repository",
        '(.head.ref | startswith("dependabot/"))',
        'head_sha="$(jq -r \'.head.sha\' <<<"$pull")"',
        '--match-head-commit "$head_sha"',
        "--auto",
        "--squash",
    )
    findings = [
        f"{path}: Dependabot auto-merge is missing required control {fragment!r}"
        for fragment in required
        if fragment not in text
    ]
    forbidden = (
        "pull_request:",
        "pull_request_target:",
        "workflow_run:",
        "actions/checkout@",
        "--admin",
    )
    findings.extend(
        f"{path}: Dependabot auto-merge contains forbidden capability {fragment!r}"
        for fragment in forbidden
        if fragment in text
    )
    return findings


def check_project_uv_ownership(path: Path) -> list[str]:
    """Require one exact uv pin and lock-derived release bootstrapping.

    Args:
        path: Project configuration file to check.

    Returns:
        Human-readable findings, empty when Dependabot owns the sole exact pin.
    """
    text = path.read_text(encoding="utf-8")
    exact_requirements = sum(
        bool(EXACT_UV_REQUIREMENT.match(line)) for line in text.splitlines()
    )
    findings: list[str] = []
    if exact_requirements != 1:
        findings.append(
            f"{path}: [dependency-groups].bootstrap must contain exactly one exact "
            "`uv==X.Y.Z` requirement"
        )
    required = (
        "bootstrap = [",
        'Path("uv.lock").read_text(encoding="utf-8")',
        'package["name"] == "uv"',
        'print(f"uv=={versions[0]}")',
        '"$UV_REQUIREMENT"',
    )
    findings.extend(
        f"{path}: semantic release is missing lock-derived uv control {fragment!r}"
        for fragment in required
        if fragment not in text
    )
    return findings


def _workflow_jobs(text: str) -> dict[str, str]:
    """Return the top-level job blocks from a GitHub Actions workflow.

    Args:
        text: Workflow text.

    Returns:
        Mapping from job identifier to its complete textual block.
    """
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines(keepends=True):
        if line.rstrip() == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        match = WORKFLOW_JOB.fullmatch(line.rstrip("\r\n"))
        if match is not None:
            name = match.group("name")
            current = name
            jobs[name] = [line]
        elif current is not None:
            jobs[current].append(line)
    return {name: "".join(lines) for name, lines in jobs.items()}


def check_release_workflow(path: Path) -> list[str]:
    """Check release ordering and privilege boundaries in ``release.yml``.

    The checks intentionally cover the high-risk invariants whose accidental
    removal could publish an unnecessary package or create a public release claim
    before PyPI accepts the artifact. General YAML validity remains actionlint's
    responsibility.

    Args:
        path: Release workflow file to check.

    Returns:
        Human-readable findings, empty when every release invariant is present.
    """
    text = path.read_text(encoding="utf-8")
    jobs = _workflow_jobs(text)
    findings: list[str] = []

    required_jobs = {
        "resolve",
        "semantic-release",
        "gate-prepared",
        "publish",
        "verify-recovery",
        "finalize",
    }
    findings.extend(
        f"{path}: required release job `{name}` is missing"
        for name in sorted(required_jobs - jobs.keys())
    )
    if findings:
        return findings

    required_fragments = {
        "resolve": (
            "scripts/release_policy.py",
            "steps.package-policy.outputs.package_changes",
            '"chore(release): "*',
        ),
        "semantic-release": (
            "python-semantic-release/python-semantic-release@",
            "commit: false",
            "tag: false",
            "push: false",
            "vcs_release: false",
            "git diff --cached --name-only --no-renames -z",
            "git diff --cached --check",
            "CHANGELOG.md|pyproject.toml|uv.lock) ;;",
            'git push origin "HEAD:refs/heads/main"',
            "git ls-remote origin refs/heads/main",
        ),
        "gate-prepared": (
            "ref: ${{ needs.semantic-release.outputs.commit }}",
            "uv run prek run --all-files --show-diff-on-failure --color always",
            "uv run pytest",
            "uv run pyright",
        ),
        "publish": (
            "id-token: write",
            "pypa/gh-action-pypi-publish@",
            "attestations: true",
        ),
        "verify-recovery": (
            "needs.resolve.outputs.path == 'recover'",
            "https://pypi.org/pypi/agents-md-compiler/",
            "https://pypi.org/integrity/agents-md-compiler/",
        ),
        "finalize": (
            "needs: [resolve, build, publish, verify-recovery]",
            "needs.publish.result == 'success'",
            "needs.verify-recovery.result == 'success'",
            "repos/${GITHUB_REPOSITORY}/git/refs",
            "repos/${GITHUB_REPOSITORY}/releases",
        ),
    }
    for job_name, fragments in required_fragments.items():
        block = jobs[job_name]
        findings.extend(
            f"{path}: `{job_name}` is missing required release control {fragment!r}"
            for fragment in fragments
            if fragment not in block
        )

    semantic_release_forbidden = ("commit: true", "tag: true", "push: true")
    findings.extend(
        f"{path}: `semantic-release` contains forbidden release control {fragment!r}"
        for fragment in semantic_release_forbidden
        if fragment in jobs["semantic-release"]
    )
    release_push_forbidden = ("git push --force", "git push -f")
    findings.extend(
        f"{path}: `semantic-release` contains forbidden force push {fragment!r}"
        for fragment in release_push_forbidden
        if fragment in jobs["semantic-release"]
    )

    publish_forbidden = (
        "actions/checkout@",
        "pip install",
        "uv sync",
        "uv build",
    )
    findings.extend(
        f"{path}: `publish` contains forbidden build capability {fragment!r}"
        for fragment in publish_forbidden
        if fragment in jobs["publish"]
    )

    oidc_permissions = re.findall(r"(?m)^\s+id-token:\s*write(?:\s*#.*)?$", text)
    if len(oidc_permissions) != 1:
        findings.append(
            f"{path}: `id-token: write` must appear exactly once, in `publish`"
        )
    if "needs.gate-prepared.result == 'success'" not in jobs.get("build", ""):
        findings.append(f"{path}: `build` must require the exact prepared-commit gate")
    tag_create = 'gh api --method POST "repos/${GITHUB_REPOSITORY}/git/refs"'
    if text.count(tag_create) != 1 or tag_create not in jobs["finalize"]:
        findings.append(f"{path}: tag creation must appear exactly once, in `finalize`")
    release_create = 'gh api --method POST "repos/${GITHUB_REPOSITORY}/releases"'
    if text.count(release_create) != 1 or release_create not in jobs["finalize"]:
        findings.append(
            f"{path}: GitHub release creation must appear exactly once, in `finalize`"
        )
    return findings


def resolve(
    paths: Sequence[Path],
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Split paths into workflows, hooks, Dependabot, and project configs.

    Args:
        paths: Files to check. Empty means discover the repository defaults.

    Returns:
        Workflow files, pre-commit configurations, Dependabot configurations, and
        project configurations, each sorted.
    """
    if paths:
        candidates: Iterable[Path] = paths
    else:
        candidates = [
            *sorted(Path(".github/workflows").glob("*.yml")),
            *sorted(Path(".github/workflows").glob("*.yaml")),
            Path(".pre-commit-config.yaml"),
            Path(".github/dependabot.yml"),
            Path(".github/dependabot.yaml"),
            Path("pyproject.toml"),
        ]
    workflows: list[Path] = []
    configs: list[Path] = []
    dependabot_configs: list[Path] = []
    project_configs: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        if path.name.startswith(".pre-commit-config"):
            configs.append(path)
        elif path.name in {"dependabot.yml", "dependabot.yaml"}:
            dependabot_configs.append(path)
        elif path.name == "pyproject.toml":
            project_configs.append(path)
        elif path.parent.name == "workflows":
            workflows.append(path)
    return (
        sorted(workflows),
        sorted(configs),
        sorted(dependabot_configs),
        sorted(project_configs),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Check every discovered or supplied file.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when every automation invariant passes, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="check_pins",
        description="Assert immutable pins and dependency automation boundaries.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "files to check; defaults to workflows, hooks, Dependabot, and project "
            "configuration"
        ),
    )
    arguments = parser.parse_args(argv)
    workflows, configs, dependabot_configs, project_configs = resolve(arguments.paths)

    findings: list[str] = []
    for path in workflows:
        findings += check_workflow(path)
        findings += check_setup_uv_workflow(path)
        if path.name == "release.yml":
            findings += check_release_workflow(path)
        if path.name in {"dependabot-auto-merge.yml", "dependabot-auto-merge.yaml"}:
            findings += check_dependabot_auto_merge(path)
    for path in configs:
        findings += check_pre_commit(path)
    for path in dependabot_configs:
        findings += check_dependabot(path)
    for path in project_configs:
        findings += check_project_uv_ownership(path)

    checked = (
        len(workflows) + len(configs) + len(dependabot_configs) + len(project_configs)
    )
    if not checked:
        print("check_pins: no automation configuration found", file=sys.stderr)
        return 1
    for finding in findings:
        print(f"FAIL: {finding}", file=sys.stderr)
    if findings:
        print(f"check_pins: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print(
        f"check_pins: {checked} file(s) checked, automation pins, uv ownership, "
        "release boundaries, and Dependabot policy are intact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

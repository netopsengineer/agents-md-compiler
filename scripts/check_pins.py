"""Assert immutable automation pins and release workflow boundaries.

Four things must hold for this repository's supply chain (PY-CI-003, PY-SEC-009):

- every ``uses:`` in a workflow names a 40-hex commit SHA, with a trailing comment
  recording which tag that SHA came from; and
- every third-party ``rev:`` in ``.pre-commit-config.yaml`` is a 40-hex commit SHA
  carrying a ``# frozen: <tag>`` comment; and
- ``.github/dependabot.yml`` omits custom ``labels`` so GitHub creates and applies
  Dependabot's default labels without repository provisioning; and
- ``release.yml`` preserves the package-change eligibility gate, separates release
  preparation from public finalization, and keeps publication minimal.

These controls are easy to satisfy by hand and easy to lose by accident. An automated
dependency update that rewrites a pin to a mutable tag would silently undo the
guarantee, and a mutable tag can be repointed at different code after review. This
check turns that from something a reviewer has to notice into a gate.

Scope: offline and structural. It proves each pin *is* an immutable SHA carrying a
tag comment and proves the Dependabot configuration does not override automatic
labels. It does not prove a SHA still corresponds to its tag, which requires network
access and is recorded in docs/dependency-verification.md.

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
    """Reject custom labels in a Dependabot configuration.

    Omitting the key preserves GitHub's documented behavior: Dependabot creates
    its default dependency and ecosystem labels when they do not exist. Custom
    labels require separate repository provisioning and are outside this project's
    configuration contract.

    Args:
        path: Dependabot configuration file to check.

    Returns:
        Human-readable findings, empty when automatic labels remain enabled.
    """
    findings: list[str] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        active, _, _ = line.partition("#")
        if DEPENDABOT_LABELS.search(active):
            findings.append(
                f"{path}:{number}: custom Dependabot `labels` are forbidden; "
                "omit the key so GitHub creates and applies its default labels"
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
) -> tuple[list[Path], list[Path], list[Path]]:
    """Split paths into workflows, hook configs, and Dependabot configs.

    Args:
        paths: Files to check. Empty means discover the repository defaults.

    Returns:
        Workflow files, pre-commit configurations, and Dependabot configurations,
        each sorted.
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
        ]
    workflows: list[Path] = []
    configs: list[Path] = []
    dependabot_configs: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        if path.name.startswith(".pre-commit-config"):
            configs.append(path)
        elif path.name in {"dependabot.yml", "dependabot.yaml"}:
            dependabot_configs.append(path)
        elif path.parent.name == "workflows":
            workflows.append(path)
    return sorted(workflows), sorted(configs), sorted(dependabot_configs)


def main(argv: Sequence[str] | None = None) -> int:
    """Check every discovered or supplied file.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when every automation invariant passes, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="check_pins",
        description="Assert immutable pins and safe Dependabot labels.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="files to check; defaults to workflows, hooks, and Dependabot config",
    )
    arguments = parser.parse_args(argv)
    workflows, configs, dependabot_configs = resolve(arguments.paths)

    findings: list[str] = []
    for path in workflows:
        findings += check_workflow(path)
        if path.name == "release.yml":
            findings += check_release_workflow(path)
    for path in configs:
        findings += check_pre_commit(path)
    for path in dependabot_configs:
        findings += check_dependabot(path)

    checked = len(workflows) + len(configs) + len(dependabot_configs)
    if not checked:
        print("check_pins: no automation configuration found", file=sys.stderr)
        return 1
    for finding in findings:
        print(f"FAIL: {finding}", file=sys.stderr)
    if findings:
        print(f"check_pins: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print(
        f"check_pins: {checked} file(s) checked, pins are immutable and "
        "release boundaries and automatic Dependabot labels are intact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

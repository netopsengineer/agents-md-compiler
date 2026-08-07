"""Assert immutable automation pins and safe Dependabot label configuration.

Three things must hold for this repository's supply chain (PY-CI-003, PY-SEC-009):

- every ``uses:`` in a workflow names a 40-hex commit SHA, with a trailing comment
  recording which tag that SHA came from; and
- every third-party ``rev:`` in ``.pre-commit-config.yaml`` is a 40-hex commit SHA
  carrying a ``# frozen: <tag>`` comment; and
- ``.github/dependabot.yml`` omits custom ``labels`` so GitHub creates and applies
  Dependabot's default labels without repository provisioning.

These controls are easy to satisfy by hand and easy to lose by accident. An automated
dependency update that rewrites a pin to a mutable tag would silently undo the
guarantee, and a mutable tag can be repointed at different code after review. This
check turns that from something a reviewer has to notice into a gate.

Scope: offline and structural. It proves each pin *is* an immutable SHA carrying a
tag comment and proves the Dependabot configuration does not override automatic
labels. It does not prove a SHA still corresponds to its tag, which requires network
access and is recorded in docs/dependency-verification.md.

Line-oriented matching is deliberate rather than a YAML parse: tag evidence lives in
comments, while the forbidden Dependabot key is unambiguous on an active YAML line.
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
        "Dependabot uses automatic labels"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

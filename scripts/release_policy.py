"""Classify one pushed change set against the published package surface.

The automatic semantic-release path may run only when at least one path changed
by the exact GitHub push affects bytes or metadata published to package users.
Repository-only changes remain eligible for an explicit operator-dispatched
release, but they must not publish merely because their commit type is ``fix`` or
``feat``.

This script receives the NUL-delimited output of ``git diff --name-only -z`` on
standard input. Keeping Git history access in the workflow makes the policy logic
pure, deterministic, and directly testable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# These paths become wheel or sdist content, package metadata, or the public long
# description. Changes elsewhere are repository operations and do not require a
# package version by themselves.
PUBLISHED_EXACT_PATHS = frozenset({"LICENSE", "README.md", "pyproject.toml"})
PUBLISHED_PREFIX = PurePosixPath("src/agents_md_compiler")


class ReleasePolicyError(ValueError):
    """Release-policy input is malformed or unsafe to evaluate."""


# release.yml executes this with the runner's system Python before uv is installed.
# Keep the policy carrier free of version-specific dataclass options.
@dataclass(frozen=True)
class ReleaseDecision:
    """Result of comparing changed paths with the published package surface.

    Attributes:
        before: Commit immediately before the GitHub push.
        after: Commit at the head of the GitHub push.
        changed_paths: All changed repository paths, sorted and deduplicated.
        package_paths: Changed paths that affect published package content.
    """

    before: str
    after: str
    changed_paths: tuple[str, ...]
    package_paths: tuple[str, ...]

    @property
    def package_changes(self) -> bool:
        """Return whether the automatic package-release path is eligible."""
        return bool(self.package_paths)


def parse_paths(data: bytes) -> tuple[str, ...]:
    """Parse strict UTF-8, NUL-delimited Git path output.

    Args:
        data: Raw bytes from ``git diff --name-only -z``.

    Returns:
        Sorted, deduplicated repository-relative paths.

    Raises:
        ReleasePolicyError: The data is not strict UTF-8, is not NUL-terminated,
            or contains an absolute or parent-relative path.
    """
    if not data:
        return ()
    if not data.endswith(b"\0"):
        message = "changed-path input must be NUL-terminated"
        raise ReleasePolicyError(message)
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as error:
        message = "changed paths must be strict UTF-8"
        raise ReleasePolicyError(message) from error

    paths: set[str] = set()
    for value in decoded[:-1].split("\0"):
        if not value:
            message = "changed-path input contains an empty path"
            raise ReleasePolicyError(message)
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            message = f"changed path is not repository-relative: {value!r}"
            raise ReleasePolicyError(message)
        paths.add(value)
    return tuple(sorted(paths))


def is_published_path(value: str) -> bool:
    """Return whether a repository path affects published package content.

    Args:
        value: Repository-relative POSIX path.

    Returns:
        ``True`` for a published file or import-package descendant.
    """
    if value in PUBLISHED_EXACT_PATHS:
        return True
    candidate = PurePosixPath(value)
    return candidate != PUBLISHED_PREFIX and PUBLISHED_PREFIX in candidate.parents


def decide(before: str, after: str, changed_paths: Sequence[str]) -> ReleaseDecision:
    """Classify changed paths for automatic package-release eligibility.

    Args:
        before: Commit immediately before the GitHub push.
        after: Commit at the head of the GitHub push.
        changed_paths: Repository-relative POSIX paths.

    Returns:
        Immutable release decision.

    Raises:
        ReleasePolicyError: A commit identifier is malformed or the range is empty.
    """
    for label, value in (("before", before), ("after", after)):
        if COMMIT_SHA.fullmatch(value) is None:
            message = f"{label} must be a lowercase 40-hex commit SHA: {value!r}"
            raise ReleasePolicyError(message)
    if before == "0" * 40:
        message = "before must identify an existing commit, not the zero object"
        raise ReleasePolicyError(message)
    if before == after:
        message = "before and after must identify different commits"
        raise ReleasePolicyError(message)
    changed = tuple(sorted(set(changed_paths)))
    package = tuple(value for value in changed if is_published_path(value))
    return ReleaseDecision(
        before=before,
        after=after,
        changed_paths=changed,
        package_paths=package,
    )


def write_github_output(path: Path, decision: ReleaseDecision) -> None:
    """Append trusted scalar decision fields to a GitHub output file.

    Changed file names are deliberately excluded because Git permits newlines in
    names. Only the validated boolean crosses the output protocol boundary.

    Args:
        path: GitHub-provided output file.
        decision: Evaluated release decision.
    """
    package_changes = str(decision.package_changes).lower()
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"package_changes={package_changes}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Evaluate NUL-delimited paths and emit the release decision.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` on a valid decision and ``2`` for invalid input.
    """
    parser = argparse.ArgumentParser(
        prog="release_policy",
        description="Decide whether changed paths affect the published package.",
    )
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--github-output", type=Path)
    arguments = parser.parse_args(argv)

    try:
        changed_paths = parse_paths(sys.stdin.buffer.read())
        decision = decide(arguments.before, arguments.after, changed_paths)
        if arguments.github_output is not None:
            write_github_output(arguments.github_output, decision)
    except (OSError, ReleasePolicyError) as error:
        print(f"release_policy: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "after": decision.after,
                "before": decision.before,
                "changed_paths": list(decision.changed_paths),
                "package_changes": decision.package_changes,
                "package_paths": list(decision.package_paths),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

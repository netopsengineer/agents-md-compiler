"""Inspect built distributions for content, identity, and reproducibility.

CI artifact jobs and local artifact gates run this same module, so a passing
pipeline and a passing workstation check mean the same thing (PY-CI-001).

Three subcommands:

``inspect``
    Assert that a ``dist/`` directory holds exactly one wheel and one sdist, that
    neither carries repository-only material, and that everything the package
    promises to ship is present.

``inventory``
    Emit a sorted, machine-readable SHA-256 inventory of the artifacts, used as the
    immutable handoff record between the release build and publish jobs.

``compare``
    Compare two archives member by member, so a wheel built from the sdist can be
    proven equivalent to the wheel built from the working tree.

Every failure is reported as a nonzero exit with the offending paths listed, never
as a bare assertion, so the CI log names what is wrong (PY-OBS-003).
"""

import argparse
import hashlib
import json
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

WHEEL_SUFFIX = ".whl"
SDIST_SUFFIX = ".tar.gz"

# Repository-only or session-local material that must never reach a user.
# uv_build's sdist already limits itself to the module root plus project metadata,
# and pyproject's source-exclude repeats these patterns; this list is the assertion
# that proves it rather than trusting either.
FORBIDDEN_SUBSTRINGS = (
    "codex-global-agents-compiler-execution-plan.md",
    "goal.md",
    "CLAUDE.md",
    "AGENTS.md",
    ".pre-commit-config.yaml",
    ".markdownlint-cli2.jsonc",
    "/.github/",
    "/docs/",
    "/examples/",
    "/scripts/",
    "/tests/",
    "/.venv/",
    "/.git/",
    "__pycache__",
    ".pyc",
)

# Paths every wheel must carry. py.typed is what makes the inline types visible to
# a consumer's checker (PY-TYPE-008), and the schemas are advertised package data.
REQUIRED_WHEEL_MEMBERS = (
    "agents_md_compiler/__init__.py",
    "agents_md_compiler/cli.py",
    "agents_md_compiler/py.typed",
    "agents_md_compiler/schemas/manifest-v1.schema.json",
    "agents_md_compiler/schemas/lock-v1.schema.json",
    "agents_md_compiler/schemas/lock-v2.schema.json",
    "agents_md_compiler/schemas/receipt-v1.schema.json",
)

REQUIRED_SDIST_MEMBERS = (
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "src/agents_md_compiler/__init__.py",
    "src/agents_md_compiler/py.typed",
)


class InspectionError(Exception):
    """A distribution failed a content or identity requirement."""


def archive_members(archive: Path) -> dict[str, bytes]:
    """Read every regular file in a wheel or sdist.

    Directory entries and symlinks are skipped: a wheel records no directories and an
    sdist's directory entries carry no content to compare.

    Args:
        archive: Path to a ``.whl`` or ``.tar.gz`` archive.

    Returns:
        Mapping of member name to member bytes.

    Raises:
        InspectionError: The suffix is not a recognized distribution format, or a
            member declared in the index could not be read.
    """
    name = archive.name
    if name.endswith(WHEEL_SUFFIX):
        with zipfile.ZipFile(archive) as bundle:
            return {
                info.filename: bundle.read(info.filename)
                for info in bundle.infolist()
                if not info.is_dir()
            }
    if name.endswith(SDIST_SUFFIX):
        members: dict[str, bytes] = {}
        # Members are read into memory and never extracted to disk. Non-regular
        # entries are skipped, and inputs are artifacts this repository just built.
        with tarfile.open(archive, mode="r:gz") as bundle:
            for info in bundle.getmembers():
                if not info.isfile():
                    continue
                handle = bundle.extractfile(info)
                if handle is None:
                    problem = f"{archive.name}: unreadable member {info.name}"
                    raise InspectionError(problem)
                with handle:
                    members[info.name] = handle.read()
        return members
    problem = f"{archive.name}: not a wheel or an sdist"
    raise InspectionError(problem)


def strip_root(member: str) -> str:
    """Drop an sdist's single top-level directory from a member name.

    An sdist wraps everything in ``name-version/``, which would otherwise make every
    required-path comparison depend on the version string.

    Args:
        member: Member name as recorded in the archive.

    Returns:
        The member name relative to the archive root.
    """
    _, separator, remainder = member.partition("/")
    return remainder if separator else member


def check_forbidden(archive: Path, members: Sequence[str]) -> list[str]:
    """Find members that must never ship.

    Args:
        archive: Archive being inspected, used in the message.
        members: Member names.

    Returns:
        Human-readable findings, empty when the archive is clean.
    """
    findings: list[str] = []
    for member in sorted(members):
        # Compare against a leading-slash form so a pattern like "/tests/" cannot be
        # satisfied by an unrelated name such as "contests/".
        probe = "/" + member
        for pattern in FORBIDDEN_SUBSTRINGS:
            if pattern in probe:
                findings.append(f"{archive.name}: ships {member} (matched {pattern})")
                break
    return findings


def check_required(
    archive: Path, members: Sequence[str], required: Sequence[str]
) -> list[str]:
    """Find promised members that are absent.

    Args:
        archive: Archive being inspected, used in the message.
        members: Member names, already relative to the archive root.
        required: Members that must be present.

    Returns:
        Human-readable findings, empty when nothing is missing.
    """
    present = set(members)
    return [
        f"{archive.name}: missing {expected}"
        for expected in required
        if expected not in present
    ]


def locate(dist: Path) -> tuple[Path, Path]:
    """Find the single wheel and single sdist in a distribution directory.

    Args:
        dist: Directory holding built artifacts.

    Returns:
        The wheel path and the sdist path.

    Raises:
        InspectionError: The directory does not hold exactly one of each. Publishing
            from a directory with a stale second artifact is how the wrong file
            reaches an index, so this is fatal rather than a warning.
    """
    wheels = sorted(p for p in dist.glob(f"*{WHEEL_SUFFIX}") if p.is_file())
    sdists = sorted(p for p in dist.glob(f"*{SDIST_SUFFIX}") if p.is_file())
    if len(wheels) != 1 or len(sdists) != 1:
        problem = (
            f"{dist}: expected exactly one wheel and one sdist, "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
        raise InspectionError(problem)
    return wheels[0], sdists[0]


def command_inspect(dist: Path) -> list[str]:
    """Assert both artifacts carry exactly what they should.

    Args:
        dist: Directory holding built artifacts.

    Returns:
        Human-readable findings, empty when both artifacts pass.
    """
    wheel, sdist = locate(dist)
    wheel_members = list(archive_members(wheel))
    sdist_members = [strip_root(name) for name in archive_members(sdist)]
    findings = check_forbidden(wheel, wheel_members)
    findings += check_required(wheel, wheel_members, REQUIRED_WHEEL_MEMBERS)
    findings += check_forbidden(sdist, sdist_members)
    findings += check_required(sdist, sdist_members, REQUIRED_SDIST_MEMBERS)
    print(f"wheel: {wheel.name} ({len(wheel_members)} members)")
    print(f"sdist: {sdist.name} ({len(sdist_members)} members)")
    return findings


def command_inventory(dist: Path, output: Path | None) -> list[str]:
    """Emit a sorted SHA-256 inventory of the built artifacts.

    Args:
        dist: Directory holding built artifacts.
        output: Path to write the inventory to, or ``None`` for stdout only.

    Returns:
        Human-readable findings, empty on success.
    """
    wheel, sdist = locate(dist)
    entries = [
        {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted((wheel, sdist), key=lambda p: p.name)
    ]
    document = json.dumps({"artifacts": entries}, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(document)
    if output is not None:
        output.write_text(document, encoding="utf-8")
    return []


def command_compare(left: Path, right: Path) -> list[str]:
    """Compare two archives member by member.

    Wheel metadata legitimately records the builder's own version, so a differing
    ``WHEEL`` or ``RECORD`` member is reported rather than ignored: the caller decides
    whether that difference is acceptable, and every other difference is a defect.

    Args:
        left: First archive.
        right: Second archive.

    Returns:
        Human-readable findings, empty when the archives are equivalent.
    """
    left_members = archive_members(left)
    right_members = archive_members(right)
    findings = [
        f"only in {left.name}: {name}"
        for name in sorted(set(left_members) - set(right_members))
    ]
    findings += [
        f"only in {right.name}: {name}"
        for name in sorted(set(right_members) - set(left_members))
    ]
    shared = sorted(set(left_members) & set(right_members))
    for name in shared:
        if left_members[name] != right_members[name]:
            findings.append(f"content differs: {name}")
    print(f"{left.name}: {len(left_members)} members")
    print(f"{right.name}: {len(right_members)} members")
    print(f"compared {len(shared)} shared member(s)")
    return findings


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="inspect_dist",
        description="Inspect built distributions for content and reproducibility.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect = subcommands.add_parser("inspect", help="assert artifact contents")
    inspect.add_argument("dist", type=Path, help="directory holding built artifacts")

    inventory = subcommands.add_parser("inventory", help="emit a SHA-256 inventory")
    inventory.add_argument("dist", type=Path, help="directory holding built artifacts")
    inventory.add_argument(
        "--output", type=Path, default=None, help="also write the inventory here"
    )

    compare = subcommands.add_parser("compare", help="compare two archives")
    compare.add_argument("left", type=Path, help="first archive")
    compare.add_argument("right", type=Path, help="second archive")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a subcommand.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when every check passed, ``1`` when a check failed, ``2`` when the
        inputs could not be inspected at all.
    """
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "inspect":
            findings = command_inspect(arguments.dist)
        elif arguments.command == "inventory":
            findings = command_inventory(arguments.dist, arguments.output)
        else:
            findings = command_compare(arguments.left, arguments.right)
    except (InspectionError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"inspect_dist: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(f"FAIL: {finding}", file=sys.stderr)
    if findings:
        print(f"inspect_dist: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("inspect_dist: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

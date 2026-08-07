"""Tests for the automatic package-release eligibility policy."""

from pathlib import Path

import pytest

from scripts.release_policy import (
    ReleasePolicyError,
    decide,
    is_published_path,
    parse_paths,
    write_github_output,
)

BEFORE = "1" * 40
AFTER = "2" * 40


def test_parses_nul_delimited_paths_deterministically() -> None:
    assert parse_paths(b"README.md\0src/agents_md_compiler/cli.py\0README.md\0") == (
        "README.md",
        "src/agents_md_compiler/cli.py",
    )
    assert parse_paths(b"") == ()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"README.md", "NUL-terminated"),
        (b"README.md\0\0", "empty path"),
        (b"/README.md\0", "repository-relative"),
        (b"docs/../README.md\0", "repository-relative"),
        (b"\xff\0", "strict UTF-8"),
    ],
)
def test_rejects_malformed_changed_path_input(data: bytes, message: str) -> None:
    with pytest.raises(ReleasePolicyError, match=message):
        parse_paths(data)


@pytest.mark.parametrize(
    ("path", "published"),
    [
        ("LICENSE", True),
        ("README.md", True),
        ("pyproject.toml", True),
        ("src/agents_md_compiler/cli.py", True),
        ("src/agents_md_compiler/schemas/manifest-v1.schema.json", True),
        ("src/agents_md_compiler_extra/cli.py", False),
        ("scripts/inspect_dist.py", False),
        ("tests/test_cli.py", False),
        ("docs/ci-evidence.md", False),
        (".github/workflows/release.yml", False),
    ],
)
def test_classifies_the_published_surface(path: str, published: bool) -> None:
    assert is_published_path(path) is published


def test_decides_from_every_path_in_the_exact_push() -> None:
    decision = decide(
        BEFORE,
        AFTER,
        [
            "docs/ci-evidence.md",
            "src/agents_md_compiler/cli.py",
            "README.md",
        ],
    )

    assert decision.package_changes is True
    assert decision.changed_paths == (
        "README.md",
        "docs/ci-evidence.md",
        "src/agents_md_compiler/cli.py",
    )
    assert decision.package_paths == (
        "README.md",
        "src/agents_md_compiler/cli.py",
    )


def test_repo_only_changes_are_an_automatic_release_no_op() -> None:
    decision = decide(
        BEFORE,
        AFTER,
        [".gitignore", ".github/workflows/release.yml", "docs/ci-evidence.md"],
    )

    assert decision.package_changes is False
    assert decision.package_paths == ()


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        ("1" * 39, AFTER, "before must be"),
        (BEFORE, "A" * 40, "after must be"),
        ("0" * 40, AFTER, "zero object"),
        (BEFORE, BEFORE, "different commits"),
    ],
)
def test_requires_a_valid_nonempty_push_range(
    before: str,
    after: str,
    message: str,
) -> None:
    with pytest.raises(ReleasePolicyError, match=message):
        decide(before, after, [])


def test_writes_only_validated_scalars_to_github_output(tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    decision = decide(
        BEFORE,
        AFTER,
        ["README.md", "docs/notes\nrelease=true.md"],
    )

    write_github_output(output, decision)

    assert output.read_text(encoding="utf-8") == "package_changes=true\n"

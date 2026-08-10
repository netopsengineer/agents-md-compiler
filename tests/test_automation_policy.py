"""Regression tests for release workflow supply-chain controls."""

from pathlib import Path

import pytest

from scripts.check_pins import check_release_workflow

REPOSITORY_ROOT = Path(__file__).parent.parent
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"


def test_release_workflow_preserves_every_release_boundary() -> None:
    assert check_release_workflow(RELEASE_WORKFLOW) == []


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("          tag: false\n", "          tag: true\n", "tag: false"),
        ("          commit: false\n", "          commit: true\n", "commit: false"),
        ("          push: false\n", "          push: true\n", "push: false"),
        (
            "CHANGELOG.md|pyproject.toml|uv.lock) ;;",
            "CHANGELOG.md|pyproject.toml|uv.lock|src/**) ;;",
            "CHANGELOG.md|pyproject.toml|uv.lock) ;;",
        ),
        (
            'git push origin "HEAD:refs/heads/main"',
            'git push --force origin "HEAD:refs/heads/main"',
            'git push origin "HEAD:refs/heads/main"',
        ),
        (
            "python3 scripts/release_policy.py",
            "python3 scripts/release_policy_disabled.py",
            "scripts/release_policy.py",
        ),
        (
            "needs.publish.result == 'success'",
            "needs.publish.result == 'skipped'",
            "needs.publish.result == 'success'",
        ),
        (
            "needs.gate-prepared.result == 'success'",
            "needs.gate-prepared.result == 'skipped'",
            "exact prepared-commit gate",
        ),
        (
            '"chore(release): "*',
            '"chore(publish): "*',
            '"chore(release): "*',
        ),
        ("      id-token: write", "      id-token: read", "id-token: write"),
        (
            "  verify-recovery:\n",
            "  recovery-removed:\n",
            "required release job `verify-recovery` is missing",
        ),
    ],
)
def test_rejects_removed_release_control(
    tmp_path: Path,
    before: str,
    after: str,
    expected: str,
) -> None:
    original = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert before in original
    workflow = tmp_path / "release.yml"
    workflow.write_text(original.replace(before, after, 1), encoding="utf-8")

    findings = check_release_workflow(workflow)

    assert any(expected in finding for finding in findings)


def test_rejects_checkout_in_the_publish_job(tmp_path: Path) -> None:
    original = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    marker = "  publish:\n    name: publish to PyPI\n"
    replacement = (
        "  publish:\n"
        "    name: publish to PyPI\n"
        "    # Simulate a forbidden privileged-job checkout.\n"
        "    uses: actions/checkout@0000000000000000000000000000000000000000\n"
    )
    assert marker in original
    workflow = tmp_path / "release.yml"
    workflow.write_text(original.replace(marker, replacement, 1), encoding="utf-8")

    findings = check_release_workflow(workflow)

    assert any("forbidden build capability" in finding for finding in findings)

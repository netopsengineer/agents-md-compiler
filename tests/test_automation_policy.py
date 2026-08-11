"""Regression tests for automation supply-chain controls."""

import re
from pathlib import Path

import pytest

from scripts.check_pins import (
    check_dependabot,
    check_dependabot_auto_merge,
    check_project_uv_ownership,
    check_release_workflow,
    check_setup_uv_workflow,
)

REPOSITORY_ROOT = Path(__file__).parent.parent
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
AUTO_MERGE_WORKFLOW = (
    REPOSITORY_ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml"
)
DEPENDABOT_CONFIG = REPOSITORY_ROOT / ".github" / "dependabot.yml"
PROJECT_CONFIG = REPOSITORY_ROOT / "pyproject.toml"


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


def test_every_setup_uv_step_uses_the_lockfile() -> None:
    workflows = sorted((REPOSITORY_ROOT / ".github" / "workflows").glob("*.yml"))

    findings = [
        finding
        for workflow in workflows
        for finding in check_setup_uv_workflow(workflow)
    ]

    assert findings == []


def test_rejects_a_setup_uv_version_override(tmp_path: Path) -> None:
    original = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "          version-file: uv.lock\n" in original
    workflow = tmp_path / "release.yml"
    workflow.write_text(
        original.replace(
            "          version-file: uv.lock\n",
            "          version: 0.12.3\n",
            1,
        ),
        encoding="utf-8",
    )

    findings = check_setup_uv_workflow(workflow)

    assert any("version-file: uv.lock" in finding for finding in findings)
    assert any("must not override" in finding for finding in findings)


def test_dependabot_owns_daily_lockstep_updates() -> None:
    assert check_dependabot(DEPENDABOT_CONFIG) == []


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("      interval: daily\n", "      interval: weekly\n", "daily schedule"),
        (
            "    multi-ecosystem-group: all-dependencies\n",
            "    multi-ecosystem-group: other\n",
            "every package ecosystem",
        ),
        (
            "      default-days: 7\n",
            "      default-days: 6\n",
            "seven-day cooldown",
        ),
        (
            "    open-pull-requests-limit: 5\n",
            "    labels: [dependencies]\n",
            "custom Dependabot `labels` are forbidden",
        ),
    ],
)
def test_rejects_nonautomatic_dependabot_policy(
    tmp_path: Path,
    before: str,
    after: str,
    expected: str,
) -> None:
    original = DEPENDABOT_CONFIG.read_text(encoding="utf-8")
    assert before in original
    config = tmp_path / "dependabot.yml"
    config.write_text(original.replace(before, after, 1), encoding="utf-8")

    findings = check_dependabot(config)

    assert any(expected in finding for finding in findings)


def test_dependabot_auto_merge_preserves_trust_boundaries() -> None:
    assert check_dependabot_auto_merge(AUTO_MERGE_WORKFLOW) == []


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("  schedule:\n", "  pull_request_target:\n", "schedule:"),
        (
            '              \'.user.login == "dependabot[bot]" and\n',
            '              \'.user.login == "somebody" and\n',
            '.user.login == "dependabot[bot]"',
        ),
        (
            "               .head.repo.full_name == $repository and\n",
            "               true and\n",
            ".head.repo.full_name == $repository",
        ),
        (
            '              --match-head-commit "$head_sha"\n',
            "              --admin\n",
            "--match-head-commit",
        ),
    ],
)
def test_rejects_weakened_dependabot_auto_merge(
    tmp_path: Path,
    before: str,
    after: str,
    expected: str,
) -> None:
    original = AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8")
    assert before in original
    workflow = tmp_path / "dependabot-auto-merge.yml"
    workflow.write_text(original.replace(before, after, 1), encoding="utf-8")

    findings = check_dependabot_auto_merge(workflow)

    assert any(expected in finding for finding in findings)


def test_project_owns_one_lock_derived_uv_pin() -> None:
    assert check_project_uv_ownership(PROJECT_CONFIG) == []


def test_rejects_a_floating_uv_bootstrap_requirement(tmp_path: Path) -> None:
    original = PROJECT_CONFIG.read_text(encoding="utf-8")
    match = re.search(
        r'^    "uv==(?P<version>\d+\.\d+\.\d+)",$', original, re.MULTILINE
    )
    assert match is not None
    exact_requirement = match.group(0)
    floating_requirement = exact_requirement.replace("uv==", "uv>=", 1)
    config = tmp_path / "pyproject.toml"
    config.write_text(
        original.replace(exact_requirement, floating_requirement, 1),
        encoding="utf-8",
    )

    findings = check_project_uv_ownership(config)

    assert any("exactly one exact" in finding for finding in findings)

"""Fail-closed tests for read-only publication reconciliation."""

import copy
import hashlib
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts import reconcile_release
from scripts.reconcile_release import (
    validate_evidence,
    verify_deferral,
    verify_downloads,
)


def evidence() -> dict[str, Any]:
    return {
        "info": {"version": "1.0.1"},
        "urls": [
            {"filename": name, "yanked": False, "digests": {"sha256": "a" * 64}}
            for name in (
                "agents_md_compiler-1.0.1-py3-none-any.whl",
                "agents_md_compiler-1.0.1.tar.gz",
            )
        ],
    }


def test_unchanged_and_tooling_only_inputs_are_no_ops() -> None:
    validate_evidence("1.0.1", (), evidence())
    validate_evidence(
        "1.0.1",
        (".pre-commit-config.yaml", ".github/workflows/security-scan.yml", "uv.lock"),
        evidence(),
    )


@pytest.mark.parametrize(
    "path", ["README.md", "LICENSE", "pyproject.toml", "src/agents_md_compiler/cli.py"]
)
def test_package_changes_require_review(path: str) -> None:
    with pytest.raises(ValueError, match="release review required"):
        validate_evidence("1.0.1", (path,), evidence())


@pytest.mark.parametrize("version", ["1.0.1rc1", "../bad", "1.0", "01.0.1"])
def test_unstable_or_unsafe_versions_fail(version: str) -> None:
    with pytest.raises(ValueError, match="stable"):
        validate_evidence(version, (), evidence())


@pytest.mark.parametrize(
    "registry",
    [
        None,
        {},
        {"info": {"version": "1.0.0"}},
        {"info": {"version": "1.0.1"}, "urls": []},
    ],
)
def test_missing_registry_evidence_fails(registry: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_evidence("1.0.1", (), registry)


@pytest.mark.parametrize(
    "mutation", ["yanked", "digest", "name", "duplicate", "missing"]
)
def test_invalid_file_evidence_fails(mutation: str) -> None:
    registry = copy.deepcopy(evidence())
    files = registry["urls"]
    if mutation == "yanked":
        files[0]["yanked"] = True
    elif mutation == "digest":
        files[0]["digests"] = {}
    elif mutation == "name":
        files[0]["filename"] = "wrong.whl"
    elif mutation == "duplicate":
        files[0] = files[1]
    else:
        files.pop()
    with pytest.raises((TypeError, ValueError)):
        validate_evidence("1.0.1", (), registry)


def test_reconciliation_is_read_only_and_does_not_change_release_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/release-reconcile.yml").read_text()
    validate = (root / ".github/workflows/validate.yml").read_text()
    release = (root / ".github/workflows/release.yml").read_text()
    assert 'cron: "17 3 * * *"' in validate
    assert 'cron: "47 * * * *"' in workflow
    assert "contents: read" in workflow
    assert ": write" not in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "fetch-depth: 0" in workflow
    assert 'git merge-base --is-ancestor "$tag" HEAD' in workflow
    assert 'git diff --name-only --no-renames -z "${tag}..HEAD"' in workflow
    assert "set -euo pipefail" in workflow
    assert "python3 -m scripts.reconcile_release" in workflow
    assert "schedule:" not in release
    assert "BEFORE: ${{ github.event.before }}" in release
    assert "git diff --name-only --no-renames -z" in release


@pytest.mark.parametrize(
    "case", ["good", "wrong-host", "http", "redirect", "oversize", "digest"]
)
def test_artifact_bytes_are_verified_without_execution(case: str) -> None:
    registry = evidence()
    for item in registry["urls"]:
        item["url"] = "https://files.pythonhosted.org/packages/verified/file"
        item["digests"]["sha256"] = hashlib.sha256(b"artifact").hexdigest()
    if case == "wrong-host":
        registry["urls"][0]["url"] = "https://example.com/packages/file"
    if case == "http":
        registry["urls"][0]["url"] = "http://files.pythonhosted.org/packages/file"
    response = MagicMock()
    response.status = 302 if case == "redirect" else 200
    response.read.return_value = (
        b"x" * (8 * 1024 * 1024 + 1) if case == "oversize" else b"artifact"
    )
    if case == "digest":
        response.read.return_value = b"tampered"
    with patch("scripts.reconcile_release.http.client.HTTPSConnection") as connection:
        connection.return_value.getresponse.return_value = response
        if case == "good":
            verify_downloads(registry)
            assert connection.call_count == 2
            assert connection.return_value.close.call_count == 2
        else:
            with pytest.raises(ValueError):
                verify_downloads(registry)


def test_active_deferral_requires_exact_tree_and_baseline() -> None:

    record = {
        "version": "1.0.1",
        "reviewed_commit": "a" * 40,
        "published_commit": "b" * 40,
        "package_inputs": {"pyproject.toml": "c" * 64},
    }
    with (
        patch("scripts.reconcile_release.git_bytes") as git,
        patch("scripts.reconcile_release.package_inputs") as inputs,
    ):
        git.side_effect = [b"d" * 40, b"b" * 40, b"", b""]
        inputs.return_value = record["package_inputs"]
        assert verify_deferral("1.0.1", record)
        assert git.call_count == 4
        inputs.assert_any_call("a" * 40)
        inputs.assert_any_call("d" * 40)


@pytest.mark.parametrize("mutation", ["altered", "added", "deleted", "reviewed"])
def test_changed_inputs_never_inherit_deferral(mutation: str) -> None:

    expected = {"pyproject.toml": "c" * 64}
    changed = dict(expected)
    if mutation == "deleted":
        changed.clear()
    elif mutation == "added":
        changed["src/agents_md_compiler/new.py"] = "e" * 64
    else:
        changed["pyproject.toml"] = "f" * 64
    record = {
        "version": "1.0.1",
        "reviewed_commit": "a" * 40,
        "published_commit": "b" * 40,
        "package_inputs": expected,
    }
    with (
        patch(
            "scripts.reconcile_release.git_bytes",
            side_effect=[b"d" * 40, b"b" * 40, b"", b""],
        ),
        patch(
            "scripts.reconcile_release.package_inputs",
            side_effect=[changed, expected]
            if mutation == "reviewed"
            else [expected, changed],
        ),
        pytest.raises(ValueError, match="inputs changed"),
    ):
        verify_deferral("1.0.1", record)


def test_deferral_retires_at_next_version_and_cannot_skip_registry() -> None:

    assert not verify_deferral("1.0.2", {"version": "1.0.1"})
    validate_evidence("1.0.1", ("pyproject.toml",), evidence(), deferred=True)
    with pytest.raises(ValueError):
        validate_evidence("1.0.1", ("pyproject.toml",), {}, deferred=True)


def test_deferral_rejects_changed_tag_and_nonancestor() -> None:

    record = {
        "version": "1.0.1",
        "reviewed_commit": "a" * 40,
        "published_commit": "b" * 40,
        "package_inputs": {"pyproject.toml": "c" * 64},
    }
    with (
        patch(
            "scripts.reconcile_release.git_bytes", side_effect=[b"d" * 40, b"e" * 40]
        ),
        pytest.raises(ValueError, match="baseline changed"),
    ):
        verify_deferral("1.0.1", record)
    with (
        patch(
            "scripts.reconcile_release.git_bytes",
            side_effect=[b"d" * 40, b"b" * 40, subprocess.CalledProcessError(1, "git")],
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        verify_deferral("1.0.1", record)


@pytest.mark.parametrize("mode", [b"120000", b"100755", b"160000"])
def test_deferral_rejects_non_regular_package_inputs(mode: bytes) -> None:

    entry = mode + b" blob " + b"a" * 40 + b"\tpyproject.toml\0"
    with (
        patch("scripts.reconcile_release.git_bytes", return_value=entry),
        pytest.raises(ValueError, match="regular"),
    ):
        reconcile_release.package_inputs("a" * 40)

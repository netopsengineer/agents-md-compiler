"""Tests for installed-distribution version discovery."""

import importlib
from importlib.metadata import PackageNotFoundError

import pytest

import agents_md_compiler
from agents_md_compiler import _version


def test_reports_the_installed_version() -> None:
    assert _version.distribution_version() == agents_md_compiler.__version__


def test_falls_back_when_the_distribution_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(_name: str) -> str:
        raise PackageNotFoundError(_version.DISTRIBUTION_NAME)

    monkeypatch.setattr(_version, "version", _missing)
    assert _version.distribution_version() == _version.UNKNOWN_VERSION


def test_module_entry_point_imports_the_cli_entry() -> None:
    module = importlib.import_module("agents_md_compiler.__main__")
    assert module.main is not None

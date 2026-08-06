"""Shared fixtures.

Every fixture writes only under ``tmp_path``. No test in this suite reads or writes
a real user configuration path, a real Codex home, or a canonical policy source.
"""

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents_md_compiler.manifest import load_manifest
from agents_md_compiler.models import CompiledBundle
from agents_md_compiler.state import compile_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODULE_FIXTURES = FIXTURES / "modules"
MANIFEST_FIXTURES = FIXTURES / "manifests"
INVALID_MANIFESTS = MANIFEST_FIXTURES / "invalid"
GOLDEN = FIXTURES / "golden"
RECEIPT_FIXTURES = FIXTURES / "receipts"
CODEX_FIXTURES = FIXTURES / "codex"

CORE_TEXT = """# Core Policy

Apply the core rules to every task.

- Prefer the smallest correct change.
- State assumptions explicitly.
"""

PYTHON_TEXT = """# Python Policy

Apply these rules when editing Python.

1. Type every public boundary.
2. Keep the formatter as the single layout authority.
"""

EXTRAS_TEXT = """# Extra Policy

This module exists so tests can assert first, middle, and last placement.
"""

NON_ASCII_TEXT = "x" * 20 + "\u00e9 trailing prose here\n"
"""UTF-8 policy text whose multibyte character crosses header probe byte 512."""


@dataclass(frozen=True, slots=True)
class Bundle:
    """A disposable bundle laid out under ``tmp_path``.

    Attributes:
        root: Directory holding the manifest.
        manifest: Manifest path.
        lock: Default lock path for that manifest.
        target: Default target path from the manifest.
        modules: Module identifier mapped to its source path.
        state_root: Disposable state root for install and rollback tests.
    """

    root: Path
    manifest: Path
    lock: Path
    target: Path
    modules: dict[str, Path]
    state_root: Path


def write_text_file(path: Path, text: str) -> Path:
    """Write a text file with LF endings and exactly one final LF.

    Args:
        path: Destination path.
        text: Content, which must already end with exactly one LF.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def build_manifest_text(
    *,
    bundle_id: str,
    default_target: str,
    modules: tuple[tuple[str, str], ...],
) -> str:
    """Compose manifest TOML text.

    Args:
        bundle_id: Bundle identifier.
        default_target: Target path value.
        modules: Ordered ``(id, source)`` pairs.

    Returns:
        The manifest text, ending with one LF.
    """
    lines = [
        "schema_version = 1",
        f'bundle_id = "{bundle_id}"',
        f'default_target = "{default_target}"',
    ]
    for module_id, source in modules:
        lines.extend(["", "[[modules]]", f'id = "{module_id}"', f'source = "{source}"'])
    return "\n".join(lines) + "\n"


def make_bundle(
    root: Path,
    *,
    bundle_id: str = "test-bundle",
    default_target: str = "out/AGENTS.md",
    module_texts: tuple[tuple[str, str], ...] = (
        ("core", CORE_TEXT),
        ("python", PYTHON_TEXT),
    ),
) -> Bundle:
    """Lay out a complete disposable bundle.

    Args:
        root: Directory to build in.
        bundle_id: Bundle identifier.
        default_target: Manifest ``default_target`` value.
        module_texts: Ordered ``(id, text)`` pairs written as module sources.

    Returns:
        The bundle description.
    """
    policy_dir = root / "policy"
    modules_dir = policy_dir / "modules"
    modules: dict[str, Path] = {}
    for module_id, text in module_texts:
        modules[module_id] = write_text_file(modules_dir / f"{module_id}.md", text)
    manifest_text = build_manifest_text(
        bundle_id=bundle_id,
        default_target=default_target,
        modules=tuple(
            (module_id, f"modules/{module_id}.md") for module_id, _ in module_texts
        ),
    )
    manifest = write_text_file(policy_dir / "global-agents.toml", manifest_text)
    state_root = root / "state"
    return Bundle(
        root=policy_dir,
        manifest=manifest,
        lock=Path(str(manifest) + ".lock.json"),
        target=(policy_dir / default_target).resolve()
        if not Path(default_target).is_absolute()
        else Path(default_target),
        modules=modules,
        state_root=state_root,
    )


@pytest.fixture
def bundle(tmp_path: Path) -> Bundle:
    """Provide a two-module disposable bundle.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The bundle description.
    """
    return make_bundle(tmp_path)


@pytest.fixture
def non_ascii_bundle(tmp_path: Path) -> Bundle:
    """Provide a disposable bundle containing UTF-8 policy prose.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The bundle description.
    """
    return make_bundle(tmp_path, module_texts=(("core", NON_ASCII_TEXT),))


@pytest.fixture
def three_module_bundle(tmp_path: Path) -> Bundle:
    """Provide a three-module bundle for first, middle, and last assertions.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The bundle description.
    """
    return make_bundle(
        tmp_path,
        bundle_id="test-three",
        module_texts=(
            ("core", CORE_TEXT),
            ("python", PYTHON_TEXT),
            ("extras", EXTRAS_TEXT),
        ),
    )


FIXED_MOMENT = datetime(2026, 8, 4, 21, 30, 15, tzinfo=UTC)
"""Pinned clock so receipt and backup file names are deterministic in tests."""

FIXED_OPERATION_ID = "0123456789abcdef0123456789abcdef"
"""Pinned operation identifier so receipt names are deterministic in tests."""


def fixed_clock() -> datetime:
    """Return the pinned test moment.

    Returns:
        The pinned UTC moment.
    """
    return FIXED_MOMENT


def fixed_operation_id() -> str:
    """Return the pinned test operation identifier.

    Returns:
        The pinned identifier.
    """
    return FIXED_OPERATION_ID


def compiled_of(bundle: Bundle, *, target: Path | None = None) -> CompiledBundle:
    """Compile a bundle from its manifest on disk.

    Args:
        bundle: The bundle to compile.
        target: Effective target, for the output-alias check.

    Returns:
        The compiled bundle.
    """
    return compile_bundle(load_manifest(bundle.manifest), target=target)


def write_lock(bundle: Bundle) -> None:
    """Write a fresh matching lock for a bundle.

    Args:
        bundle: The bundle to lock.
    """
    bundle.lock.write_bytes(compiled_of(bundle).lock_bytes)


@pytest.fixture
def locked_bundle(tmp_path: Path) -> Bundle:
    """Provide a two-module bundle whose lock is already current.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        The bundle description.
    """
    built = make_bundle(tmp_path)
    built.target.parent.mkdir(parents=True, exist_ok=True)
    write_lock(built)
    return built


MOCK_CODEX = CODEX_FIXTURES / "mock_codex.py"
"""Mock Codex CLI. Selected by MOCK_CODEX_MODE; sends no model request."""


@dataclass(frozen=True, slots=True)
class MockCodex:
    """A mock ``codex`` executable placed on ``PATH``.

    Attributes:
        directory: Directory prepended to ``PATH``.
        executable: The launcher the verifier will resolve.
    """

    directory: Path
    executable: Path


def install_mock_codex(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
    bundle: Path | None = None,
) -> MockCodex:
    """Place a mock ``codex`` on ``PATH`` and select its behavior.

    A real executable is installed rather than a patched function, so the verifier's
    own subprocess, timeout, and output-capture code is what runs. The launcher form
    differs by platform because a shebang is not executable on Windows; that branch
    lives here in test support, never in the package.

    Args:
        root: Directory to build the launcher in.
        monkeypatch: pytest patcher, used to set PATH and the mock's mode.
        mode: Value for ``MOCK_CODEX_MODE``.
        bundle: File whose bytes the mock should echo back.

    Returns:
        The installed mock description.
    """
    directory = root / "mock-bin"
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        executable = directory / "codex.bat"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{MOCK_CODEX}" %*\r\n', encoding="utf-8"
        )
    else:
        executable = directory / "codex"
        executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{MOCK_CODEX}" "$@"\n',
            encoding="utf-8",
        )
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("MOCK_CODEX_MODE", mode)
    monkeypatch.setenv("MOCK_CODEX_BUNDLE", "" if bundle is None else str(bundle))
    return MockCodex(directory=directory, executable=executable)

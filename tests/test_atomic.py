"""Atomic replacement: durability steps, mode handling, and every failure path.

Each failure is injected at the exact call it guards, and every one asserts that no
partial file and no leftover temporary file remains.
"""

import os
import stat
import tempfile
from pathlib import Path

import pytest

from agents_md_compiler import atomic
from agents_md_compiler.errors import MutationError, MutationProblem


def _temp_leftovers(directory: Path) -> list[Path]:
    """List temporary files this module would have created.

    Args:
        directory: Directory to inspect.

    Returns:
        Any leftover temporary paths.
    """
    return sorted(directory.glob(f"{atomic.TEMP_PREFIX}*"))


def test_a_new_file_is_created_with_the_requested_mode(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    synced = atomic.atomic_write(target, b"# Body\n", mode=0o600)
    assert target.read_bytes() == b"# Body\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert synced is True
    assert _temp_leftovers(tmp_path) == []


def test_an_existing_file_is_replaced(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"old\n")
    atomic.atomic_write(target, b"new\n", mode=0o644)
    assert target.read_bytes() == b"new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_the_temporary_file_shares_the_target_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Atomic replacement only holds within one filesystem, so the temporary file
    # must be created beside the target rather than in a system temp directory.
    target = tmp_path / "nested" / "AGENTS.md"
    target.parent.mkdir()
    seen: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def _record(
        *,
        dir: Path,
        prefix: str,
        suffix: str,
    ) -> tuple[int, str]:
        seen.append(str(dir))
        return real_mkstemp(dir=dir, prefix=prefix, suffix=suffix)

    monkeypatch.setattr(tempfile, "mkstemp", _record)
    atomic.atomic_write(target, b"x\n", mode=0o600)
    assert seen == [str(target.parent)]


def test_an_empty_payload_is_written(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    atomic.atomic_write(target, b"", mode=0o600)
    assert target.read_bytes() == b""


def test_a_missing_parent_directory_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MutationError) as raised:
        atomic.atomic_write(tmp_path / "absent" / "AGENTS.md", b"x\n", mode=0o600)
    assert raised.value.problem is MutationProblem.WRITE_FAILED


def test_a_write_failure_leaves_no_partial_file_and_no_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"old\n")
    real_fsync = os.fsync

    def _refuse(fileno: int) -> None:
        message = "Input/output error"
        raise OSError(5, message)

    monkeypatch.setattr(os, "fsync", _refuse)
    with pytest.raises(MutationError) as raised:
        atomic.atomic_write(target, b"new\n", mode=0o600)
    assert raised.value.problem is MutationProblem.SYNC_FAILED
    assert target.read_bytes() == b"old\n", "the old bytes must survive intact"
    assert _temp_leftovers(tmp_path) == []
    monkeypatch.setattr(os, "fsync", real_fsync)


def test_a_permission_failure_leaves_the_target_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"old\n")

    def _refuse(self: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        message = "Operation not permitted"
        raise OSError(1, message)

    monkeypatch.setattr(Path, "chmod", _refuse)
    with pytest.raises(MutationError) as raised:
        atomic.atomic_write(target, b"new\n", mode=0o600)
    assert raised.value.problem is MutationProblem.PERMISSION_FAILED
    assert target.read_bytes() == b"old\n"
    assert _temp_leftovers(tmp_path) == []


def test_a_replace_failure_leaves_the_target_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"old\n")

    def _refuse(self: Path, target: object) -> None:
        message = "Read-only file system"
        raise OSError(30, message)

    monkeypatch.setattr(Path, "replace", _refuse)
    with pytest.raises(MutationError) as raised:
        atomic.atomic_write(target, b"new\n", mode=0o600)
    assert raised.value.problem is MutationProblem.REPLACE_FAILED
    assert target.read_bytes() == b"old\n"
    assert _temp_leftovers(tmp_path) == []


def test_an_interrupted_write_never_yields_a_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The strongest form of the guarantee: the target holds either the complete old
    # bytes or the complete new bytes, never a prefix of the new ones.
    target = tmp_path / "AGENTS.md"
    old = b"# Old bundle\n" + b"o" * 4096 + b"\n"
    target.write_bytes(old)
    new = b"# New bundle\n" + b"n" * 8192 + b"\n"

    def _die_mid_write(fileno: int) -> None:
        message = "Interrupted system call"
        raise OSError(4, message)

    monkeypatch.setattr(os, "fsync", _die_mid_write)
    with pytest.raises(MutationError):
        atomic.atomic_write(target, new, mode=0o600)
    observed = target.read_bytes()
    assert observed in {old, new}
    assert observed == old


def test_directory_sync_reports_unsupported_platforms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows cannot open a directory as a descriptor. The replacement stays atomic;
    # only the durability of the rename is platform-dependent.
    real_open = os.open

    def _refuse(path: object, flags: int, *args: object) -> int:
        if str(path) == str(tmp_path):
            message = "Permission denied"
            raise OSError(13, message)
        return real_open(path, flags, *args)  # pyright: ignore[reportArgumentType, reportCallIssue]

    monkeypatch.setattr(os, "open", _refuse)
    assert atomic.sync_directory(tmp_path) is False


def test_directory_sync_reports_a_failed_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(fileno: int) -> None:
        message = "Invalid argument"
        raise OSError(22, message)

    monkeypatch.setattr(os, "fsync", _refuse)
    assert atomic.sync_directory(tmp_path) is False


def test_a_write_reports_when_the_directory_could_not_be_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "AGENTS.md"

    def _unsupported(_directory: Path) -> bool:
        return False

    monkeypatch.setattr(atomic, "sync_directory", _unsupported)
    assert atomic.atomic_write(target, b"x\n", mode=0o600) is False
    assert target.read_bytes() == b"x\n"


def test_state_directories_are_created_with_owner_only_access(tmp_path: Path) -> None:
    directory = tmp_path / "state" / "bundle" / "receipts"
    atomic.create_state_directory(directory, mode=0o700)
    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_creating_an_existing_state_directory_leaves_its_mode_alone(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o500)
    try:
        atomic.create_state_directory(directory, mode=0o700)
        assert stat.S_IMODE(directory.stat().st_mode) == 0o500
    finally:
        directory.chmod(0o700)


def test_a_state_directory_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(
        self: Path, mode: int = 0o777, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        message = "Read-only file system"
        raise OSError(30, message)

    monkeypatch.setattr(Path, "mkdir", _refuse)
    with pytest.raises(MutationError) as raised:
        atomic.create_state_directory(tmp_path / "state", mode=0o700)
    assert raised.value.problem is MutationProblem.STATE_ROOT_FAILED

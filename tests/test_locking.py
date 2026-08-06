"""Advisory locking.

The lock has no platform branch: exclusive-create behaves identically on POSIX and
Windows, so one implementation is exercised here and the same code path runs on
every platform in CI.

Contention is exercised with injected clock and sleep functions, so no test waits on
real elapsed time.
"""

import json
import os
from pathlib import Path

import pytest

from agents_md_compiler import locking
from agents_md_compiler.errors import MutationError, MutationProblem


def test_the_lock_path_is_derived_from_the_target_digest(tmp_path: Path) -> None:
    first = locking.lock_path_for(Path("/home/u/.codex/AGENTS.md"), lock_dir=tmp_path)
    again = locking.lock_path_for(Path("/home/u/.codex/AGENTS.md"), lock_dir=tmp_path)
    other = locking.lock_path_for(Path("/home/u/other/AGENTS.md"), lock_dir=tmp_path)
    assert first == again
    assert first != other
    assert first.parent == tmp_path
    assert first.name.endswith(locking.LOCK_SUFFIX)
    # A digest keeps the name filesystem-safe whatever the target path contains.
    assert "/" not in first.stem


def test_the_lock_is_created_and_removed(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    with locking.advisory_lock(lock_file) as held:
        assert held == lock_file
        assert lock_file.is_file()
    assert not lock_file.exists()


def test_the_lock_records_its_owner(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    with locking.advisory_lock(lock_file, pid=4242):
        recorded = json.loads(lock_file.read_text(encoding="utf-8"))
    assert recorded["pid"] == 4242


def test_the_lock_defaults_to_the_current_process(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    with locking.advisory_lock(lock_file):
        recorded = json.loads(lock_file.read_text(encoding="utf-8"))
    assert recorded["pid"] == os.getpid()


def test_the_lock_is_released_even_when_the_body_raises(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    sentinel = RuntimeError("body failed")
    with pytest.raises(RuntimeError) as raised, locking.advisory_lock(lock_file):
        raise sentinel
    assert raised.value is sentinel
    assert not lock_file.exists()


def test_the_lock_survives_an_operator_removing_it(tmp_path: Path) -> None:
    # Releasing is unlinking, so a lock an operator removed mid-hold must not turn
    # the release into a second failure.
    lock_file = tmp_path / "guard.lock"
    with locking.advisory_lock(lock_file):
        lock_file.unlink()
    assert not lock_file.exists()


def test_a_contended_lock_is_acquired_once_it_is_released(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    lock_file.write_text(
        json.dumps({"pid": 1, "acquired_at": "held"}), encoding="utf-8"
    )
    ticks = iter([0.0, 0.1, 0.2, 0.3])
    released: list[int] = []

    def _sleep(_seconds: float) -> None:
        # Simulate the holder releasing between polls.
        lock_file.unlink()
        released.append(1)

    with locking.advisory_lock(
        lock_file, timeout_seconds=5.0, clock=lambda: next(ticks), sleep=_sleep
    ):
        assert lock_file.is_file()
    assert released == [1]


def test_a_held_lock_times_out_and_names_its_holder(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    lock_file.write_text(
        json.dumps({"pid": 777, "acquired_at": "2026-08-04T00:00:00Z"}),
        encoding="utf-8",
    )
    ticks = iter([0.0, 10.0])
    with (
        pytest.raises(MutationError) as raised,
        locking.advisory_lock(
            lock_file,
            timeout_seconds=1.0,
            clock=lambda: next(ticks),
            sleep=lambda _s: None,
        ),
    ):
        pass  # pragma: no cover - acquisition must fail before the body runs
    assert raised.value.problem is MutationProblem.LOCK_UNAVAILABLE
    assert "pid 777" in str(raised.value)
    assert lock_file.is_file(), "a timeout must not remove the holder's lock"


def test_an_unreadable_holder_record_still_produces_a_diagnostic(
    tmp_path: Path,
) -> None:
    lock_file = tmp_path / "guard.lock"
    lock_file.write_bytes(b"\xff\xfe not json")
    ticks = iter([0.0, 10.0])
    with (
        pytest.raises(MutationError) as raised,
        locking.advisory_lock(
            lock_file,
            timeout_seconds=1.0,
            clock=lambda: next(ticks),
            sleep=lambda _s: None,
        ),
    ):
        pass  # pragma: no cover - acquisition must fail before the body runs
    assert "holder unknown" in str(raised.value)


def test_a_non_object_holder_record_produces_a_diagnostic(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    lock_file.write_text("[1, 2]", encoding="utf-8")
    ticks = iter([0.0, 10.0])
    with (
        pytest.raises(MutationError) as raised,
        locking.advisory_lock(
            lock_file,
            timeout_seconds=1.0,
            clock=lambda: next(ticks),
            sleep=lambda _s: None,
        ),
    ):
        pass  # pragma: no cover - acquisition must fail before the body runs
    assert "holder unknown" in str(raised.value)


def test_a_holder_record_that_vanishes_produces_a_diagnostic(tmp_path: Path) -> None:
    lock_file = tmp_path / "guard.lock"
    lock_file.write_text("{}", encoding="utf-8")
    ticks = iter([0.0, 10.0])

    def _clock() -> float:
        value = next(ticks)
        if value > 0:
            lock_file.unlink()
        return value

    with (
        pytest.raises(MutationError) as raised,
        locking.advisory_lock(
            lock_file, timeout_seconds=1.0, clock=_clock, sleep=lambda _s: None
        ),
    ):
        pass  # pragma: no cover - acquisition must fail before the body runs
    assert "holder unknown" in str(raised.value)


def test_a_missing_lock_directory_is_reported(tmp_path: Path) -> None:
    with (
        pytest.raises(MutationError) as raised,
        locking.advisory_lock(tmp_path / "absent" / "guard.lock"),
    ):
        pass  # pragma: no cover - acquisition must fail before the body runs
    assert raised.value.problem is MutationProblem.LOCK_UNAVAILABLE


def test_a_lock_write_failure_closes_the_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_file = tmp_path / "guard.lock"
    real_write = os.write

    def _refuse(fileno: int, data: bytes) -> int:
        if lock_file.exists():
            message = "No space left on device"
            raise OSError(28, message)
        return real_write(fileno, data)

    monkeypatch.setattr(os, "write", _refuse)
    with (
        pytest.raises(OSError, match="No space left"),
        locking.advisory_lock(lock_file),
    ):
        pass  # pragma: no cover - the write fails before the body runs
    assert not lock_file.exists()

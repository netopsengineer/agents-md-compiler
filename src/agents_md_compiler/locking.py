"""Cross-platform advisory locking.

The primitive is an exclusive-create lock file: ``os.open`` with ``O_CREAT |
O_EXCL`` succeeds for exactly one process, and the lock is released by unlinking
the file. That is deliberately not ``fcntl.flock`` or ``msvcrt.locking``.

Why: those two APIs live in platform-only standard-library modules, so a single
implementation would need an import that cannot even be loaded on the other
platform, leaving one branch permanently unexecutable and untestable. Exclusive
create behaves identically on POSIX and Windows, needs no platform branch at all,
and is fully exercised by the test suite on any one platform.

What this buys and what it does not: the lock coordinates cooperating compiler
instances. It cannot stop a process that ignores it, and it does not make network
filesystem semantics safe. That is why every mutation additionally recaptures its
digest preconditions under the lock and verifies its postcondition after writing.

A lock file records the owning process ID and acquisition time, so a lock left
behind by a killed process names its owner in the timeout diagnostic and can be
removed deliberately by an operator.
"""

import json
import os
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from agents_md_compiler.errors import MutationError, MutationProblem
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import STATE_FILE_MODE

DEFAULT_TIMEOUT_SECONDS = 30.0
"""How long acquisition waits before reporting the lock unavailable."""

DEFAULT_POLL_SECONDS = 0.05
"""Delay between acquisition attempts while the lock is held elsewhere."""

LOCK_SUFFIX = ".lock"
"""Suffix appended to a lock file name."""


def lock_path_for(target: Path, *, lock_dir: Path) -> Path:
    """Derive the lock file path for one target.

    The name is a digest of the resolved target path, so the lock lives in the
    tool's own state directory instead of beside the target. Writing a sibling file
    next to a target under ``~/.codex`` would litter a directory this tool does not
    own, and a digest keeps the name filesystem-safe for any target path.

    Args:
        target: Resolved target path being protected.
        lock_dir: Directory that holds lock files.

    Returns:
        The lock file path, which may not exist yet.
    """
    digest = sha256_bytes(str(target).encode("utf-8"))
    return lock_dir / f"{digest}{LOCK_SUFFIX}"


def _describe_holder(path: Path) -> str:
    """Read the recorded owner of an existing lock file.

    Args:
        path: Lock file path.

    Returns:
        A short description of the holder, or a note that it could not be read.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return "holder unknown"
    if not isinstance(payload, dict):
        return "holder unknown"
    record = cast("dict[str, object]", payload)
    pid = record.get("pid", "unknown")
    acquired = record.get("acquired_at", "unknown")
    return f"held by pid {pid} since {acquired}"


@contextmanager
def advisory_lock(
    path: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    pid: int | None = None,
) -> Generator[Path]:
    """Hold an advisory lock for the duration of the block.

    Args:
        path: Lock file path, whose parent directory must already exist.
        timeout_seconds: How long to wait for a contended lock.
        poll_seconds: Delay between attempts.
        clock: Monotonic clock, injectable so tests need no real elapsed time.
        sleep: Delay function, injectable so tests need no real sleeping.
        pid: Process identifier to record. Defaults to the current process.

    Yields:
        The lock file path, while the lock is held.

    Raises:
        MutationError: The lock could not be acquired before the deadline, or the
            lock file could not be created for a reason other than contention.
    """
    deadline = clock() + timeout_seconds
    owner = os.getpid() if pid is None else pid
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, STATE_FILE_MODE
            )
        except FileExistsError as error:
            if clock() >= deadline:
                raise MutationError(
                    MutationProblem.LOCK_UNAVAILABLE,
                    path=path,
                    detail=f"{_describe_holder(path)}; waited {timeout_seconds}s",
                ) from error
            sleep(poll_seconds)
        except OSError as error:
            raise MutationError(
                MutationProblem.LOCK_UNAVAILABLE,
                path=path,
                detail=error.strerror or type(error).__name__,
            ) from error
    try:
        payload = json.dumps(
            {"pid": owner, "acquired_at": "held"}, ensure_ascii=True, sort_keys=True
        )
        os.write(descriptor, payload.encode("utf-8"))
        os.close(descriptor)
        descriptor = None
        yield path
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # Releasing is unlinking. missing_ok covers an operator who removed a lock
        # believed to be stale while this process still held it; the mutation's own
        # digest preconditions remain the real protection.
        path.unlink(missing_ok=True)

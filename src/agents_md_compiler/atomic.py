"""Atomic same-filesystem file replacement.

An interrupted write must leave either the complete old bytes or the complete new
bytes, never a partial file. The sequence is: create a temporary file in the
target's own directory, write it, flush it, ``fsync`` it, apply the intended mode,
atomically replace the target, then ``fsync`` the containing directory where the
platform supports it.

The temporary file must share the target's directory because ``os.replace`` is
atomic only within one filesystem. A temporary directory elsewhere would silently
degrade to a copy.
"""

import os
import tempfile
from pathlib import Path

from agents_md_compiler.errors import MutationError, MutationProblem

TEMP_PREFIX = ".agents-md-compiler-"
"""Prefix for the same-directory temporary file, so a leftover is identifiable."""

TEMP_SUFFIX = ".tmp"
"""Suffix for the same-directory temporary file."""


def sync_directory(directory: Path) -> bool:
    """Flush a directory entry to stable storage where supported.

    Windows cannot open a directory as a file descriptor, so this is a documented
    best effort rather than a guarantee. The replacement itself is still atomic;
    only the durability of the rename across a crash is platform-dependent.

    Args:
        directory: Directory whose entries were changed.

    Returns:
        ``True`` when the directory was synced, ``False`` when the platform does not
        support it.
    """
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def atomic_write(target: Path, data: bytes, *, mode: int) -> bool:
    """Replace a file's contents atomically.

    Args:
        target: Destination path. Its parent directory must already exist.
        data: Exact bytes to write.
        mode: Permission bits to apply to the new file.

    Returns:
        ``True`` when the containing directory was also synced.

    Raises:
        MutationError: Any step failed. The temporary file is removed first, so a
            failure never leaves a partial file behind and never damages the target.
    """
    directory = target.parent
    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=directory, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX
        )
    except OSError as error:
        raise MutationError(
            MutationProblem.WRITE_FAILED,
            path=target,
            detail=error.strerror or type(error).__name__,
        ) from error
    temporary = Path(temp_name)
    try:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise MutationError(
                MutationProblem.SYNC_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
        try:
            temporary.chmod(mode)
        except OSError as error:
            raise MutationError(
                MutationProblem.PERMISSION_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
        try:
            temporary.replace(target)
        except OSError as error:
            raise MutationError(
                MutationProblem.REPLACE_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
    except MutationError:
        temporary.unlink(missing_ok=True)
        raise
    return sync_directory(directory)


def create_state_directory(directory: Path, *, mode: int) -> None:
    """Create a state directory tree with owner-only access.

    An existing directory's mode is left alone, so a more restrictive operator
    choice is never broadened.

    Args:
        directory: Directory to create, including parents.
        mode: Mode applied to directories this call creates.

    Raises:
        MutationError: The directory could not be created.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=mode)
    except OSError as error:
        raise MutationError(
            MutationProblem.STATE_ROOT_FAILED,
            path=directory,
            detail=error.strerror or type(error).__name__,
        ) from error

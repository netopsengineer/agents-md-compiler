"""Deterministic lock generation, parsing, and comparison.

The lock is the pinning artifact. Its serialization is byte-exact and documented in
``docs/rendered-format-v1.md``, because the rendered bundle records the lock's own
digest, which makes the serialization part of the output format.

Parsing is strict for the same reason manifest parsing is: a lock is reviewed
evidence, and silently ignoring part of it would mean the review covered something
other than what was verified.
"""

import json
import re
import stat
from pathlib import Path
from typing import Any, cast

from agents_md_compiler.errors import (
    LockError,
    LockMissingError,
    LockProblem,
    LockStaleProblem,
)
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import (
    IDENTIFIER_PATTERN,
    LOCK_FORMAT_VERSION,
    SHA256_PATTERN,
    BundleLock,
    BundleManifest,
    LockedModule,
    SourceSnapshot,
)

TOP_LEVEL_KEYS = frozenset(
    {"bundle_id", "format_version", "manifest_sha256", "modules"}
)
"""The only accepted top-level lock keys."""

MODULE_KEYS = frozenset({"id", "resolved_source", "sha256", "size_bytes"})
"""The only accepted locked module keys."""


def build_lock(
    manifest: BundleManifest, snapshots: tuple[SourceSnapshot, ...]
) -> BundleLock:
    """Build a lock from a manifest and its validated snapshots.

    Args:
        manifest: The parsed manifest.
        snapshots: Validated snapshots in manifest order.

    Returns:
        A lock recording each source's resolved path, digest, and size.
    """
    return BundleLock(
        bundle_id=manifest.bundle_id,
        format_version=LOCK_FORMAT_VERSION,
        manifest_sha256=manifest.sha256,
        modules=tuple(
            LockedModule(
                id=snapshot.id,
                resolved_source=str(snapshot.resolved_source),
                sha256=snapshot.sha256,
                size_bytes=snapshot.size_bytes,
            )
            for snapshot in snapshots
        ),
    )


def serialize_lock(lock: BundleLock) -> bytes:
    """Serialize a lock to its canonical bytes.

    Keys are sorted at every depth, indentation is two spaces, output is
    ASCII-escaped so the file is unambiguously valid UTF-8 regardless of the host's
    filename encoding, and the result ends with exactly one LF. Module order is
    preserved because order is semantic.

    Args:
        lock: The lock to serialize.

    Returns:
        Canonical lock bytes.
    """
    payload: dict[str, Any] = {
        "bundle_id": lock.bundle_id,
        "format_version": lock.format_version,
        "manifest_sha256": lock.manifest_sha256,
        "modules": [
            {
                "id": module.id,
                "resolved_source": module.resolved_source,
                "sha256": module.sha256,
                "size_bytes": module.size_bytes,
            }
            for module in lock.modules
        ],
    }
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _require_text(
    document: dict[str, object],
    key: str,
    lock: Path | None,
    lexical: str | None,
    *,
    pattern_problem: LockProblem,
    pattern: re.Pattern[str],
) -> str:
    """Fetch a required string that must match a compiled pattern.

    Args:
        document: Decoded object.
        key: Key to read.
        lock: Resolved lock path.
        lexical: Lock path as supplied.
        pattern_problem: Refusal to use when the pattern does not match.
        pattern: Compiled pattern exposing ``match``.

    Returns:
        The validated string.

    Raises:
        LockError: The key is missing, not a string, or does not match.
    """
    if key not in document:
        raise LockError(LockProblem.MISSING_KEY, detail=key, lock=lock, lexical=lexical)
    value: object = document[key]
    if not isinstance(value, str):
        raise LockError(
            LockProblem.WRONG_TYPE,
            detail=f"{key} is {type(value).__name__}, expected string",
            lock=lock,
            lexical=lexical,
        )
    if pattern.match(value) is None:
        raise LockError(
            pattern_problem, detail=f"{key}={value!r}", lock=lock, lexical=lexical
        )
    return value


def _parse_modules(
    document: dict[str, object], lock: Path | None, lexical: str | None
) -> tuple[LockedModule, ...]:
    """Parse the locked module array.

    Args:
        document: Decoded lock object.
        lock: Resolved lock path.
        lexical: Lock path as supplied.

    Returns:
        Locked modules in recorded order.

    Raises:
        LockError: The array is missing, empty, or structurally invalid.
    """
    if "modules" not in document:
        raise LockError(
            LockProblem.MISSING_KEY, detail="modules", lock=lock, lexical=lexical
        )
    raw: object = document["modules"]
    if not isinstance(raw, list) or not raw:
        raise LockError(LockProblem.NO_MODULES, lock=lock, lexical=lexical)
    # Narrowing cast justified by the isinstance check above: pyright narrows a
    # value of static type `object` to `list[Unknown]`, and this restates the
    # element type as `object` so every element is still isinstance-checked below.
    entries = cast("list[object]", raw)
    modules: list[LockedModule] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise LockError(
                LockProblem.MODULE_NOT_AN_OBJECT,
                detail=f"modules[{index}] is {type(entry).__name__}",
                lock=lock,
                lexical=lexical,
            )
        module = cast("dict[str, object]", entry)
        unknown = sorted(frozenset(module) - MODULE_KEYS)
        if unknown:
            raise LockError(
                LockProblem.UNKNOWN_MODULE_KEY,
                detail=", ".join(unknown),
                lock=lock,
                lexical=lexical,
            )
        module_id = _require_text(
            module,
            "id",
            lock,
            lexical,
            pattern_problem=LockProblem.BAD_IDENTIFIER,
            pattern=IDENTIFIER_PATTERN,
        )
        digest = _require_text(
            module,
            "sha256",
            lock,
            lexical,
            pattern_problem=LockProblem.BAD_DIGEST,
            pattern=SHA256_PATTERN,
        )
        if "resolved_source" not in module:
            raise LockError(
                LockProblem.MISSING_KEY,
                detail="resolved_source",
                lock=lock,
                lexical=lexical,
            )
        resolved_source: object = module["resolved_source"]
        if not isinstance(resolved_source, str) or not resolved_source:
            raise LockError(
                LockProblem.WRONG_TYPE,
                detail="resolved_source must be a non-empty string",
                lock=lock,
                lexical=lexical,
            )
        if "size_bytes" not in module:
            raise LockError(
                LockProblem.MISSING_KEY,
                detail="size_bytes",
                lock=lock,
                lexical=lexical,
            )
        size_bytes: object = module["size_bytes"]
        # `type(...) is int` rather than isinstance: bool is an int subclass and
        # `true` must not satisfy a byte count.
        if type(size_bytes) is not int or size_bytes < 1:
            raise LockError(
                LockProblem.BAD_SIZE,
                detail=f"size_bytes={size_bytes!r}",
                lock=lock,
                lexical=lexical,
            )
        modules.append(
            LockedModule(
                id=module_id,
                resolved_source=resolved_source,
                sha256=digest,
                size_bytes=size_bytes,
            )
        )
    return tuple(modules)


def parse_lock(
    data: bytes, *, lock: Path | None = None, lexical: str | None = None
) -> BundleLock:
    """Parse and validate lock bytes.

    Args:
        data: Exact lock bytes.
        lock: Resolved lock path, for diagnostics.
        lexical: Lock path as supplied, for diagnostics.

    Returns:
        The validated lock.

    Raises:
        LockError: The bytes violate any lock format version 1 rule.
    """
    try:
        document: object = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise LockError(
            LockProblem.SYNTAX, detail="not valid UTF-8", lock=lock, lexical=lexical
        ) from error
    except json.JSONDecodeError as error:
        raise LockError(
            LockProblem.SYNTAX, detail=str(error), lock=lock, lexical=lexical
        ) from error
    if not isinstance(document, dict):
        raise LockError(
            LockProblem.NOT_AN_OBJECT,
            detail=type(document).__name__,
            lock=lock,
            lexical=lexical,
        )
    root = cast("dict[str, object]", document)
    unknown = sorted(frozenset(root) - TOP_LEVEL_KEYS)
    if unknown:
        raise LockError(
            LockProblem.UNKNOWN_KEY,
            detail=", ".join(unknown),
            lock=lock,
            lexical=lexical,
        )
    if "format_version" not in root:
        raise LockError(
            LockProblem.MISSING_KEY,
            detail="format_version",
            lock=lock,
            lexical=lexical,
        )
    format_version: object = root["format_version"]
    if type(format_version) is not int:
        raise LockError(
            LockProblem.WRONG_TYPE,
            detail=f"format_version is {type(format_version).__name__}, expected integer",
            lock=lock,
            lexical=lexical,
        )
    if format_version != LOCK_FORMAT_VERSION:
        raise LockError(
            LockProblem.UNSUPPORTED_FORMAT_VERSION,
            detail=f"{format_version}, expected {LOCK_FORMAT_VERSION}",
            lock=lock,
            lexical=lexical,
        )
    bundle_id = _require_text(
        root,
        "bundle_id",
        lock,
        lexical,
        pattern_problem=LockProblem.BAD_IDENTIFIER,
        pattern=IDENTIFIER_PATTERN,
    )
    manifest_sha256 = _require_text(
        root,
        "manifest_sha256",
        lock,
        lexical,
        pattern_problem=LockProblem.BAD_DIGEST,
        pattern=SHA256_PATTERN,
    )
    return BundleLock(
        bundle_id=bundle_id,
        format_version=format_version,
        manifest_sha256=manifest_sha256,
        modules=_parse_modules(root, lock, lexical),
    )


def read_lock_bytes(path: Path, *, lexical: str | None = None) -> bytes:
    """Read the exact bytes of an on-disk lock.

    Args:
        path: Resolved lock path.
        lexical: Lock path as supplied.

    Returns:
        The exact file bytes.

    Raises:
        LockMissingError: No lock exists at the path.
        LockError: The path is a link, is not a regular file, or is unreadable.
    """
    reference = str(path) if lexical is None else lexical
    if path.is_symlink():
        raise LockError(LockProblem.SYMLINK, lock=path, lexical=reference)
    try:
        result = path.stat()
    except FileNotFoundError as error:
        raise LockMissingError(lock=path, lexical=reference) from error
    except OSError as error:
        raise LockError(
            LockProblem.UNREADABLE,
            detail=error.strerror or type(error).__name__,
            lock=path,
            lexical=reference,
        ) from error
    if not stat.S_ISREG(result.st_mode):
        raise LockError(LockProblem.NOT_A_FILE, lock=path, lexical=reference)
    try:
        return path.read_bytes()
    except OSError as error:
        raise LockError(
            LockProblem.UNREADABLE,
            detail=error.strerror or type(error).__name__,
            lock=path,
            lexical=reference,
        ) from error


def load_lock(path: Path, *, lexical: str | None = None) -> BundleLock:
    """Read and validate an on-disk lock.

    An absent lock propagates as :class:`LockMissingError`, and an unreadable or
    invalid one as :class:`LockError`, from the read and parse steps.

    Args:
        path: Resolved lock path.
        lexical: Lock path as supplied.

    Returns:
        The validated lock.
    """
    data = read_lock_bytes(path, lexical=lexical)
    return parse_lock(data, lock=path, lexical=lexical)


def lock_digest(lock: BundleLock) -> str:
    """Digest a lock's canonical serialization.

    Args:
        lock: The lock to digest.

    Returns:
        Lowercase hexadecimal SHA-256 digest of the canonical bytes.
    """
    return sha256_bytes(serialize_lock(lock))


def compare_locks(on_disk: BundleLock, fresh: BundleLock) -> LockStaleProblem | None:
    """Explain how an on-disk lock differs from a freshly built one.

    Args:
        on_disk: The lock read from disk.
        fresh: The lock built from the current manifest and sources.

    Returns:
        The most specific difference, or ``None`` when the two agree.
    """
    if on_disk.bundle_id != fresh.bundle_id:
        return LockStaleProblem.BUNDLE_ID_CHANGED
    if on_disk.manifest_sha256 != fresh.manifest_sha256:
        return LockStaleProblem.MANIFEST_CHANGED
    if tuple(module.id for module in on_disk.modules) != tuple(
        module.id for module in fresh.modules
    ):
        return LockStaleProblem.MODULE_SET_CHANGED
    if on_disk.modules != fresh.modules:
        return LockStaleProblem.SOURCES_CHANGED
    return None

"""Canonical source reading and byte-invariant validation.

A source is read exactly once into a validated snapshot. Hashing and rendering both
consume that snapshot, so the bytes that were checked are provably the bytes that
get written. The opened descriptor is re-stat'ed against the pre-open stat, so a
file swapped between the check and the read is detected rather than trusted.

Nothing here repairs a source. Every violation is a refusal.
"""

import os
import stat
from pathlib import Path

from agents_md_compiler.errors import SourceError, SourceProblem
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import (
    MARKER_PREFIX,
    BundleLimits,
    ModuleSpec,
    SourceSnapshot,
)

UTF8_BOM = b"\xef\xbb\xbf"
"""Rejected outright: a BOM is valid UTF-8 but corrupts a concatenated document."""


def _check_path_encodable(spec: ModuleSpec) -> None:
    """Require the source path itself to be representable as UTF-8.

    The lock records the resolved path, so a path that cannot round-trip through
    UTF-8 could not be recorded unambiguously.

    Args:
        spec: The module to check.

    Raises:
        SourceError: The path is not UTF-8 representable.
    """
    try:
        str(spec.source).encode("utf-8")
    except UnicodeEncodeError as error:
        raise SourceError.from_spec(SourceProblem.PATH_NOT_UTF8, spec) from error


def _stat_source(spec: ModuleSpec, limits: BundleLimits) -> os.stat_result:
    """Validate the source's path, type, and size before opening it.

    Args:
        spec: The module to check.
        limits: Configured safeguards.

    Returns:
        The pre-open stat result, used later to detect a swap.

    Raises:
        SourceError: The source is a link, missing, not a regular file, unreadable,
            empty, or larger than the configured limit.
    """
    if spec.source.is_symlink():
        raise SourceError.from_spec(
            SourceProblem.SYMLINK,
            spec,
            link_target=Path(os.path.realpath(spec.source)),
        )
    try:
        result = spec.source.stat()
    except FileNotFoundError as error:
        raise SourceError.from_spec(SourceProblem.MISSING, spec) from error
    except OSError as error:
        raise SourceError.from_spec(
            SourceProblem.UNREADABLE,
            spec,
            detail=error.strerror or type(error).__name__,
        ) from error
    if not stat.S_ISREG(result.st_mode):
        raise SourceError.from_spec(SourceProblem.NOT_A_FILE, spec)
    if result.st_size == 0:
        raise SourceError.from_spec(SourceProblem.EMPTY, spec)
    if result.st_size > limits.max_source_bytes:
        raise SourceError.from_spec(
            SourceProblem.TOO_LARGE,
            spec,
            detail=f"{result.st_size} > {limits.max_source_bytes}",
        )
    return result


def _read_exact(
    spec: ModuleSpec, before: os.stat_result, limits: BundleLimits
) -> bytes:
    """Read the source once and prove it did not change during the read.

    Args:
        spec: The module to read.
        before: Stat result captured before opening.
        limits: Configured safeguards.

    Returns:
        The exact file bytes.

    Raises:
        SourceError: The file is unreadable, exceeds the limit, or its identity or
            size changed between the pre-open stat and the read.
    """
    try:
        with spec.source.open("rb") as stream:
            after = os.fstat(stream.fileno())
            # One byte past the limit distinguishes "at the limit" from "over it"
            # without materializing an unbounded read.
            data = stream.read(limits.max_source_bytes + 1)
    except OSError as error:
        raise SourceError.from_spec(
            SourceProblem.UNREADABLE,
            spec,
            detail=error.strerror or type(error).__name__,
        ) from error
    identity_before = (before.st_dev, before.st_ino, before.st_size)
    identity_after = (after.st_dev, after.st_ino, after.st_size)
    # This single check also bounds the size. `_stat_source` already refused a
    # pre-open size over the limit, so an identical identity plus a read length
    # equal to the reported size proves the accepted bytes are within the limit. A
    # file that grew after `fstat` fails the length comparison rather than slipping
    # through, and the read itself is capped one byte past the limit so an
    # unbounded payload is never materialized.
    if identity_before != identity_after or len(data) != after.st_size:
        raise SourceError.from_spec(
            SourceProblem.CHANGED_WHILE_READING,
            spec,
            detail=f"expected {identity_before}, observed {identity_after}, read {len(data)} bytes",
        )
    return data


def _check_bytes(spec: ModuleSpec, data: bytes) -> None:
    """Enforce every byte-level invariant, in diagnostic-friendly order.

    A BOM and a NUL byte are both valid UTF-8, so they are checked before decoding
    to produce a specific diagnostic instead of a generic decode failure.

    Args:
        spec: The module the bytes belong to.
        data: Exact file bytes.

    Raises:
        SourceError: The bytes violate an encoding or content invariant.
    """
    # Emptiness is not rechecked here. `_stat_source` owns that rule, and the
    # identity check in `_read_exact` proves the read length equals the non-zero
    # reported size, so a second guard here would be unreachable rather than
    # defensive, and unreachable code cannot be tested or trusted.
    if data.startswith(UTF8_BOM):
        raise SourceError.from_spec(SourceProblem.HAS_BOM, spec)
    nul_offset = data.find(b"\x00")
    if nul_offset >= 0:
        raise SourceError.from_spec(
            SourceProblem.HAS_NUL, spec, detail=f"at byte {nul_offset}"
        )
    cr_offset = data.find(b"\r")
    if cr_offset >= 0:
        raise SourceError.from_spec(
            SourceProblem.HAS_CR, spec, detail=f"at byte {cr_offset}"
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceError.from_spec(
            SourceProblem.NOT_UTF8, spec, detail=f"at byte {error.start}"
        ) from error
    if not data.endswith(b"\n") or data.endswith(b"\n\n"):
        raise SourceError.from_spec(
            SourceProblem.NO_FINAL_LF, spec, detail=f"last bytes {data[-2:]!r}"
        )
    marker_offset = data.find(MARKER_PREFIX)
    if marker_offset >= 0:
        raise SourceError.from_spec(
            SourceProblem.HAS_MARKER, spec, detail=f"at byte {marker_offset}"
        )


def read_source(
    spec: ModuleSpec, *, limits: BundleLimits | None = None
) -> SourceSnapshot:
    """Read and fully validate one canonical source.

    Every documented invariant violation propagates as a :class:`SourceError` from
    the path, stat, read, and byte-check steps this function composes.

    Args:
        spec: The module to read.
        limits: Configured safeguards. Defaults to :class:`BundleLimits`.

    Returns:
        A validated snapshot of the exact accepted bytes.
    """
    effective_limits = BundleLimits() if limits is None else limits
    _check_path_encodable(spec)
    before = _stat_source(spec, effective_limits)
    data = _read_exact(spec, before, effective_limits)
    _check_bytes(spec, data)
    return SourceSnapshot(
        id=spec.id,
        lexical_source=spec.lexical_source,
        resolved_source=spec.source,
        data=data,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
    )


def read_sources(
    specs: tuple[ModuleSpec, ...], *, limits: BundleLimits | None = None
) -> tuple[SourceSnapshot, ...]:
    """Read every module in manifest order and enforce cross-source rules.

    Args:
        specs: Modules in manifest order.
        limits: Configured safeguards. Defaults to :class:`BundleLimits`.

    Returns:
        Validated snapshots in manifest order.

    Raises:
        SourceError: A source is invalid, two sources are byte-identical, or the
            accumulated size exceeds the configured bundle limit.
    """
    effective_limits = BundleLimits() if limits is None else limits
    snapshots: list[SourceSnapshot] = []
    total = 0
    by_digest: dict[str, str] = {}
    for spec in specs:
        snapshot = read_source(spec, limits=effective_limits)
        total += snapshot.size_bytes
        if total > effective_limits.max_bundle_bytes:
            raise SourceError.from_spec(
                SourceProblem.BUNDLE_TOO_LARGE,
                spec,
                detail=f"{total} > {effective_limits.max_bundle_bytes}",
            )
        previous = by_digest.get(snapshot.sha256)
        if previous is not None:
            raise SourceError.from_spec(
                SourceProblem.DUPLICATE_CONTENT,
                spec,
                detail=f"identical to module {previous!r} (sha256 {snapshot.sha256})",
            )
        by_digest[snapshot.sha256] = snapshot.id
        snapshots.append(snapshot)
    return tuple(snapshots)

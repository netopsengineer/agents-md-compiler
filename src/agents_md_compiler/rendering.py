"""Format-version-1 rendering and structural validation.

This module is pure: bytes and validated values in, bytes out. It performs no I/O,
so rendering cannot depend on the filesystem, the clock, the environment, or the
locale.

Validation is a structural parse rather than a substring search. A short module's
bytes can legitimately occur inside a longer module's text, so counting occurrences
would prove nothing about placement or ordering.
"""

import re

from agents_md_compiler.errors import RenderError, RenderProblem
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import (
    BUNDLE_TITLE,
    DO_NOT_EDIT_NOTICE,
    IDENTIFIER_PATTERN,
    RENDER_FORMAT_VERSION,
    SHA256_PATTERN,
    BundleLock,
    LockedModule,
    RenderedBundle,
    SourceSnapshot,
)

GENERATED_MARKER_PATTERN = re.compile(
    r"\A<!-- agents-md-compiler:generated format=(0|[1-9][0-9]{0,8}) -->\Z"
)
"""Recognizes a generated header line and captures its declared format value."""

MODULE_BEGIN_COUNT_TOKEN = b"<!-- agents-md-compiler:module-begin "
"""Counted to prove no extra module block hides in the output."""

HEADER_PROBE_BYTES = 512
"""Bytes inspected when classifying an existing file.

The probe must exceed the longest possible three-line header prefix, which is 84
bytes at the widest permitted format value.
"""


def generated_marker(format_version: int = RENDER_FORMAT_VERSION) -> str:
    """Compose the generated-file marker line.

    Args:
        format_version: Format value to declare.

    Returns:
        The marker line without its trailing LF.
    """
    return f"<!-- agents-md-compiler:generated format={format_version} -->"


def begin_marker(module: LockedModule) -> str:
    """Compose a module begin marker line.

    Args:
        module: The locked module to describe.

    Returns:
        The marker line without its trailing LF.
    """
    return (
        f"<!-- agents-md-compiler:module-begin id={module.id} "
        f"sha256={module.sha256} bytes={module.size_bytes} -->"
    )


def end_marker(module: LockedModule) -> str:
    """Compose a module end marker line.

    Args:
        module: The locked module to describe.

    Returns:
        The marker line without its trailing LF.
    """
    return f"<!-- agents-md-compiler:module-end id={module.id} -->"


def build_header(*, bundle_id: str, manifest_sha256: str, lock_sha256: str) -> bytes:
    """Compose the seven-line header block.

    Args:
        bundle_id: Validated bundle identifier.
        manifest_sha256: Digest of the exact manifest bytes.
        lock_sha256: Digest of the canonical lock bytes.

    Returns:
        The header bytes, ending with one LF.
    """
    lines = (
        BUNDLE_TITLE,
        "",
        generated_marker(),
        f"<!-- bundle-id: {bundle_id} -->",
        f"<!-- manifest-sha256: {manifest_sha256} -->",
        f"<!-- lock-sha256: {lock_sha256} -->",
        DO_NOT_EDIT_NOTICE,
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def _check_identity(
    module_id: str, digest: str, size_bytes: int, content_length: int
) -> None:
    """Refuse to place an unvalidated value inside a generated marker.

    Identifiers and digests are validated upstream, but the renderer refuses rather
    than escapes: escaping would imply that some invalid identifier is renderable,
    and no such case exists.

    Args:
        module_id: Module identifier destined for a marker.
        digest: Digest destined for a marker.
        size_bytes: Declared byte count destined for a marker.
        content_length: Actual length of the content being rendered.

    Raises:
        RenderError: An identifier, digest, or byte count is unusable.
    """
    if IDENTIFIER_PATTERN.match(module_id) is None:
        raise RenderError(RenderProblem.BAD_IDENTIFIER, detail=repr(module_id))
    if SHA256_PATTERN.match(digest) is None:
        raise RenderError(RenderProblem.BAD_DIGEST, detail=repr(digest))
    if size_bytes != content_length:
        raise RenderError(
            RenderProblem.BAD_SIZE,
            detail=f"declared {size_bytes}, actual {content_length}",
        )


def render_bundle(
    *,
    bundle_id: str,
    manifest_sha256: str,
    lock_sha256: str,
    snapshots: tuple[SourceSnapshot, ...],
) -> RenderedBundle:
    """Render one bundle from validated snapshots.

    Args:
        bundle_id: Validated bundle identifier.
        manifest_sha256: Digest of the exact manifest bytes.
        lock_sha256: Digest of the canonical lock bytes.
        snapshots: Validated snapshots in manifest order, which is output order.

    Returns:
        The rendered bundle and the identity it encodes.

    Raises:
        RenderError: An identifier, digest, or byte count is unusable.
    """
    if IDENTIFIER_PATTERN.match(bundle_id) is None:
        raise RenderError(RenderProblem.BAD_IDENTIFIER, detail=repr(bundle_id))
    if SHA256_PATTERN.match(manifest_sha256) is None:
        raise RenderError(RenderProblem.BAD_DIGEST, detail=repr(manifest_sha256))
    if SHA256_PATTERN.match(lock_sha256) is None:
        raise RenderError(RenderProblem.BAD_DIGEST, detail=repr(lock_sha256))
    chunks = [
        build_header(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            lock_sha256=lock_sha256,
        )
    ]
    modules: list[LockedModule] = []
    for snapshot in snapshots:
        _check_identity(
            snapshot.id, snapshot.sha256, snapshot.size_bytes, len(snapshot.data)
        )
        module = LockedModule(
            id=snapshot.id,
            resolved_source=str(snapshot.resolved_source),
            sha256=snapshot.sha256,
            size_bytes=snapshot.size_bytes,
        )
        modules.append(module)
        chunks.append(b"\n")
        chunks.append((begin_marker(module) + "\n").encode("ascii"))
        chunks.append(snapshot.data)
        chunks.append((end_marker(module) + "\n").encode("ascii"))
    data = b"".join(chunks)
    return RenderedBundle(
        data=data,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
        lock_sha256=lock_sha256,
        modules=tuple(modules),
    )


def validate_rendered(data: bytes, lock: BundleLock, lock_sha256: str) -> None:
    """Structurally verify rendered bytes against a lock.

    Proves that each locked source range appears exactly once, in order, with its
    recorded identity, and that nothing else is present.

    Args:
        data: Rendered bytes to verify.
        lock: The lock the bytes must satisfy.
        lock_sha256: Digest of that lock's canonical bytes.

    Raises:
        RenderError: The bytes do not match the format or the lock.
    """
    header = build_header(
        bundle_id=lock.bundle_id,
        manifest_sha256=lock.manifest_sha256,
        lock_sha256=lock_sha256,
    )
    if not data.startswith(header):
        raise RenderError(RenderProblem.HEADER_MISMATCH)
    marker_count = data.count(MODULE_BEGIN_COUNT_TOKEN)
    if marker_count != len(lock.modules):
        raise RenderError(
            RenderProblem.MODULE_COUNT_MISMATCH,
            detail=f"found {marker_count}, locked {len(lock.modules)}",
        )
    offset = len(header)
    for module in lock.modules:
        if not data.startswith(b"\n", offset):
            raise RenderError(RenderProblem.MISSING_SEPARATOR, detail=module.id)
        offset += 1
        begin = (begin_marker(module) + "\n").encode("ascii")
        if not data.startswith(begin, offset):
            raise RenderError(
                RenderProblem.MARKER_MISMATCH, detail=f"begin {module.id}"
            )
        offset += len(begin)
        content = data[offset : offset + module.size_bytes]
        if len(content) != module.size_bytes:
            raise RenderError(RenderProblem.TRUNCATED, detail=module.id)
        if sha256_bytes(content) != module.sha256:
            raise RenderError(RenderProblem.CONTENT_DIGEST_MISMATCH, detail=module.id)
        offset += module.size_bytes
        end = (end_marker(module) + "\n").encode("ascii")
        if not data.startswith(end, offset):
            raise RenderError(RenderProblem.MARKER_MISMATCH, detail=f"end {module.id}")
        offset += len(end)
    if offset != len(data):
        raise RenderError(
            RenderProblem.TRAILING_CONTENT, detail=f"{len(data) - offset} extra bytes"
        )


def declared_format(data: bytes) -> int | None:
    """Read the generated format value from candidate bundle bytes.

    Args:
        data: Bytes of an existing file.

    Returns:
        The declared format value, or ``None`` when the bytes carry no recognized
        generated header in the exact header position.
    """
    # Split on LF as bytes first. Decoding a fixed-size byte prefix can cut a
    # multibyte character in half, which misclassified a compiler-generated target
    # as unmanaged whenever module content crossed the probe boundary.
    lines = data[:HEADER_PROBE_BYTES].split(b"\n")
    if len(lines) < 4:
        return None
    try:
        title, blank, marker = (line.decode("ascii") for line in lines[:3])
    except UnicodeDecodeError:
        return None
    if title != BUNDLE_TITLE or blank != "":
        return None
    match = GENERATED_MARKER_PATTERN.match(marker)
    if match is None:
        return None
    return int(match.group(1))

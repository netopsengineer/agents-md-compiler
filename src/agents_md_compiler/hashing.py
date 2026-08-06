"""SHA-256 helpers.

Digests are lowercase hexadecimal throughout, because the lock, the rendered
markers, the receipts, and the ``--expect-target-sha256`` option all compare them
as strings.
"""

import hashlib
from pathlib import Path

READ_CHUNK_BYTES = 1024 * 1024
"""Chunk size used when digesting a file that is not held in memory."""


def sha256_bytes(data: bytes) -> str:
    """Digest an in-memory byte string.

    Args:
        data: Bytes to digest.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, chunk_bytes: int = READ_CHUNK_BYTES) -> str:
    """Digest a file without holding it in memory.

    Used for targets and backups, whose size this tool does not bound. Canonical
    sources are digested from their validated snapshot instead, so that hashing and
    rendering can never see different bytes.

    Args:
        path: File to digest.
        chunk_bytes: Read size per iteration.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()

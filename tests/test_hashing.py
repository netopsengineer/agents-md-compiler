"""SHA-256 helpers."""

import hashlib
from pathlib import Path

from agents_md_compiler import hashing

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_digests_are_lowercase_hexadecimal() -> None:
    digest = hashing.sha256_bytes(b"policy\n")
    assert digest == digest.lower()
    assert len(digest) == 64


def test_empty_input_has_the_known_digest() -> None:
    assert hashing.sha256_bytes(b"") == EMPTY_SHA256


def test_file_and_byte_digests_agree(tmp_path: Path) -> None:
    payload = b"# Policy\n\nBody.\n"
    target = tmp_path / "policy.md"
    target.write_bytes(payload)
    assert hashing.sha256_file(target) == hashing.sha256_bytes(payload)


def test_file_digest_streams_across_chunk_boundaries(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 40
    target = tmp_path / "binary.bin"
    target.write_bytes(payload)
    assert (
        hashing.sha256_file(target, chunk_bytes=7)
        == hashlib.sha256(payload).hexdigest()
    )


def test_empty_file_digest(tmp_path: Path) -> None:
    target = tmp_path / "empty.bin"
    target.write_bytes(b"")
    assert hashing.sha256_file(target) == EMPTY_SHA256

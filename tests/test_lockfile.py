"""Lock generation, canonical serialization, strict parsing, and comparison."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import GOLDEN, MANIFEST_FIXTURES, MODULE_FIXTURES, Bundle

from agents_md_compiler import lockfile
from agents_md_compiler.errors import (
    LockError,
    LockMissingError,
    LockProblem,
    LockStaleProblem,
)
from agents_md_compiler.manifest import load_manifest
from agents_md_compiler.models import LOCK_FORMAT_VERSION, BundleLock, BundleState
from agents_md_compiler.sources import read_sources


def build_from(manifest_path: Path) -> tuple[BundleLock, bytes]:
    """Build a lock and its canonical bytes from a manifest on disk.

    Args:
        manifest_path: Manifest to read.

    Returns:
        The lock and its canonical serialization.
    """
    manifest = load_manifest(manifest_path)
    snapshots = read_sources(manifest.modules)
    lock = lockfile.build_lock(manifest, snapshots)
    return lock, lockfile.serialize_lock(lock)


def test_serialization_matches_the_hand_authored_golden() -> None:
    lock, data = build_from(MANIFEST_FIXTURES / "minimal.toml")
    expected_text = (GOLDEN / "minimal.lock.json.tmpl").read_text(encoding="utf-8")
    for source_name in ("core.md", "python.md"):
        escaped_source = json.dumps(
            str(MODULE_FIXTURES / source_name), ensure_ascii=True
        )[1:-1]
        expected_text = expected_text.replace(
            f"__SOURCE_DIR__/{source_name}", escaped_source
        )
    expected = expected_text.encode("utf-8")
    assert data == expected
    assert lockfile.lock_digest(lock) == lockfile.sha256_bytes(expected)


def test_serialization_is_deterministic() -> None:
    first = build_from(MANIFEST_FIXTURES / "minimal.toml")[1]
    second = build_from(MANIFEST_FIXTURES / "minimal.toml")[1]
    assert first == second


def test_serialization_uses_the_documented_byte_conventions(bundle: Bundle) -> None:
    data = build_from(bundle.manifest)[1]
    text = data.decode("utf-8")
    assert data.endswith(b"\n")
    assert not data.endswith(b"\n\n")
    assert b"\r" not in data
    assert '\n  "bundle_id"' in text
    assert text.index('"bundle_id"') < text.index('"format_version"')
    assert text.index('"format_version"') < text.index('"manifest_sha256"')
    assert text.index('"manifest_sha256"') < text.index('"modules"')
    assert data == data.decode("ascii").encode("ascii")


def test_module_order_survives_key_sorting(three_module_bundle: Bundle) -> None:
    data = build_from(three_module_bundle.manifest)[1]
    document = json.loads(data)
    assert [module["id"] for module in document["modules"]] == [
        "core",
        "python",
        "extras",
    ]


def test_a_non_ascii_source_path_is_escaped(tmp_path: Path) -> None:
    module_dir = tmp_path / "policÿy"
    module_dir.mkdir()
    (module_dir / "core.md").write_bytes(b"# Core\n\nBody.\n")
    manifest = tmp_path / "global-agents.toml"
    manifest.write_bytes(
        b'schema_version = 1\nbundle_id = "escaped"\ndefault_target = "out/AGENTS.md"\n'
        b'\n[[modules]]\nid = "core"\nsource = "polic\xc3\xbfy/core.md"\n'
    )
    data = build_from(manifest)[1]
    assert b"\\u00ff" in data
    assert data == data.decode("ascii").encode("ascii")
    assert json.loads(data)["modules"][0]["resolved_source"].endswith("policÿy/core.md")


def test_round_trip_through_parse(bundle: Bundle) -> None:
    lock, data = build_from(bundle.manifest)
    assert lockfile.parse_lock(data) == lock


def test_the_format_version_is_recorded(bundle: Bundle) -> None:
    lock, _ = build_from(bundle.manifest)
    assert lockfile.parse_lock(lockfile.serialize_lock(lock)).format_version == (
        LOCK_FORMAT_VERSION
    )


PARSE_FAILURES = [
    pytest.param(b"{", LockProblem.SYNTAX, id="truncated-json"),
    pytest.param(b"[]", LockProblem.NOT_AN_OBJECT, id="array-root"),
    pytest.param(b'"text"', LockProblem.NOT_AN_OBJECT, id="string-root"),
    pytest.param(b"\xff\xfe", LockProblem.SYNTAX, id="not-utf8"),
    pytest.param(b"{}", LockProblem.MISSING_KEY, id="empty-object"),
]


@pytest.mark.parametrize(("payload", "problem"), PARSE_FAILURES)
def test_malformed_lock_bytes_are_refused(payload: bytes, problem: LockProblem) -> None:
    with pytest.raises(LockError) as raised:
        lockfile.parse_lock(payload)
    assert raised.value.problem is problem
    assert raised.value.state is BundleState.INVALID_LOCK


def mutated(bundle: Bundle, **changes: object) -> bytes:
    """Serialize a lock document with top-level keys replaced.

    Args:
        bundle: Bundle providing the base lock.
        **changes: Keys to set, or to delete when the value is ``...``.

    Returns:
        The mutated JSON bytes.
    """
    document = json.loads(build_from(bundle.manifest)[1])
    for key, value in changes.items():
        if value is ...:
            del document[key]
        else:
            document[key] = value
    return json.dumps(document).encode("utf-8")


STRUCTURAL_FAILURES = [
    pytest.param({"format_version": ...}, LockProblem.MISSING_KEY, id="no-format"),
    pytest.param({"format_version": "1"}, LockProblem.WRONG_TYPE, id="string-format"),
    pytest.param({"format_version": True}, LockProblem.WRONG_TYPE, id="bool-format"),
    pytest.param(
        {"format_version": 2},
        LockProblem.UNSUPPORTED_FORMAT_VERSION,
        id="future-format",
    ),
    pytest.param({"bundle_id": ...}, LockProblem.MISSING_KEY, id="no-bundle-id"),
    pytest.param({"bundle_id": 7}, LockProblem.WRONG_TYPE, id="int-bundle-id"),
    pytest.param(
        {"bundle_id": "Bad_Id"}, LockProblem.BAD_IDENTIFIER, id="bad-bundle-id"
    ),
    pytest.param(
        {"manifest_sha256": ...}, LockProblem.MISSING_KEY, id="no-manifest-sha"
    ),
    pytest.param({"manifest_sha256": "abc"}, LockProblem.BAD_DIGEST, id="short-sha"),
    pytest.param({"manifest_sha256": "A" * 64}, LockProblem.BAD_DIGEST, id="upper-sha"),
    pytest.param({"manifest_sha256": 1}, LockProblem.WRONG_TYPE, id="int-sha"),
    pytest.param({"modules": ...}, LockProblem.MISSING_KEY, id="no-modules"),
    pytest.param({"modules": []}, LockProblem.NO_MODULES, id="empty-modules"),
    pytest.param({"modules": {}}, LockProblem.NO_MODULES, id="object-modules"),
    pytest.param(
        {"modules": ["core"]}, LockProblem.MODULE_NOT_AN_OBJECT, id="string-module"
    ),
    pytest.param({"extra": True}, LockProblem.UNKNOWN_KEY, id="unknown-key"),
]


@pytest.mark.parametrize(("changes", "problem"), STRUCTURAL_FAILURES)
def test_structurally_invalid_locks_are_refused(
    bundle: Bundle, changes: dict[str, object], problem: LockProblem
) -> None:
    with pytest.raises(LockError) as raised:
        lockfile.parse_lock(mutated(bundle, **changes))
    assert raised.value.problem is problem


def module_mutated(bundle: Bundle, **changes: object) -> bytes:
    """Serialize a lock document with the first module's keys replaced.

    Args:
        bundle: Bundle providing the base lock.
        **changes: Keys to set, or to delete when the value is ``...``.

    Returns:
        The mutated JSON bytes.
    """
    document = json.loads(build_from(bundle.manifest)[1])
    for key, value in changes.items():
        if value is ...:
            del document["modules"][0][key]
        else:
            document["modules"][0][key] = value
    return json.dumps(document).encode("utf-8")


MODULE_FAILURES = [
    pytest.param({"id": ...}, LockProblem.MISSING_KEY, id="no-id"),
    pytest.param({"id": "Core"}, LockProblem.BAD_IDENTIFIER, id="bad-id"),
    pytest.param({"id": 3}, LockProblem.WRONG_TYPE, id="int-id"),
    pytest.param({"sha256": ...}, LockProblem.MISSING_KEY, id="no-sha"),
    pytest.param({"sha256": "z" * 64}, LockProblem.BAD_DIGEST, id="non-hex-sha"),
    pytest.param({"resolved_source": ...}, LockProblem.MISSING_KEY, id="no-source"),
    pytest.param({"resolved_source": ""}, LockProblem.WRONG_TYPE, id="empty-source"),
    pytest.param({"resolved_source": 5}, LockProblem.WRONG_TYPE, id="int-source"),
    pytest.param({"size_bytes": ...}, LockProblem.MISSING_KEY, id="no-size"),
    pytest.param({"size_bytes": 0}, LockProblem.BAD_SIZE, id="zero-size"),
    pytest.param({"size_bytes": -1}, LockProblem.BAD_SIZE, id="negative-size"),
    pytest.param({"size_bytes": 1.5}, LockProblem.BAD_SIZE, id="float-size"),
    pytest.param({"size_bytes": True}, LockProblem.BAD_SIZE, id="bool-size"),
    pytest.param({"unexpected": 1}, LockProblem.UNKNOWN_MODULE_KEY, id="unknown-key"),
]


@pytest.mark.parametrize(("changes", "problem"), MODULE_FAILURES)
def test_structurally_invalid_locked_modules_are_refused(
    bundle: Bundle, changes: dict[str, object], problem: LockProblem
) -> None:
    with pytest.raises(LockError) as raised:
        lockfile.parse_lock(module_mutated(bundle, **changes))
    assert raised.value.problem is problem


def test_reading_a_missing_lock_raises_lock_missing(tmp_path: Path) -> None:
    with pytest.raises(LockMissingError) as raised:
        lockfile.load_lock(tmp_path / "absent.lock.json")
    assert raised.value.state is BundleState.LOCK_MISSING


def test_reading_a_symlinked_lock_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.lock.json"
    real.write_bytes(b"{}")
    link = tmp_path / "linked.lock.json"
    link.symlink_to(real)
    with pytest.raises(LockError) as raised:
        lockfile.load_lock(link)
    assert raised.value.problem is LockProblem.SYMLINK


def test_reading_a_directory_lock_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "lock.json"
    directory.mkdir()
    with pytest.raises(LockError) as raised:
        lockfile.load_lock(directory)
    assert raised.value.problem is LockProblem.NOT_A_FILE


def test_reading_an_unreadable_lock_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "lock.json"
    lock.write_bytes(b"{}")
    lock.chmod(0o000)
    try:
        with pytest.raises(LockError) as raised:
            lockfile.load_lock(lock)
        assert raised.value.problem is LockProblem.UNREADABLE
    finally:
        lock.chmod(0o600)


def test_a_stat_failure_on_the_lock_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "lock.json"
    lock.write_bytes(b"{}")
    real_stat = Path.stat

    def _refuse(self: Path, *, follow_symlinks: bool = True) -> object:
        if self == lock:
            message = "Stale file handle"
            raise OSError(116, message)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _refuse)
    with pytest.raises(LockError) as raised:
        lockfile.load_lock(lock)
    assert raised.value.problem is LockProblem.UNREADABLE


def test_loading_a_valid_lock_from_disk(bundle: Bundle) -> None:
    lock, data = build_from(bundle.manifest)
    bundle.lock.write_bytes(data)
    assert lockfile.load_lock(bundle.lock) == lock


def test_comparison_reports_no_difference_for_equal_locks(bundle: Bundle) -> None:
    lock, _ = build_from(bundle.manifest)
    assert lockfile.compare_locks(lock, lock) is None


def test_comparison_detects_a_changed_manifest(bundle: Bundle) -> None:
    before, _ = build_from(bundle.manifest)
    bundle.manifest.write_bytes(bundle.manifest.read_bytes() + b"\n# note\n")
    after, _ = build_from(bundle.manifest)
    assert lockfile.compare_locks(before, after) is LockStaleProblem.MANIFEST_CHANGED


def test_comparison_detects_a_changed_source(bundle: Bundle) -> None:
    before, _ = build_from(bundle.manifest)
    bundle.modules["core"].write_bytes(b"# Core\n\nEdited body.\n")
    after, _ = build_from(bundle.manifest)
    assert lockfile.compare_locks(before, after) is LockStaleProblem.SOURCES_CHANGED


def test_comparison_detects_a_changed_bundle_id(bundle: Bundle) -> None:
    before, _ = build_from(bundle.manifest)
    after = replace(before, bundle_id="other-bundle")
    assert lockfile.compare_locks(before, after) is LockStaleProblem.BUNDLE_ID_CHANGED


def test_comparison_detects_a_changed_module_set(bundle: Bundle) -> None:
    # Built directly rather than through a manifest edit: any manifest edit also
    # changes the manifest digest, and MANIFEST_CHANGED is both checked first and
    # more specific. This isolates the module-set comparison itself.
    before, _ = build_from(bundle.manifest)
    after = replace(before, modules=before.modules[:1])
    assert lockfile.compare_locks(before, after) is LockStaleProblem.MODULE_SET_CHANGED


def test_comparison_detects_reordered_modules(three_module_bundle: Bundle) -> None:
    before, _ = build_from(three_module_bundle.manifest)
    after = replace(before, modules=tuple(reversed(before.modules)))
    assert lockfile.compare_locks(before, after) is LockStaleProblem.MODULE_SET_CHANGED


def test_comparison_detects_a_moved_source_path(bundle: Bundle) -> None:
    before, _ = build_from(bundle.manifest)
    moved = replace(before.modules[0], resolved_source="/elsewhere/core.md")
    after = replace(before, modules=(moved, *before.modules[1:]))
    assert lockfile.compare_locks(before, after) is LockStaleProblem.SOURCES_CHANGED


def test_comparison_detects_a_tampered_digest(bundle: Bundle) -> None:
    before, _ = build_from(bundle.manifest)
    tampered = replace(before.modules[0], sha256="0" * 64)
    after = replace(before, modules=(tampered, *before.modules[1:]))
    assert lockfile.compare_locks(before, after) is LockStaleProblem.SOURCES_CHANGED


def test_comparison_detects_a_tampered_size(bundle: Bundle) -> None:
    before, _ = build_from(bundle.manifest)
    tampered = replace(before.modules[0], size_bytes=before.modules[0].size_bytes + 1)
    after = replace(before, modules=(tampered, *before.modules[1:]))
    assert lockfile.compare_locks(before, after) is LockStaleProblem.SOURCES_CHANGED


def test_a_manifest_edit_takes_precedence_over_a_source_difference(
    bundle: Bundle,
) -> None:
    before, _ = build_from(bundle.manifest)
    bundle.manifest.write_bytes(bundle.manifest.read_bytes() + b"\n# note\n")
    bundle.modules["core"].write_bytes(b"# Core\n\nEdited.\n")
    after, _ = build_from(bundle.manifest)
    assert lockfile.compare_locks(before, after) is LockStaleProblem.MANIFEST_CHANGED

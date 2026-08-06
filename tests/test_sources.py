"""Canonical source validation: path, type, encoding, and content invariants.

Byte-hostile inputs are written here as bytes rather than committed as fixture
files, because the repository's end-of-file, byte-order-marker, line-ending, and
trailing-whitespace hooks would silently repair a tracked file and quietly delete
the very condition under test.
"""

import os
from pathlib import Path

import pytest
from conftest import CORE_TEXT, write_text_file

from agents_md_compiler.errors import SourceError, SourceProblem
from agents_md_compiler.models import BundleLimits, BundleState, ModuleSpec
from agents_md_compiler.sources import read_source, read_sources

VALID = b"# Core\n\nBody text.\n"


def spec_for(
    path: Path, *, module_id: str = "core", lexical: str | None = None
) -> ModuleSpec:
    """Build a module spec for one source path.

    Args:
        path: Resolved source path.
        module_id: Module identifier.
        lexical: Path as a manifest would have written it.

    Returns:
        The module spec.
    """
    return ModuleSpec(
        id=module_id,
        lexical_source=str(path) if lexical is None else lexical,
        source=path,
    )


def write_bytes_file(path: Path, payload: bytes) -> Path:
    """Write exact bytes.

    Args:
        path: Destination path.
        payload: Bytes to write.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_a_valid_source_is_snapshotted_exactly(tmp_path: Path) -> None:
    source = write_bytes_file(tmp_path / "core.md", VALID)
    snapshot = read_source(spec_for(source, lexical="modules/core.md"))
    assert snapshot.data == VALID
    assert snapshot.size_bytes == len(VALID)
    assert snapshot.id == "core"
    assert snapshot.lexical_source == "modules/core.md"
    assert snapshot.resolved_source == source


BYTE_CASES = [
    pytest.param(b"\xef\xbb\xbf# Core\n", SourceProblem.HAS_BOM, id="utf8-bom"),
    pytest.param(b"# Core\n\x00tail\n", SourceProblem.HAS_NUL, id="nul-byte"),
    pytest.param(b"# Core\r\n\r\nBody.\r\n", SourceProblem.HAS_CR, id="crlf"),
    pytest.param(b"# Core\rBody\n", SourceProblem.HAS_CR, id="lone-cr"),
    pytest.param(b"# Core\n\nCaf\xe9\n", SourceProblem.NOT_UTF8, id="invalid-utf8"),
    pytest.param(b"# Core\n\nBody.", SourceProblem.NO_FINAL_LF, id="no-final-lf"),
    pytest.param(b"# Core\n\nBody.\n\n", SourceProblem.NO_FINAL_LF, id="two-final-lfs"),
    pytest.param(
        b"# Core\n\n<!-- agents-md-compiler:module-begin id=x -->\n",
        SourceProblem.HAS_MARKER,
        id="marker-collision",
    ),
    pytest.param(
        b"prefix <!-- agents-md-compiler:generated format=1 --> suffix\n",
        SourceProblem.HAS_MARKER,
        id="marker-mid-line",
    ),
]


@pytest.mark.parametrize(("payload", "problem"), BYTE_CASES)
def test_byte_invariants_are_enforced(
    tmp_path: Path, payload: bytes, problem: SourceProblem
) -> None:
    source = write_bytes_file(tmp_path / "core.md", payload)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(source))
    assert raised.value.problem is problem
    assert raised.value.state is BundleState.INVALID_SOURCE


def test_an_empty_source_is_refused(tmp_path: Path) -> None:
    source = write_bytes_file(tmp_path / "core.md", b"")
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(source))
    assert raised.value.problem is SourceProblem.EMPTY


def test_a_missing_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(tmp_path / "absent.md"))
    assert raised.value.problem is SourceProblem.MISSING


def test_a_directory_source_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "core.md"
    directory.mkdir()
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(directory))
    assert raised.value.problem is SourceProblem.NOT_A_FILE


def test_a_fifo_source_is_refused(tmp_path: Path) -> None:
    fifo = tmp_path / "core.md"
    os.mkfifo(fifo)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(fifo))
    assert raised.value.problem is SourceProblem.NOT_A_FILE


def test_a_symlinked_source_is_refused_and_reports_both_paths(tmp_path: Path) -> None:
    real = write_bytes_file(tmp_path / "real.md", VALID)
    link = tmp_path / "core.md"
    link.symlink_to(real)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(link, lexical="modules/core.md"))
    assert raised.value.problem is SourceProblem.SYMLINK
    assert str(real) in str(raised.value)
    assert raised.value.paths is not None
    assert raised.value.paths.lexical == "modules/core.md"
    assert raised.value.paths.resolved == str(link)


def test_a_source_beneath_a_symlinked_ancestor_is_accepted(tmp_path: Path) -> None:
    real_directory = tmp_path / "real"
    source = write_bytes_file(real_directory / "core.md", VALID)
    linked_directory = tmp_path / "modules"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    linked_source = linked_directory / "core.md"
    snapshot = read_source(spec_for(linked_source, lexical="modules/core.md"))

    assert snapshot.data == VALID
    assert snapshot.lexical_source == "modules/core.md"
    assert snapshot.resolved_source == linked_source
    assert source.samefile(snapshot.resolved_source)


def test_an_unreadable_source_is_refused(tmp_path: Path) -> None:
    source = write_bytes_file(tmp_path / "core.md", VALID)
    source.chmod(0o000)
    try:
        with pytest.raises(SourceError) as raised:
            read_source(spec_for(source))
        assert raised.value.problem is SourceProblem.UNREADABLE
    finally:
        source.chmod(0o600)


def test_a_stat_failure_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_bytes_file(tmp_path / "core.md", VALID)
    real_stat = Path.stat

    def _refuse(self: Path, *, follow_symlinks: bool = True) -> object:
        if self == source:
            message = "Stale file handle"
            raise OSError(116, message)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _refuse)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(source))
    assert raised.value.problem is SourceProblem.UNREADABLE


def test_an_oversized_source_is_refused_by_its_stat_size(tmp_path: Path) -> None:
    source = write_bytes_file(tmp_path / "core.md", VALID)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(source), limits=BundleLimits(max_source_bytes=4))
    assert raised.value.problem is SourceProblem.TOO_LARGE
    assert "4" in str(raised.value)


def test_a_source_at_exactly_the_limit_is_accepted(tmp_path: Path) -> None:
    source = write_bytes_file(tmp_path / "core.md", VALID)
    snapshot = read_source(
        spec_for(source), limits=BundleLimits(max_source_bytes=len(VALID))
    )
    assert snapshot.size_bytes == len(VALID)


def test_a_source_whose_identity_changes_between_stat_and_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A swapped file keeps the same path but changes inode. Faking fstat is the
    # only deterministic way to exercise that race without a real scheduler window.
    source = write_bytes_file(tmp_path / "core.md", VALID)
    real_fstat = os.fstat

    def _swapped(fileno: int) -> os.stat_result:
        real = real_fstat(fileno)
        fields = list(real)
        fields[1] = real.st_ino + 1
        return os.stat_result(tuple(fields))

    monkeypatch.setattr(os, "fstat", _swapped)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(source))
    assert raised.value.problem is SourceProblem.CHANGED_WHILE_READING


def test_a_source_truncated_between_stat_and_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_bytes_file(tmp_path / "core.md", VALID)
    real_fstat = os.fstat

    def _lie(fileno: int) -> os.stat_result:
        real = real_fstat(fileno)
        fields = list(real)
        fields[6] = real.st_size + 10
        return os.stat_result(tuple(fields))

    monkeypatch.setattr(os, "fstat", _lie)
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(source))
    assert raised.value.problem is SourceProblem.CHANGED_WHILE_READING


def test_a_path_that_is_not_utf8_representable_is_refused(tmp_path: Path) -> None:
    surrogate = Path(str(tmp_path) + "/" + "core\udcff.md")
    with pytest.raises(SourceError) as raised:
        read_source(spec_for(surrogate))
    assert raised.value.problem is SourceProblem.PATH_NOT_UTF8


def test_reading_many_sources_preserves_order(tmp_path: Path) -> None:
    specs = tuple(
        spec_for(
            write_bytes_file(
                tmp_path / f"m{index}.md", f"# M{index}\n\nBody.\n".encode()
            ),
            module_id=f"m{index}",
        )
        for index in range(3)
    )
    snapshots = read_sources(specs)
    assert [snapshot.id for snapshot in snapshots] == ["m0", "m1", "m2"]


def test_duplicate_content_across_modules_is_refused(tmp_path: Path) -> None:
    first = write_bytes_file(tmp_path / "a.md", VALID)
    second = write_bytes_file(tmp_path / "b.md", VALID)
    with pytest.raises(SourceError) as raised:
        read_sources((spec_for(first, module_id="a"), spec_for(second, module_id="b")))
    assert raised.value.problem is SourceProblem.DUPLICATE_CONTENT
    assert "'a'" in str(raised.value)


def test_the_bundle_size_limit_is_enforced_across_sources(tmp_path: Path) -> None:
    specs = tuple(
        spec_for(
            write_bytes_file(
                tmp_path / f"m{index}.md", f"# M{index}\n\nBody.\n".encode()
            ),
            module_id=f"m{index}",
        )
        for index in range(3)
    )
    with pytest.raises(SourceError) as raised:
        read_sources(specs, limits=BundleLimits(max_bundle_bytes=30))
    assert raised.value.problem is SourceProblem.BUNDLE_TOO_LARGE


def test_a_bundle_at_exactly_the_limit_is_accepted(tmp_path: Path) -> None:
    source = write_text_file(tmp_path / "core.md", CORE_TEXT)
    total = source.stat().st_size
    snapshots = read_sources(
        (spec_for(source),), limits=BundleLimits(max_bundle_bytes=total)
    )
    assert snapshots[0].size_bytes == total

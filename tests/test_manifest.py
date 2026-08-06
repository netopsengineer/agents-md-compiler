"""Strict manifest parsing: every documented rejection branch."""

from pathlib import Path

import pytest
from conftest import INVALID_MANIFESTS, MANIFEST_FIXTURES, Bundle, write_text_file

from agents_md_compiler.errors import ManifestError, ManifestProblem
from agents_md_compiler.manifest import load_manifest
from agents_md_compiler.models import BundleLimits, BundleState

EXPECTED_PROBLEM = {
    "unknown-top-key.toml": ManifestProblem.UNKNOWN_KEY,
    "unknown-module-key.toml": ManifestProblem.UNKNOWN_MODULE_KEY,
    "schema-version-string.toml": ManifestProblem.WRONG_TYPE,
    "schema-version-bool.toml": ManifestProblem.WRONG_TYPE,
    "schema-version-float.toml": ManifestProblem.WRONG_TYPE,
    "schema-version-unsupported.toml": ManifestProblem.UNSUPPORTED_SCHEMA_VERSION,
    "schema-version-missing.toml": ManifestProblem.MISSING_KEY,
    "bundle-id-missing.toml": ManifestProblem.MISSING_KEY,
    "bundle-id-pattern.toml": ManifestProblem.BAD_IDENTIFIER,
    "bundle-id-type.toml": ManifestProblem.WRONG_TYPE,
    "bundle-id-empty.toml": ManifestProblem.BLANK_VALUE,
    "default-target-missing.toml": ManifestProblem.MISSING_KEY,
    "default-target-blank.toml": ManifestProblem.BLANK_VALUE,
    "default-target-type.toml": ManifestProblem.WRONG_TYPE,
    "modules-missing.toml": ManifestProblem.MISSING_KEY,
    "modules-empty.toml": ManifestProblem.NO_MODULES,
    "modules-not-array.toml": ManifestProblem.NO_MODULES,
    "modules-not-tables.toml": ManifestProblem.MODULE_NOT_A_TABLE,
    "module-id-missing.toml": ManifestProblem.MISSING_KEY,
    "module-id-pattern.toml": ManifestProblem.BAD_IDENTIFIER,
    "module-id-type.toml": ManifestProblem.WRONG_TYPE,
    "module-id-too-long.toml": ManifestProblem.BAD_IDENTIFIER,
    "module-source-missing.toml": ManifestProblem.MISSING_KEY,
    "module-source-blank.toml": ManifestProblem.BLANK_VALUE,
    "module-source-type.toml": ManifestProblem.WRONG_TYPE,
    "duplicate-module-id.toml": ManifestProblem.DUPLICATE_MODULE_ID,
    "duplicate-source-path.toml": ManifestProblem.DUPLICATE_SOURCE_PATH,
}


def test_the_expectation_table_covers_every_committed_invalid_fixture() -> None:
    on_disk = {path.name for path in INVALID_MANIFESTS.glob("*.toml")}
    assert on_disk == set(EXPECTED_PROBLEM)


@pytest.mark.parametrize("fixture_name", sorted(EXPECTED_PROBLEM))
def test_every_invalid_fixture_is_rejected_with_its_documented_problem(
    fixture_name: str,
) -> None:
    with pytest.raises(ManifestError) as raised:
        load_manifest(INVALID_MANIFESTS / fixture_name)
    assert raised.value.problem is EXPECTED_PROBLEM[fixture_name]
    assert raised.value.state is BundleState.INVALID_MANIFEST


def test_a_valid_manifest_resolves_sources_against_its_own_directory() -> None:
    manifest = load_manifest(MANIFEST_FIXTURES / "minimal.toml")
    assert manifest.bundle_id == "fixture-bundle"
    assert manifest.schema_version == 1
    assert [module.id for module in manifest.modules] == ["core", "python"]
    for module in manifest.modules:
        assert module.source.is_absolute()
        assert module.source.parent == (MANIFEST_FIXTURES.parent / "modules")
        assert module.lexical_source.startswith("../modules/")


def test_manifest_order_is_preserved() -> None:
    manifest = load_manifest(MANIFEST_FIXTURES / "three-modules.toml")
    assert [module.id for module in manifest.modules] == ["core", "python", "extras"]


def test_the_manifest_digest_covers_the_exact_bytes(bundle: Bundle) -> None:
    first = load_manifest(bundle.manifest)
    bundle.manifest.write_bytes(bundle.manifest.read_bytes() + b"\n# comment\n")
    second = load_manifest(bundle.manifest)
    assert first.sha256 != second.sha256
    assert second.size_bytes > first.size_bytes


def test_relative_resolution_ignores_the_process_directory(
    bundle: Bundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    from_cwd = load_manifest(bundle.manifest)
    monkeypatch.chdir(bundle.root)
    from_manifest_dir = load_manifest(bundle.manifest)
    assert [m.source for m in from_cwd.modules] == [
        m.source for m in from_manifest_dir.modules
    ]


def test_a_tilde_source_expands_to_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    write_text_file(fake_home / "policy" / "core.md", "# Home\n\nBody.\n")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    manifest = write_text_file(
        tmp_path / "global-agents.toml",
        'schema_version = 1\nbundle_id = "tilde"\ndefault_target = "out/AGENTS.md"\n'
        '\n[[modules]]\nid = "core"\nsource = "~/policy/core.md"\n',
    )
    parsed = load_manifest(manifest)
    assert parsed.modules[0].source == fake_home / "policy" / "core.md"
    assert parsed.modules[0].lexical_source == "~/policy/core.md"


def test_a_tilde_default_target_expands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    write_text_file(tmp_path / "core.md", "# Core\n\nBody.\n")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    manifest = write_text_file(
        tmp_path / "global-agents.toml",
        'schema_version = 1\nbundle_id = "tilde"\ndefault_target = "~/.codex/AGENTS.md"\n'
        '\n[[modules]]\nid = "core"\nsource = "core.md"\n',
    )
    parsed = load_manifest(manifest)
    assert parsed.default_target == fake_home / ".codex" / "AGENTS.md"
    assert parsed.lexical_default_target == "~/.codex/AGENTS.md"


def test_an_absolute_source_is_used_as_written(tmp_path: Path) -> None:
    source = write_text_file(tmp_path / "abs" / "core.md", "# Abs\n\nBody.\n")
    manifest = write_text_file(
        tmp_path / "global-agents.toml",
        f'schema_version = 1\nbundle_id = "abs"\ndefault_target = "out/AGENTS.md"\n'
        f'\n[[modules]]\nid = "core"\nsource = "{source}"\n',
    )
    assert load_manifest(manifest).modules[0].source == source


def test_a_missing_manifest_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(ManifestError) as raised:
        load_manifest(tmp_path / "absent.toml")
    assert raised.value.problem is ManifestProblem.UNREADABLE


def test_a_directory_manifest_is_not_a_file(tmp_path: Path) -> None:
    directory = tmp_path / "global-agents.toml"
    directory.mkdir()
    with pytest.raises(ManifestError) as raised:
        load_manifest(directory)
    assert raised.value.problem is ManifestProblem.NOT_A_FILE


def test_a_symlinked_manifest_is_refused(tmp_path: Path) -> None:
    real = write_text_file(tmp_path / "real.toml", "schema_version = 1\n")
    link = tmp_path / "linked.toml"
    link.symlink_to(real)
    with pytest.raises(ManifestError) as raised:
        load_manifest(link)
    assert raised.value.problem is ManifestProblem.NOT_A_FILE


def test_malformed_toml_is_a_syntax_error(tmp_path: Path) -> None:
    # Written here rather than committed: the repository's check-toml hook parses
    # every tracked .toml file, so a syntactically broken fixture cannot be tracked.
    manifest = tmp_path / "broken.toml"
    manifest.write_bytes(b'schema_version = 1\nbundle_id = "unterminated\n')
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest)
    assert raised.value.problem is ManifestProblem.SYNTAX


def test_non_utf8_manifest_bytes_are_a_syntax_error(tmp_path: Path) -> None:
    manifest = tmp_path / "latin1.toml"
    manifest.write_bytes(b'schema_version = 1\nbundle_id = "caf\xe9"\n')
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest)
    assert raised.value.problem is ManifestProblem.SYNTAX
    assert "UTF-8" in str(raised.value)


def test_an_oversized_manifest_is_refused(tmp_path: Path) -> None:
    manifest = write_text_file(
        tmp_path / "global-agents.toml", "schema_version = 1\n" + "# pad\n" * 100
    )
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest, limits=BundleLimits(max_manifest_bytes=32))
    assert raised.value.problem is ManifestProblem.TOO_LARGE


def test_too_many_modules_is_refused(tmp_path: Path) -> None:
    for index in range(3):
        write_text_file(tmp_path / f"m{index}.md", f"# M{index}\n\nBody {index}.\n")
    manifest = write_text_file(
        tmp_path / "global-agents.toml",
        'schema_version = 1\nbundle_id = "many"\ndefault_target = "out/AGENTS.md"\n'
        + "".join(
            f'\n[[modules]]\nid = "m{index}"\nsource = "m{index}.md"\n'
            for index in range(3)
        ),
    )
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest, limits=BundleLimits(max_modules=2))
    assert raised.value.problem is ManifestProblem.TOO_MANY_MODULES


def test_the_error_reports_both_path_forms(tmp_path: Path) -> None:
    manifest = write_text_file(tmp_path / "global-agents.toml", "schema_version = 2\n")
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest, lexical_path="global-agents.toml")
    assert raised.value.paths is not None
    assert raised.value.paths.lexical == "global-agents.toml"
    assert raised.value.paths.resolved == str(manifest)


def test_an_unreadable_manifest_reports_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = write_text_file(tmp_path / "global-agents.toml", "schema_version = 1\n")
    real_read_bytes = Path.read_bytes

    def _refuse(self: Path) -> bytes:
        if self == manifest:
            message = "Permission denied"
            raise PermissionError(13, message)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _refuse)
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest)
    assert raised.value.problem is ManifestProblem.UNREADABLE


def test_a_stat_failure_reports_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = write_text_file(tmp_path / "global-agents.toml", "schema_version = 1\n")
    real_stat = Path.stat

    def _refuse(self: Path, *, follow_symlinks: bool = True) -> object:
        if self == manifest:
            message = "Stale file handle"
            raise OSError(116, message)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _refuse)
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest)
    assert raised.value.problem is ManifestProblem.UNREADABLE

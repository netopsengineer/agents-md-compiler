"""Compilation orchestration, target inspection, and state precedence."""

from pathlib import Path

import pytest
from conftest import CORE_TEXT, Bundle, make_bundle, write_text_file

from agents_md_compiler.errors import (
    LockError,
    LockProblem,
    ManifestError,
    ManifestProblem,
    ShadowedError,
    SourceError,
    SourceProblem,
    TargetError,
    TargetProblem,
)
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.lockfile import serialize_lock
from agents_md_compiler.manifest import load_manifest
from agents_md_compiler.models import BundleState, BundleStatus, TargetKind
from agents_md_compiler.state import (
    compile_bundle,
    evaluate,
    inspect_override,
    inspect_target,
    require_not_shadowed,
    require_target_parent,
)


def write_lock(bundle: Bundle) -> None:
    """Write a fresh, matching lock for a bundle.

    Args:
        bundle: The bundle to lock.
    """
    manifest = load_manifest(bundle.manifest)
    compiled = compile_bundle(manifest)
    bundle.lock.write_bytes(compiled.lock_bytes)


def install_rendered(bundle: Bundle) -> None:
    """Write the rendered bundle to its target, bypassing the installer.

    Args:
        bundle: The bundle to render and place.
    """
    compiled = compile_bundle(load_manifest(bundle.manifest))
    bundle.target.parent.mkdir(parents=True, exist_ok=True)
    bundle.target.write_bytes(compiled.rendered.data)


def evaluate_bundle(bundle: Bundle) -> BundleStatus:
    """Evaluate a bundle with its default lock and target.

    Args:
        bundle: The bundle to evaluate.

    Returns:
        The evaluated status.
    """
    return evaluate(load_manifest(bundle.manifest), lock_path=bundle.lock)


def test_compilation_produces_a_matching_lock_and_render(bundle: Bundle) -> None:
    compiled = compile_bundle(load_manifest(bundle.manifest))
    assert compiled.lock_bytes == serialize_lock(compiled.lock)
    assert compiled.rendered.lock_sha256 == compiled.lock_sha256
    assert [module.id for module in compiled.lock.modules] == ["core", "python"]


def test_a_source_that_aliases_the_target_is_refused(tmp_path: Path) -> None:
    target = write_text_file(tmp_path / "policy" / "modules" / "core.md", CORE_TEXT)
    manifest = write_text_file(
        tmp_path / "policy" / "global-agents.toml",
        'schema_version = 1\nbundle_id = "alias"\ndefault_target = "modules/core.md"\n'
        '\n[[modules]]\nid = "core"\nsource = "modules/core.md"\n',
    )
    with pytest.raises(ManifestError) as raised:
        compile_bundle(load_manifest(manifest))
    assert raised.value.problem is ManifestProblem.TARGET_ALIASES_SOURCE
    assert target.exists()


def test_an_explicit_target_that_aliases_a_source_is_refused(bundle: Bundle) -> None:
    with pytest.raises(ManifestError) as raised:
        compile_bundle(load_manifest(bundle.manifest), target=bundle.modules["core"])
    assert raised.value.problem is ManifestProblem.TARGET_ALIASES_SOURCE


def test_compilation_propagates_a_source_failure(bundle: Bundle) -> None:
    bundle.modules["core"].write_bytes(b"# Core\n\nNo final newline.")
    with pytest.raises(SourceError) as raised:
        compile_bundle(load_manifest(bundle.manifest))
    assert raised.value.problem is SourceProblem.NO_FINAL_LF


def test_inspecting_a_missing_target(tmp_path: Path) -> None:
    inspection = inspect_target(tmp_path / "AGENTS.md")
    assert inspection.kind is TargetKind.MISSING
    assert inspection.sha256 is None
    assert inspection.size_bytes is None
    assert inspection.mode is None
    assert inspection.declared_format is None


def test_a_target_with_a_missing_parent_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "AGENTS.md"
    with pytest.raises(TargetError) as raised:
        require_target_parent(target, lexical="missing/AGENTS.md")
    assert raised.value.problem is TargetProblem.PARENT_MISSING
    assert raised.value.paths is not None
    assert raised.value.paths.lexical == "missing/AGENTS.md"


def test_a_target_parent_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    parent = write_text_file(tmp_path / "not-a-directory", "# File\n")
    with pytest.raises(TargetError) as raised:
        require_target_parent(parent / "AGENTS.md")
    assert raised.value.problem is TargetProblem.PARENT_NOT_A_DIRECTORY


def test_an_unreadable_target_parent_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "target-parent"
    parent.mkdir()
    target = parent / "AGENTS.md"
    real_stat = Path.stat

    def _refuse(self: Path, *, follow_symlinks: bool = True) -> object:
        if self == parent:
            message = "Stale file handle"
            raise OSError(116, message)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _refuse)
    with pytest.raises(TargetError) as raised:
        require_target_parent(target)
    assert raised.value.problem is TargetProblem.UNREADABLE


def test_inspecting_a_managed_target(bundle: Bundle) -> None:
    install_rendered(bundle)
    inspection = inspect_target(bundle.target)
    assert inspection.kind is TargetKind.MANAGED
    assert inspection.declared_format == 2
    assert inspection.sha256 is not None
    assert inspection.mode is not None


def test_inspecting_an_unmanaged_target(tmp_path: Path) -> None:
    target = write_text_file(tmp_path / "AGENTS.md", "# Hand written\n\nBody.\n")
    inspection = inspect_target(target)
    assert inspection.kind is TargetKind.UNMANAGED
    assert inspection.declared_format is None


def test_a_format_1_target_remains_managed(tmp_path: Path) -> None:
    target = write_text_file(
        tmp_path / "AGENTS.md",
        "# Global Agent Instructions\n\n"
        "<!-- agents-md-compiler:generated format=1 -->\n",
    )
    inspection = inspect_target(target)
    assert inspection.kind is TargetKind.MANAGED
    assert inspection.declared_format == 1


def test_a_future_format_target_is_unmanaged(tmp_path: Path) -> None:
    target = write_text_file(
        tmp_path / "AGENTS.md",
        "# Agent Instructions\n\n<!-- agents-md-compiler:generated format=99 -->\n",
    )
    inspection = inspect_target(target)
    assert inspection.kind is TargetKind.UNMANAGED
    assert inspection.declared_format == 99


def test_a_symlinked_target_is_refused(tmp_path: Path) -> None:
    real = write_text_file(tmp_path / "real.md", "# Real\n\nBody.\n")
    link = tmp_path / "AGENTS.md"
    link.symlink_to(real)
    with pytest.raises(TargetError) as raised:
        inspect_target(link, lexical="~/.codex/AGENTS.md")
    assert raised.value.problem is TargetProblem.SYMLINK
    assert raised.value.paths is not None
    assert raised.value.paths.lexical == "~/.codex/AGENTS.md"


def test_a_directory_target_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "AGENTS.md"
    directory.mkdir()
    with pytest.raises(TargetError) as raised:
        inspect_target(directory)
    assert raised.value.problem is TargetProblem.NOT_A_FILE


def test_an_unreadable_target_is_refused(tmp_path: Path) -> None:
    target = write_text_file(tmp_path / "AGENTS.md", "# Body\n")
    target.chmod(0o000)
    try:
        with pytest.raises(TargetError) as raised:
            inspect_target(target)
        assert raised.value.problem is TargetProblem.UNREADABLE
    finally:
        target.chmod(0o600)


def test_a_stat_failure_on_the_target_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = write_text_file(tmp_path / "AGENTS.md", "# Body\n")
    real_stat = Path.stat

    def _refuse(self: Path, *, follow_symlinks: bool = True) -> object:
        if self == target:
            message = "Stale file handle"
            raise OSError(116, message)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _refuse)
    with pytest.raises(TargetError) as raised:
        inspect_target(target)
    assert raised.value.problem is TargetProblem.UNREADABLE


def test_override_detection_applies_only_to_a_global_agents_file(
    tmp_path: Path,
) -> None:
    assert inspect_override(tmp_path / "other.md").path is None
    assert inspect_override(tmp_path / "other.md").present is False


def test_an_absent_override_is_not_present(tmp_path: Path) -> None:
    inspection = inspect_override(tmp_path / "AGENTS.md")
    assert inspection.path == tmp_path / "AGENTS.override.md"
    assert inspection.present is False


def test_an_empty_override_is_not_shadowing(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.override.md").write_bytes(b"")
    assert inspect_override(tmp_path / "AGENTS.md").present is False


def test_a_non_empty_override_is_shadowing(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.override.md").write_bytes(b"# Override\n")
    assert inspect_override(tmp_path / "AGENTS.md").present is True


def test_require_not_shadowed_passes_when_absent(tmp_path: Path) -> None:
    require_not_shadowed(
        inspect_override(tmp_path / "AGENTS.md"), tmp_path / "AGENTS.md"
    )


def test_require_not_shadowed_refuses_when_present(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.override.md").write_bytes(b"# Override\n")
    with pytest.raises(ShadowedError) as raised:
        require_not_shadowed(
            inspect_override(tmp_path / "AGENTS.md"), tmp_path / "AGENTS.md"
        )
    assert raised.value.state is BundleState.SHADOWED


def test_a_missing_lock_is_reported_before_any_target_state(bundle: Bundle) -> None:
    status = evaluate_bundle(bundle)
    assert status.state is BundleState.LOCK_MISSING
    assert status.lock_present is False
    assert status.lock_matches is False
    assert status.target is None


def test_a_stale_lock_after_a_source_edit(bundle: Bundle) -> None:
    write_lock(bundle)
    bundle.modules["core"].write_bytes(b"# Core\n\nEdited body.\n")
    assert evaluate_bundle(bundle).state is BundleState.LOCK_STALE


def test_a_stale_lock_after_a_manifest_comment(bundle: Bundle) -> None:
    write_lock(bundle)
    bundle.manifest.write_bytes(bundle.manifest.read_bytes() + b"\n# reviewed\n")
    assert evaluate_bundle(bundle).state is BundleState.LOCK_STALE


def test_a_lock_reserialized_with_different_bytes_is_stale(bundle: Bundle) -> None:
    write_lock(bundle)
    # Same content, different serialization: the digest recorded in the rendered
    # header is over the canonical bytes, so a reformatted lock is stale.
    bundle.lock.write_bytes(bundle.lock.read_bytes().replace(b"\n  ", b"\n    "))
    assert evaluate_bundle(bundle).state is BundleState.LOCK_STALE


def test_a_missing_target_with_a_matching_lock(bundle: Bundle) -> None:
    bundle.target.parent.mkdir(parents=True)
    write_lock(bundle)
    status = evaluate_bundle(bundle)
    assert status.state is BundleState.MISSING
    assert status.lock_matches is True
    assert status.target is not None
    assert status.target.kind is TargetKind.MISSING


def test_a_current_target(bundle: Bundle) -> None:
    write_lock(bundle)
    install_rendered(bundle)
    status = evaluate_bundle(bundle)
    assert status.state is BundleState.CURRENT
    assert status.target is not None
    assert status.compiled is not None
    assert status.target.sha256 == status.compiled.rendered.sha256


def test_a_drifted_target(bundle: Bundle) -> None:
    write_lock(bundle)
    install_rendered(bundle)
    bundle.target.write_bytes(bundle.target.read_bytes() + b"\nappended\n")
    assert evaluate_bundle(bundle).state is BundleState.DRIFTED


def test_a_managed_target_uses_its_header_and_whole_file_digest(
    bundle: Bundle,
) -> None:
    write_lock(bundle)
    malformed = (
        b"# Global Agent Instructions\n\n"
        b"<!-- agents-md-compiler:generated format=1 -->\n"
        b"not a structurally valid rendered bundle\n"
    )
    bundle.target.parent.mkdir(parents=True, exist_ok=True)
    bundle.target.write_bytes(malformed)

    status = evaluate_bundle(bundle)

    assert status.state is BundleState.DRIFTED
    assert status.target is not None
    assert status.target.kind is TargetKind.MANAGED
    assert status.target.sha256 == sha256_bytes(malformed)


def test_an_unmanaged_target(bundle: Bundle) -> None:
    write_lock(bundle)
    bundle.target.parent.mkdir(parents=True, exist_ok=True)
    bundle.target.write_bytes(b"# Hand written\n\nBody.\n")
    assert evaluate_bundle(bundle).state is BundleState.UNMANAGED_TARGET


def test_shadowing_outranks_every_target_state(tmp_path: Path) -> None:
    shadowed = make_bundle(tmp_path, default_target="out/AGENTS.md")
    write_lock(shadowed)
    install_rendered(shadowed)
    (shadowed.target.parent / "AGENTS.override.md").write_bytes(b"# Override\n")
    status = evaluate(load_manifest(shadowed.manifest), lock_path=shadowed.lock)
    assert status.state is BundleState.SHADOWED
    assert status.override.present is True
    assert status.target is None


def test_an_explicit_target_overrides_the_manifest_default(
    bundle: Bundle, tmp_path: Path
) -> None:
    write_lock(bundle)
    elsewhere = tmp_path / "elsewhere" / "AGENTS.md"
    elsewhere.parent.mkdir(parents=True)
    compiled = compile_bundle(load_manifest(bundle.manifest), target=elsewhere)
    elsewhere.write_bytes(compiled.rendered.data)
    status = evaluate(
        load_manifest(bundle.manifest), lock_path=bundle.lock, target=elsewhere
    )
    assert status.state is BundleState.CURRENT
    assert status.target is not None
    assert status.target.path == elsewhere


def test_the_reported_lock_path_is_used_for_diagnostics(bundle: Bundle) -> None:
    write_lock(bundle)
    bundle.lock.write_bytes(b"{}")
    with pytest.raises(LockError) as raised:
        evaluate(
            load_manifest(bundle.manifest),
            lock_path=bundle.lock,
            lock_lexical="global-agents.toml.lock.json",
        )
    assert raised.value.paths is not None
    assert raised.value.paths.lexical == "global-agents.toml.lock.json"


def test_a_symlinked_lock_is_reported_as_present(
    bundle: Bundle, tmp_path: Path
) -> None:
    write_lock(bundle)
    real = tmp_path / "real.lock.json"
    real.write_bytes(bundle.lock.read_bytes())
    bundle.lock.unlink()
    bundle.lock.symlink_to(real)
    with pytest.raises(LockError) as raised:
        evaluate_bundle(bundle)
    assert raised.value.problem is LockProblem.SYMLINK

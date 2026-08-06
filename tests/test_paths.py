"""Path resolution, tilde expansion, state-root selection, and containment."""

from pathlib import Path

import pytest

from agents_md_compiler import paths
from agents_md_compiler.models import DISTRIBUTION_DIRECTORY


def test_relative_values_resolve_against_the_given_base(tmp_path: Path) -> None:
    resolved = paths.resolve_against(tmp_path / "policy", "modules/core.md")
    assert resolved == tmp_path / "policy" / "modules" / "core.md"


def test_absolute_values_ignore_the_base(tmp_path: Path) -> None:
    absolute = tmp_path / "elsewhere" / "core.md"
    assert paths.resolve_against(tmp_path / "policy", str(absolute)) == absolute


def test_parent_components_normalize_lexically(tmp_path: Path) -> None:
    resolved = paths.resolve_against(tmp_path / "policy", "../modules/./core.md")
    assert resolved == tmp_path / "modules" / "core.md"
    assert ".." not in resolved.parts


def test_leading_tilde_expands_to_the_home_directory() -> None:
    assert paths.expand_leading_tilde("~/x.md") == str(Path.home() / "x.md")


def test_a_tilde_elsewhere_is_literal() -> None:
    assert paths.expand_leading_tilde("policy/~backup.md") == "policy/~backup.md"


def test_no_environment_variable_is_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLICY_HOME", str(tmp_path / "expanded"))
    resolved = paths.resolve_against(tmp_path, "$POLICY_HOME/core.md")
    assert resolved == tmp_path / "$POLICY_HOME" / "core.md"
    assert "expanded" not in str(resolved)


def test_windows_style_variable_is_literal(tmp_path: Path) -> None:
    resolved = paths.resolve_against(tmp_path, "%APPDATA%/core.md")
    assert resolved == tmp_path / "%APPDATA%" / "core.md"


def test_no_glob_is_expanded(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x\n", encoding="utf-8")
    resolved = paths.resolve_against(tmp_path, "*.md")
    assert resolved == tmp_path / "*.md"


def test_explicit_paths_resolve_from_the_working_directory(tmp_path: Path) -> None:
    assert paths.resolve_from_cwd("out/AGENTS.md", cwd=tmp_path) == (
        tmp_path / "out" / "AGENTS.md"
    )


def test_explicit_paths_default_to_the_process_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert paths.resolve_from_cwd("x.md") == tmp_path / "x.md"


def test_default_lock_path_appends_the_suffix(tmp_path: Path) -> None:
    manifest = tmp_path / "global-agents.toml"
    assert paths.default_lock_path(manifest).name == "global-agents.toml.lock.json"


def test_posix_state_root_honors_xdg_state_home(tmp_path: Path) -> None:
    root = paths.user_state_root(environ={paths.XDG_STATE_ENV: str(tmp_path)})
    assert root == tmp_path / DISTRIBUTION_DIRECTORY


def test_posix_state_root_falls_back_to_home() -> None:
    root = paths.user_state_root(environ={})
    assert root == Path.home() / paths.POSIX_STATE_FALLBACK / DISTRIBUTION_DIRECTORY


def test_posix_state_root_treats_an_empty_variable_as_unset() -> None:
    root = paths.user_state_root(environ={paths.XDG_STATE_ENV: ""})
    assert root == Path.home() / paths.POSIX_STATE_FALLBACK / DISTRIBUTION_DIRECTORY


def test_state_root_reads_the_process_environment_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.XDG_STATE_ENV, str(tmp_path))
    assert paths.user_state_root() == tmp_path / DISTRIBUTION_DIRECTORY


def test_windows_state_root_uses_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths.sys, "platform", "win32")
    root = paths.user_state_root(environ={paths.WINDOWS_STATE_ENV: str(tmp_path)})
    assert root == tmp_path / DISTRIBUTION_DIRECTORY


def test_windows_state_root_falls_back_to_the_default_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths.sys, "platform", "win32")
    root = paths.user_state_root(environ={})
    assert root == Path.home() / "AppData" / "Local" / DISTRIBUTION_DIRECTORY


def test_bundle_state_dir_nests_under_the_state_root(tmp_path: Path) -> None:
    assert paths.bundle_state_dir("demo", state_root=tmp_path) == tmp_path / "demo"


def test_bundle_state_dir_defaults_to_the_platform_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.XDG_STATE_ENV, str(tmp_path))
    assert paths.bundle_state_dir("demo") == (
        tmp_path / DISTRIBUTION_DIRECTORY / "demo"
    )


def test_containment_accepts_the_root_itself(tmp_path: Path) -> None:
    assert paths.is_within(tmp_path, tmp_path) is True


def test_containment_accepts_a_nested_path(tmp_path: Path) -> None:
    assert paths.is_within(tmp_path / "a" / "b.json", tmp_path) is True


def test_containment_rejects_a_sibling(tmp_path: Path) -> None:
    assert paths.is_within(tmp_path.parent / "other", tmp_path) is False


def test_containment_rejects_traversal_out_of_the_root(tmp_path: Path) -> None:
    escaped = tmp_path / "a" / ".." / ".." / "escaped.json"
    assert paths.is_within(escaped, tmp_path) is False


def test_containment_normalizes_before_comparing(tmp_path: Path) -> None:
    inside = Path(f"{tmp_path}/a/../b.json")
    assert paths.is_within(inside, tmp_path) is True

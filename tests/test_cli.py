"""CLI behavior: streams, exit codes, JSON envelopes, and every refusal.

Every test isolates the user state root through ``XDG_STATE_HOME`` and runs inside a
disposable bundle directory, so no test reads or writes a real user configuration
path or a real Codex home.
"""

import argparse
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    Bundle,
    compiled_of,
    install_mock_codex,
    make_bundle,
    write_lock,
    write_text_file,
)

from agents_md_compiler import cli
from agents_md_compiler.hashing import sha256_file
from agents_md_compiler.models import DISTRIBUTION_DIRECTORY, BundleState
from agents_md_compiler.paths import deployment_state_dir

UNMANAGED_TEXT = "# Hand written policy\n\nBody that must be preserved.\n"


@pytest.fixture(autouse=True)
def isolate_user_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every CLI test independent of the operator's home and Codex home."""
    fake_home = tmp_path / "process-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("CODEX_HOME", raising=False)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bundle:
    """Provide a locked bundle with the process rooted in it and state isolated.

    Args:
        tmp_path: pytest temporary directory.
        monkeypatch: pytest patcher.

    Returns:
        The bundle description.
    """
    built = make_bundle(tmp_path)
    built.target.parent.mkdir(parents=True, exist_ok=True)
    write_lock(built)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(built.root)
    return built


def state_dir(bundle: Bundle, tmp_path: Path) -> Path:
    """Locate the isolated target-qualified deployment directory.

    Args:
        bundle: The bundle.
        tmp_path: pytest temporary directory.

    Returns:
        The state directory path.
    """
    state_root = tmp_path / "xdg" / DISTRIBUTION_DIRECTORY
    return deployment_state_dir("test-bundle", bundle.target, state_root=state_root)


def run(*argv: str) -> int:
    """Invoke the CLI.

    Args:
        *argv: Argument vector without the program name.

    Returns:
        The exit code.
    """
    return cli.main(list(argv))


def envelope(captured: str) -> dict[str, Any]:
    """Parse the single JSON object a command wrote to stdout.

    Args:
        captured: Captured stdout.

    Returns:
        The decoded envelope.
    """
    assert captured.count("\n") == 1, "JSON mode must emit exactly one line"
    decoded: dict[str, Any] = json.loads(captured)
    for field in ("command", "ok", "schema_version", "state"):
        assert field in decoded, f"every envelope must carry {field}"
    return decoded


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run("--help")
    assert exit_info.value.code == 0
    assert "COMMAND" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(("status", "--nope"), id="unknown-option"),
        pytest.param(("bogus",), id="unknown-command"),
        pytest.param(("status", "--format", "yaml"), id="invalid-choice"),
        pytest.param(("verify-codex", "--timeout", "abc"), id="invalid-option-type"),
        pytest.param(("rollback",), id="missing-required-option"),
    ],
)
def test_argparse_usage_errors_exit_one(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(*argv) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err


@pytest.mark.parametrize(
    "command",
    [
        "init",
        "lock",
        "validate",
        "render",
        "check",
        "status",
        "install",
        "rollback",
        "version",
    ],
)
def test_every_documented_subcommand_has_help(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(command, "--help")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out != ""


def test_no_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert run() == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no command given" in captured.err


def test_the_state_exit_code_table_is_exhaustive() -> None:
    assert set(cli.STATE_EXIT_CODES) == set(BundleState)


def test_version_subcommand_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("version") == cli.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.strip() != ""
    assert captured.err == ""


def test_version_flag_matches_the_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("--version") == cli.EXIT_OK
    from_flag = capsys.readouterr().out
    assert run("version") == cli.EXIT_OK
    assert capsys.readouterr().out == from_flag


def test_version_json_carries_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("version", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["command"] == "version"
    assert payload["state"] is None
    assert payload["version"]


def test_the_format_flag_is_accepted_in_both_positions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("--format", "json", "version") == cli.EXIT_OK
    assert envelope(capsys.readouterr().out)["command"] == "version"
    assert run("--format", "json", "version", "--format", "text") == cli.EXIT_OK
    assert not capsys.readouterr().out.startswith("{")


def test_resolve_format_defaults_to_text() -> None:
    assert cli.resolve_format(argparse.Namespace(global_format=None)) == cli.FORMAT_TEXT


def test_resolve_quiet_reads_both_positions() -> None:
    assert cli.resolve_quiet(argparse.Namespace(global_quiet=None)) is False
    assert cli.resolve_quiet(argparse.Namespace(global_quiet=True)) is True
    assert (
        cli.resolve_quiet(argparse.Namespace(global_quiet=None, subcommand_quiet=True))
        is True
    )


def test_quiet_is_accepted_in_both_positions() -> None:
    parser = cli.build_parser()
    assert cli.resolve_quiet(parser.parse_args(["--quiet", "version"])) is True
    assert cli.resolve_quiet(parser.parse_args(["version", "--quiet"])) is True
    assert cli.resolve_quiet(parser.parse_args(["version"])) is False


def test_emit_json_sorts_keys(capsys: pytest.CaptureFixture[str]) -> None:
    cli.emit_json({"b": 1, "a": 2})
    assert capsys.readouterr().out == '{"a": 2, "b": 1}\n'


def test_init_scaffolds_a_working_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert run("init", "--directory", "policy", "--bundle-id", "scaffolded") == (
        cli.EXIT_OK
    )
    capsys.readouterr()
    assert (tmp_path / "policy" / "agents-md.toml").is_file()
    assert 'default_target = "~/.codex/AGENTS.md"' in (
        tmp_path / "policy" / "agents-md.toml"
    ).read_text(encoding="utf-8")
    assert (tmp_path / "policy" / "modules" / "core.md").is_file()
    assert (tmp_path / "policy" / "modules" / "python.md").is_file()
    assert run("lock", "--manifest", "policy/agents-md.toml") == cli.EXIT_OK
    capsys.readouterr()
    assert run("validate", "--manifest", "policy/agents-md.toml") == cli.EXIT_OK


def test_init_json_lists_what_it_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("init", "--directory", "policy", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] is None
    assert len(payload["created"]) == 3
    assert payload["directory"].endswith("policy")
    assert payload["target_path"].endswith(".codex/AGENTS.md")
    assert payload["next_command"][1:3] == ["lock", "--manifest"]


def test_init_serializes_a_relative_target_from_the_scaffold_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert (
        run("init", "--directory", "policy", "--target", "repo/AGENTS.md")
        == cli.EXIT_OK
    )
    capsys.readouterr()
    manifest = (tmp_path / "policy" / "agents-md.toml").read_text(encoding="utf-8")
    assert 'default_target = "../repo/AGENTS.md"' in manifest


def test_init_preserves_an_absolute_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "repo" / "AGENTS.md"
    assert run("init", "--directory", "policy", "--target", str(target)) == cli.EXIT_OK
    capsys.readouterr()
    manifest = (tmp_path / "policy" / "agents-md.toml").read_text(encoding="utf-8")
    assert f'default_target = "{target}"' in manifest


def test_init_honors_an_explicit_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert run("init", "--directory", "policy") == cli.EXIT_OK
    capsys.readouterr()
    manifest = (tmp_path / "policy" / "agents-md.toml").read_text(encoding="utf-8")
    assert f'default_target = "{codex_home / "AGENTS.md"}"' in manifest


def test_init_refuses_an_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    write_text_file(tmp_path / "policy" / "agents-md.toml", "# existing\n")
    assert run("init", "--directory", "policy") == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "refusing to write" in captured.err
    assert (tmp_path / "policy" / "agents-md.toml").read_text(
        encoding="utf-8"
    ) == "# existing\n"
    assert not (tmp_path / "policy" / "modules").exists()


def test_init_refuses_an_invalid_bundle_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("init", "--directory", "policy", "--bundle-id", "Bad_Id") == (
        cli.EXIT_ERROR
    )
    assert "must match" in capsys.readouterr().err
    assert not (tmp_path / "policy").exists()


def test_init_refuses_a_blank_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("init", "--directory", "policy", "--target", "   ") == cli.EXIT_ERROR
    assert "must not be empty" in capsys.readouterr().err
    assert not (tmp_path / "policy").exists()


def test_lock_writes_and_then_reports_current(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("lock") == cli.EXIT_OK
    assert workspace.lock.is_file()
    capsys.readouterr()
    assert run("lock") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_lock_json_reports_what_it_wrote(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("lock", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["written"] is True
    assert payload["state"] == "CURRENT"
    assert [module["id"] for module in payload["modules"]] == ["core", "python"]


def test_lock_check_is_read_only_and_reports_staleness(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    before = workspace.lock.read_bytes()
    workspace.modules["core"].write_bytes(b"# Core\n\nEdited.\n")
    assert run("lock", "--check") == cli.EXIT_DIFFERENCE
    assert "LOCK_STALE" in capsys.readouterr().out
    assert workspace.lock.read_bytes() == before, "--check must not write"


def test_lock_check_reports_a_missing_lock(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("lock", "--check") == cli.EXIT_DIFFERENCE
    assert "LOCK_MISSING" in capsys.readouterr().out
    assert not workspace.lock.exists()


def test_lock_explicitly_migrates_a_format_1_lock(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    document = json.loads(workspace.lock.read_bytes())
    document["format_version"] = 1
    for module in document["modules"]:
        source = module.pop("source")
        module["resolved_source"] = str(workspace.root / source)
    legacy = (
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode()
    workspace.lock.write_bytes(legacy)
    assert run("validate") == cli.EXIT_DIFFERENCE
    assert "LOCK_STALE" in capsys.readouterr().out
    assert workspace.lock.read_bytes() == legacy
    assert run("lock") == cli.EXIT_OK
    capsys.readouterr()
    assert json.loads(workspace.lock.read_bytes())["format_version"] == 2


def test_lock_refuses_a_concurrent_lockfile_change(
    workspace: Bundle,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace.modules["core"].write_bytes(b"# Core\n\nEdited.\n")
    real_read = cli.read_lock_bytes
    calls: list[int] = []

    def _change(path: Path, *, lexical: str | None = None) -> bytes:
        calls.append(1)
        if len(calls) == 2:
            workspace.lock.write_bytes(b'{"changed": true}')
        return real_read(path, lexical=lexical)

    monkeypatch.setattr(cli, "read_lock_bytes", _change)
    assert run("lock") == cli.EXIT_REFUSAL
    assert "lock changed after its precondition" in capsys.readouterr().err
    assert workspace.lock.read_bytes() == b'{"changed": true}', (
        "the concurrent writer's bytes must survive untouched"
    )


def test_validate_reports_current_without_a_target(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert not workspace.target.exists()
    assert run("validate") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_validate_json_reports_no_target_digest(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("validate", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["target_sha256"] is None
    assert payload["output_bytes"] > 0


def test_validate_reports_a_stale_lock(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.manifest.write_bytes(workspace.manifest.read_bytes() + b"\n# note\n")
    assert run("validate") == cli.EXIT_DIFFERENCE
    assert "LOCK_STALE" in capsys.readouterr().out


def test_validate_reports_a_missing_lock(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("validate") == cli.EXIT_DIFFERENCE
    assert "LOCK_MISSING" in capsys.readouterr().out


def test_render_to_stdout_emits_only_rendered_bytes(
    workspace: Bundle, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    compiled = compiled_of(workspace, target=workspace.target)
    assert run("render") == cli.EXIT_OK
    captured = capsysbinary.readouterr()
    assert captured.out == compiled.rendered.data
    assert captured.err == b""


def test_render_locked_emits_the_same_bytes(
    workspace: Bundle, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    compiled = compiled_of(workspace, target=workspace.target)
    assert run("render", "--locked") == cli.EXIT_OK
    assert capsysbinary.readouterr().out == compiled.rendered.data


def test_render_locked_refuses_a_stale_lock_and_emits_no_bytes(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.modules["core"].write_bytes(b"# Core\n\nEdited.\n")
    assert run("render", "--locked") == cli.EXIT_DIFFERENCE
    captured = capsys.readouterr()
    assert captured.out.strip() == "LOCK_STALE"
    assert "Core Policy" not in captured.out
    assert "LOCK_STALE" in captured.err


def test_render_locked_refuses_a_missing_lock(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("render", "--locked") == cli.EXIT_DIFFERENCE
    assert capsys.readouterr().out.strip() == "LOCK_MISSING"


def test_render_locked_json_reports_the_refusal(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("render", "--locked", "--format", "json") == cli.EXIT_DIFFERENCE
    payload = envelope(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["state"] == "LOCK_MISSING"


def test_render_json_reports_identity_without_policy_bytes(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    compiled = compiled_of(workspace, target=workspace.target)
    assert run("render", "--format", "json") == cli.EXIT_OK
    captured = capsys.readouterr().out
    payload = envelope(captured)
    assert payload["output_sha256"] == compiled.rendered.sha256
    assert payload["output_path"] is None
    assert "Core Policy" not in captured


def test_render_to_a_new_output_path(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    compiled = compiled_of(workspace, target=workspace.target)
    assert run("render", "--output", "out/rendered.md") == cli.EXIT_OK
    written = workspace.root / "out" / "rendered.md"
    assert written.read_bytes() == compiled.rendered.data
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert str(written) in capsys.readouterr().out


def test_render_refuses_an_existing_output_path(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = write_text_file(workspace.root / "out" / "rendered.md", "# Existing\n")
    assert run("render", "--output", "out/rendered.md") == cli.EXIT_ERROR
    assert existing.read_text(encoding="utf-8") == "# Existing\n"
    assert "refusing to write" in capsys.readouterr().err


def test_render_refuses_an_existing_output_symlink(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    real = write_text_file(workspace.root / "real.md", "# Real\n")
    link = workspace.root / "linked.md"
    link.symlink_to(real)
    assert run("render", "--output", "linked.md") == cli.EXIT_ERROR
    assert real.read_text(encoding="utf-8") == "# Real\n"
    assert "refusing to write" in capsys.readouterr().err


def test_render_refuses_a_missing_output_parent(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("render", "--output", "missing/rendered.md") == cli.EXIT_ERROR
    assert "target parent directory does not exist" in capsys.readouterr().err


def test_render_output_json_reports_the_path(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("render", "--output", "out/rendered.md", "--format", "json") == (
        cli.EXIT_OK
    )
    payload = envelope(capsys.readouterr().out)
    assert payload["output_path"].endswith("out/rendered.md")


def test_check_reports_missing_then_current(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("check") == cli.EXIT_DIFFERENCE
    assert "MISSING" in capsys.readouterr().out
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    assert run("check") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_install_then_check_reports_current_for_a_non_ascii_source(
    non_ascii_bundle: Bundle,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    non_ascii_bundle.target.parent.mkdir(parents=True, exist_ok=True)
    write_lock(non_ascii_bundle)
    monkeypatch.setenv("XDG_STATE_HOME", str(non_ascii_bundle.root.parent / "xdg"))
    monkeypatch.chdir(non_ascii_bundle.root)

    assert run("install", "--apply") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out
    assert run("check") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_check_reports_drift(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    workspace.target.write_bytes(workspace.target.read_bytes() + b"\nappended\n")
    assert run("check") == cli.EXIT_DIFFERENCE
    assert "DRIFTED" in capsys.readouterr().out


def test_check_reports_an_unmanaged_target(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_file(workspace.target, UNMANAGED_TEXT)
    assert run("check") == cli.EXIT_REFUSAL
    assert "UNMANAGED_TARGET" in capsys.readouterr().out


def test_check_json_reports_the_target_digest(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    assert run("check", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["target_sha256"] == payload["output_sha256"]
    assert payload["state"] == "CURRENT"


def test_check_honors_an_explicit_target(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace.root / "elsewhere").mkdir()
    assert run("check", "--target", "elsewhere/AGENTS.md") == cli.EXIT_DIFFERENCE
    payload = capsys.readouterr().out
    assert "MISSING" in payload


def test_install_dry_run_refuses_a_missing_target_parent(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--target", "missing/AGENTS.md") == cli.EXIT_ERROR
    assert "target parent directory does not exist" in capsys.readouterr().err


def test_status_reports_the_full_picture(
    workspace: Bundle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    assert run("status", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] == "CURRENT"
    assert payload["lock_present"] is True
    assert payload["lock_matches"] is True
    assert payload["target_kind"] == "MANAGED"
    assert payload["receipt_count"] == 1
    assert payload["latest_receipt"].endswith(".json")
    assert payload["backup_count"] == 0
    assert payload["state_root"] == str(state_dir(workspace, tmp_path))
    assert payload["override_present"] is False
    # The default target is named AGENTS.md, so override shadowing applies to it and
    # the path that *would* shadow it is reported even though nothing is there.
    assert payload["override_path"].endswith("AGENTS.override.md")


def test_status_reports_a_shadowing_override(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    global_target = workspace.target.parent / "AGENTS.md"
    (global_target.parent / "AGENTS.override.md").write_text(
        "# Override\n", encoding="utf-8"
    )
    assert run("status", "--target", str(global_target), "--format", "json") == (
        cli.EXIT_REFUSAL
    )
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] == "SHADOWED"
    assert payload["override_present"] is True
    assert payload["override_path"].endswith("AGENTS.override.md")


def test_status_history_is_scoped_to_the_selected_target(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    second = workspace.root / "second" / "AGENTS.md"
    second.parent.mkdir()
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    assert run("install", "--target", str(second), "--apply") == cli.EXIT_OK
    capsys.readouterr()
    assert run("status", "--format", "json") == cli.EXIT_OK
    first_status = envelope(capsys.readouterr().out)
    assert run("status", "--target", str(second), "--format", "json") == cli.EXIT_OK
    second_status = envelope(capsys.readouterr().out)
    assert first_status["receipt_count"] == 1
    assert second_status["receipt_count"] == 1
    assert first_status["state_root"] != second_status["state_root"]


def test_status_counts_backups(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_file(workspace.target, UNMANAGED_TEXT)
    digest = sha256_file(workspace.target)
    assert (
        run(
            "install",
            "--apply",
            "--replace-unmanaged",
            "--expect-target-sha256",
            digest,
        )
        == cli.EXIT_OK
    )
    capsys.readouterr()
    assert run("status", "--format", "json") == cli.EXIT_OK
    assert envelope(capsys.readouterr().out)["backup_count"] == 1


def test_install_dry_run_writes_nothing(
    workspace: Bundle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install") == cli.EXIT_DIFFERENCE
    captured = capsys.readouterr()
    assert "MISSING" in captured.out
    assert "dry run" in captured.err
    assert not workspace.target.exists()
    assert not (state_dir(workspace, tmp_path) / "receipts").exists()


def test_install_dry_run_json_reports_the_plan(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--format", "json") == cli.EXIT_DIFFERENCE
    payload = envelope(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["previous_state"] == "MISSING"
    assert payload["backup_path"] is None
    assert payload["receipt_path"] is None
    assert payload["target_mode"] == "0600"


def test_install_applies_and_reports_current(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    compiled = compiled_of(workspace, target=workspace.target)
    assert run("install", "--apply", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["state"] == "CURRENT"
    assert payload["receipt_path"].endswith(".json")
    assert workspace.target.read_bytes() == compiled.rendered.data


def test_install_refuses_an_unmanaged_target(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_file(workspace.target, UNMANAGED_TEXT)
    assert run("install", "--apply") == cli.EXIT_REFUSAL
    assert "--replace-unmanaged" in capsys.readouterr().err
    assert workspace.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_install_adopts_an_unmanaged_target_with_a_matching_digest(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_file(workspace.target, UNMANAGED_TEXT)
    digest = sha256_file(workspace.target)
    compiled = compiled_of(workspace, target=workspace.target)
    assert (
        run(
            "install",
            "--apply",
            "--replace-unmanaged",
            "--expect-target-sha256",
            digest,
            "--format",
            "json",
        )
        == cli.EXIT_OK
    )
    payload = envelope(capsys.readouterr().out)
    assert payload["previous_state"] == "UNMANAGED"
    assert payload["previous_sha256"] == digest
    assert payload["backup_sha256"] == digest
    assert workspace.target.read_bytes() == compiled.rendered.data
    assert Path(payload["backup_path"]).read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_install_refuses_a_mismatched_expected_digest(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_file(workspace.target, UNMANAGED_TEXT)
    assert (
        run(
            "install",
            "--apply",
            "--replace-unmanaged",
            "--expect-target-sha256",
            "0" * 64,
        )
        == cli.EXIT_REFUSAL
    )
    assert "does not match" in capsys.readouterr().err
    assert workspace.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_install_refuses_a_malformed_expected_digest(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            "install",
            "--apply",
            "--replace-unmanaged",
            "--expect-target-sha256",
            "nope",
        )
        == cli.EXIT_ERROR
    )
    assert "lowercase 64-character" in capsys.readouterr().err


def test_install_refuses_an_expected_digest_without_adoption(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply", "--expect-target-sha256", "0" * 64) == (
        cli.EXIT_ERROR
    )
    assert "only meaningful with --replace-unmanaged" in capsys.readouterr().err


def test_install_reports_a_stale_lock_without_mutating(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.modules["core"].write_bytes(b"# Core\n\nEdited.\n")
    assert run("install", "--apply") == cli.EXIT_DIFFERENCE
    assert "LOCK_STALE" in capsys.readouterr().out
    assert not workspace.target.exists()


def test_install_reports_a_missing_lock_without_mutating(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.lock.unlink()
    assert run("install", "--apply") == cli.EXIT_DIFFERENCE
    assert "LOCK_MISSING" in capsys.readouterr().out
    assert not workspace.target.exists()


def test_install_refuses_a_shadowed_target(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    global_target = workspace.target.parent / "AGENTS.md"
    (global_target.parent / "AGENTS.override.md").write_text(
        "# Override\n", encoding="utf-8"
    )
    assert run("install", "--apply", "--target", str(global_target)) == cli.EXIT_REFUSAL
    assert "would be loaded instead" in capsys.readouterr().err
    assert not global_target.exists()


def test_rollback_restores_and_reports(
    workspace: Bundle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_file(workspace.target, UNMANAGED_TEXT)
    digest = sha256_file(workspace.target)
    assert (
        run(
            "install",
            "--apply",
            "--replace-unmanaged",
            "--expect-target-sha256",
            digest,
            "--format",
            "json",
        )
        == cli.EXIT_OK
    )
    receipt = envelope(capsys.readouterr().out)["receipt_path"]
    assert run("rollback", "--receipt", receipt, "--apply", "--format", "json") == (
        cli.EXIT_OK
    )
    payload = envelope(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["restored_sha256"] == digest
    assert payload["preserved_path"] is None
    assert workspace.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_rollback_dry_run_changes_nothing(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply", "--format", "json") == cli.EXIT_OK
    receipt = envelope(capsys.readouterr().out)["receipt_path"]
    installed = workspace.target.read_bytes()
    assert run("rollback", "--receipt", receipt) == cli.EXIT_DIFFERENCE
    captured = capsys.readouterr()
    assert "dry run" in captured.err
    assert workspace.target.read_bytes() == installed


def test_rollback_of_a_created_target_preserves_it(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply", "--format", "json") == cli.EXIT_OK
    receipt = envelope(capsys.readouterr().out)["receipt_path"]
    assert run("rollback", "--receipt", receipt, "--apply", "--format", "json") == (
        cli.EXIT_OK
    )
    payload = envelope(capsys.readouterr().out)
    assert payload["restored_sha256"] is None
    assert Path(payload["preserved_path"]).is_file()
    assert not workspace.target.exists()


def test_rollback_refuses_a_forged_receipt(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    forged = write_text_file(workspace.root / "forged.json", '{"operation": "install"}')
    assert run("rollback", "--receipt", str(forged)) == cli.EXIT_ERROR
    assert "state root" in capsys.readouterr().err


def test_rollback_refuses_after_the_target_changed(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply", "--format", "json") == cli.EXIT_OK
    receipt = envelope(capsys.readouterr().out)["receipt_path"]
    workspace.target.write_text("# Edited by hand\n", encoding="utf-8")
    assert run("rollback", "--receipt", receipt, "--apply") == cli.EXIT_REFUSAL
    assert "target changed after its precondition" in capsys.readouterr().err
    assert workspace.target.read_text(encoding="utf-8") == "# Edited by hand\n"


def test_quiet_suppresses_notes_but_not_errors(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--quiet") == cli.EXIT_DIFFERENCE
    assert "dry run" not in capsys.readouterr().err
    write_text_file(workspace.target, UNMANAGED_TEXT)
    assert run("install", "--apply", "--quiet") == cli.EXIT_REFUSAL
    assert "--replace-unmanaged" in capsys.readouterr().err


def test_quiet_does_not_suppress_json(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("lock", "--check", "--quiet", "--format", "json") == cli.EXIT_OK
    assert envelope(capsys.readouterr().out)["command"] == "lock"


def test_an_invalid_manifest_reports_an_error_envelope(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.manifest.write_bytes(b"schema_version = 2\n")
    assert run("check", "--format", "json") == cli.EXIT_ERROR
    payload = envelope(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["state"] == "INVALID_MANIFEST"
    assert payload["error"]["kind"] == "ManifestError"
    assert "schema_version" in payload["error"]["message"]


def test_an_error_envelope_reports_both_path_forms(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    link = workspace.root / "linked.md"
    link.symlink_to(workspace.modules["core"])
    workspace.manifest.write_bytes(
        workspace.manifest.read_bytes().replace(
            b'source = "modules/core.md"', b'source = "linked.md"'
        )
    )
    assert run("check", "--format", "json") == cli.EXIT_ERROR
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] == "INVALID_SOURCE"
    assert payload["error"]["paths"]["lexical"] == "linked.md"
    assert payload["error"]["paths"]["resolved"].endswith("linked.md")


def test_an_error_omits_the_resolved_form_when_it_matches(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            "check",
            "--manifest",
            str(workspace.root / "absent.toml"),
            "--format",
            "json",
        )
        == cli.EXIT_ERROR
    )
    payload = envelope(capsys.readouterr().out)
    assert "resolved" not in payload["error"]["paths"]


def test_errors_always_reach_stderr_even_in_json_mode(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace.manifest.write_bytes(b"schema_version = 2\n")
    assert run("check", "--format", "json") == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.err != ""
    assert captured.out.startswith("{")


def test_an_explicit_lock_path_is_honored(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    moved = workspace.root / "custom.lock.json"
    moved.write_bytes(workspace.lock.read_bytes())
    workspace.lock.unlink()
    assert run("validate", "--lock", "custom.lock.json") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_an_install_receipt_records_the_explicit_lock_path_used(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    moved = workspace.root / "elsewhere" / "custom.lock.json"
    moved.parent.mkdir()
    moved.write_bytes(workspace.lock.read_bytes())
    workspace.lock.unlink()

    assert (
        run(
            "install",
            "--apply",
            "--lock",
            "elsewhere/custom.lock.json",
            "--format",
            "json",
        )
        == cli.EXIT_OK
    )
    payload = envelope(capsys.readouterr().out)
    receipt = json.loads(Path(payload["receipt_path"]).read_text(encoding="utf-8"))
    assert moved.is_file()
    assert receipt["lock_path"]["lexical"] == "elsewhere/custom.lock.json"
    assert receipt["lock_path"]["resolved"] == payload["lock_path"] == str(moved)


def test_a_default_manifest_is_read_from_the_working_directory(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("validate") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_the_neutral_default_manifest_is_preferred_when_present(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    neutral = workspace.root / "agents-md.toml"
    neutral_lock = Path(str(neutral) + ".lock.json")
    workspace.manifest.replace(neutral)
    workspace.lock.replace(neutral_lock)
    assert run("validate") == cli.EXIT_OK
    assert "CURRENT" in capsys.readouterr().out


def test_implicit_manifest_discovery_refuses_ambiguity(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace.root / "agents-md.toml").write_bytes(workspace.manifest.read_bytes())
    assert run("validate") == cli.EXIT_ERROR
    assert "both 'agents-md.toml' and 'global-agents.toml'" in capsys.readouterr().err


def test_missing_implicit_manifest_names_the_neutral_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("validate") == cli.EXIT_ERROR
    assert "agents-md.toml" in capsys.readouterr().err


def test_rollback_apply_in_text_mode_reports_the_state(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply", "--format", "json") == cli.EXIT_OK
    receipt = envelope(capsys.readouterr().out)["receipt_path"]
    assert run("rollback", "--receipt", receipt, "--apply") == cli.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.strip() == "MISSING"
    assert "dry run" not in captured.err


def test_rollback_accepts_a_legacy_bundle_only_receipt(
    workspace: Bundle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("install", "--apply", "--format", "json") == cli.EXIT_OK
    receipt_path = Path(envelope(capsys.readouterr().out)["receipt_path"])
    legacy_receipts = (
        tmp_path / "xdg" / DISTRIBUTION_DIRECTORY / "test-bundle" / "receipts"
    )
    legacy_receipts.mkdir(parents=True)
    legacy_receipt = legacy_receipts / receipt_path.name
    receipt_path.replace(legacy_receipt)
    assert run("rollback", "--receipt", str(legacy_receipt), "--apply") == cli.EXIT_OK
    assert capsys.readouterr().out.strip() == "MISSING"
    assert not workspace.target.exists()
    assert len(list((state_dir(workspace, tmp_path) / "receipts").glob("*.json"))) == 1


def test_an_error_without_a_path_still_produces_an_envelope(
    workspace: Bundle, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run("install", "--apply", "--expect-target-sha256", "nope", "--format", "json")
        == cli.EXIT_ERROR
    )
    payload = envelope(capsys.readouterr().out)
    assert payload["error"]["kind"] == "UsageError"
    assert "paths" not in payload["error"]
    assert payload["state"] is None


def install_verify_mock(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "ok",
) -> None:
    """Install a mock codex that echoes the workspace's rendered bundle.

    Args:
        workspace: The locked bundle.
        tmp_path: pytest temporary directory.
        monkeypatch: pytest patcher.
        mode: Mock behavior selector.
    """
    rendered = compiled_of(workspace, target=workspace.target).rendered
    echoed = tmp_path / "echoed-bundle.md"
    echoed.write_bytes(rendered.data)
    install_mock_codex(tmp_path, monkeypatch, mode=mode, bundle=echoed)


def test_verify_codex_passes_when_the_bundle_is_visible(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    install_verify_mock(workspace, tmp_path, monkeypatch)
    assert run("verify-codex", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] == "CURRENT"
    assert payload["capability_present"] is True
    assert payload["markers_found"] == payload["markers_expected"] == 5
    assert payload["sentinels_found"] == payload["sentinels_expected"] == 2
    assert payload["failure"] is None
    assert payload["probe_command"][1:3] == ["debug", "prompt-input"]
    assert payload["probe_cwd"] == str(workspace.target.parent)
    assert payload["verification_context"] == "project"


def test_verify_codex_accepts_a_descendant_project_cwd(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    child = workspace.target.parent / "nested"
    child.mkdir()
    install_verify_mock(workspace, tmp_path, monkeypatch)
    assert run("verify-codex", "--cwd", str(child), "--format", "json") == cli.EXIT_OK
    assert envelope(capsys.readouterr().out)["probe_cwd"] == str(child)


def test_verify_codex_refuses_a_project_cwd_outside_the_target_chain(
    workspace: Bundle,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("verify-codex", "--cwd", str(tmp_path)) == cli.EXIT_ERROR
    assert "or one of its descendants" in capsys.readouterr().err


def test_verify_codex_refuses_cwd_for_the_active_global_target(
    workspace: Bundle,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(workspace.target.parent))
    assert run("verify-codex", "--cwd", str(workspace.target.parent)) == cli.EXIT_ERROR
    assert "cannot be used with the active global target" in capsys.readouterr().err


def test_verify_codex_isolates_the_active_global_target(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    monkeypatch.setenv("CODEX_HOME", str(workspace.target.parent))
    install_verify_mock(workspace, tmp_path, monkeypatch)
    assert run("verify-codex", "--format", "json") == cli.EXIT_OK
    payload = envelope(capsys.readouterr().out)
    assert payload["verification_context"] == "global"
    assert Path(payload["probe_cwd"]).name.startswith("amc-probe-")


def test_verify_codex_reports_a_note_in_text_mode(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    install_verify_mock(workspace, tmp_path, monkeypatch)
    assert run("verify-codex") == cli.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.strip() == "CURRENT"
    assert "content sentinels" in captured.err


def test_verify_codex_reports_runtime_unverified_and_exits_one(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    install_verify_mock(workspace, tmp_path, monkeypatch, mode="no-capability")
    assert run("verify-codex", "--format", "json") == cli.EXIT_ERROR
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] == "RUNTIME_UNVERIFIED"
    assert payload["ok"] is False
    assert "prompt-input" in payload["failure"]
    assert payload["markers_found"] == 0


def test_verify_codex_reports_the_failure_on_stderr_in_text_mode(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    install_verify_mock(workspace, tmp_path, monkeypatch, mode="missing-marker")
    assert run("verify-codex") == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out.strip() == "RUNTIME_UNVERIFIED"
    assert "module marker is not present" in captured.err


def test_verify_codex_never_reports_current_for_a_drifted_target(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Runtime verification must not run against bytes nobody asked about, and the
    # result must never be CURRENT when verification did not happen.
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    workspace.target.write_bytes(workspace.target.read_bytes() + b"\ndrift\n")
    install_verify_mock(workspace, tmp_path, monkeypatch)
    assert run("verify-codex", "--format", "json") == cli.EXIT_DIFFERENCE
    payload = envelope(capsys.readouterr().out)
    assert payload["state"] == "DRIFTED"
    assert payload["capability_present"] is False
    assert payload["failure"] == "static state is not CURRENT"


def test_verify_codex_refuses_a_shadowed_target(
    workspace: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("install", "--apply") == cli.EXIT_OK
    capsys.readouterr()
    (workspace.target.parent / "AGENTS.override.md").write_text(
        "# Override\n", encoding="utf-8"
    )
    install_verify_mock(workspace, tmp_path, monkeypatch)
    assert run("verify-codex", "--format", "json") == cli.EXIT_REFUSAL
    assert envelope(capsys.readouterr().out)["state"] == "SHADOWED"

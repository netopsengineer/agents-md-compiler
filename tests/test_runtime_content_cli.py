"""Runtime content contracts through the CLI and real mock-host subprocesses."""

import json
from pathlib import Path
from typing import cast

import pytest
from conftest import (
    CORE_TEXT,
    EXTRAS_TEXT,
    PYTHON_TEXT,
    Bundle,
    compiled_of,
    install_mock_codex,
    make_bundle,
    write_lock,
)

from agents_md_compiler import cli


@pytest.fixture(autouse=True)
def isolated_codex_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep context classification and state paths away from the operator's home."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def installed_fixture(
    root: Path,
    *,
    texts: tuple[tuple[str, str], ...] = (
        ("core", CORE_TEXT),
        ("python", PYTHON_TEXT),
        ("extras", EXTRAS_TEXT),
    ),
) -> Bundle:
    """Prepare an exact static target without invoking any production install path."""
    built = make_bundle(root, bundle_id="selected", module_texts=texts)
    write_lock(built)
    built.target.parent.mkdir(parents=True, exist_ok=True)
    built.target.write_bytes(compiled_of(built).rendered.data)
    return built


def json_envelope(text: str) -> dict[str, object]:
    """Narrow the CLI's decoded JSON object after checking its envelope shape."""
    assert text.count("\n") == 1
    decoded: object = json.loads(text)
    assert isinstance(decoded, dict)
    # JSON object keys are strings; the value types remain deliberately unknown.
    result = cast("dict[str, object]", decoded)
    assert result["schema_version"] == 1
    assert result["command"] == "verify-codex"
    return result


def invoke(bundle: Bundle, *options: str) -> int:
    return cli.main(["verify-codex", "--manifest", str(bundle.manifest), *options])


def test_cli_json_reports_complete_content_and_retains_legacy_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = installed_fixture(tmp_path)
    install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=bundle.target)
    before = {
        path: path.read_bytes()
        for path in (
            bundle.manifest,
            bundle.lock,
            bundle.target,
            *bundle.modules.values(),
        )
    }
    assert invoke(bundle, "--format", "json") == 0
    captured = capsys.readouterr()
    payload = json_envelope(captured.out)
    assert payload["state"] == "CURRENT"
    assert payload["ok"] is True
    assert payload["verification_method"] == "complete_bundle_bytes"
    assert payload["content_verified"] is True
    assert payload["selected_bundle_occurrences"] == 1
    assert payload["modules_expected"] == payload["modules_verified"] == 3
    assert payload["overlap_scan_complete"] is True
    assert payload["diagnostics"] == []
    assert payload["markers_found"] == payload["markers_expected"] == 7
    assert payload["sentinels_found"] == payload["sentinels_expected"] == 2
    assert payload["verification_context"] == "project"
    assert payload["probe_cwd"] == str(bundle.target.parent)
    assert {path: path.read_bytes() for path in before} == before
    assert captured.err == ""


@pytest.mark.parametrize("quiet", [False, True])
def test_overlap_warning_stays_current_and_obeys_text_quiet_rules(
    quiet: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = installed_fixture(tmp_path / "selected")
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("renamed", CORE_TEXT),),
        )
    ).rendered
    visible = tmp_path / "visible.md"
    visible.write_bytes(bundle.target.read_bytes() + b"\n" + foreign.data)
    install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=visible)
    assert invoke(bundle, *("--quiet",) if quiet else ()) == 0
    captured = capsys.readouterr()
    assert captured.out == "CURRENT\n"
    if quiet:
        assert captured.err == ""
    else:
        assert "CROSS_BUNDLE_CONTENT_OVERLAP" in captured.err
        assert "selected" in captured.err
        assert "foreign" in captured.err
        assert "core" in captured.err
        assert "renamed" in captured.err
    assert CORE_TEXT not in captured.out + captured.err


def test_overlap_is_structured_in_json_without_policy_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = installed_fixture(tmp_path / "selected")
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("renamed", CORE_TEXT),),
        )
    ).rendered
    visible = tmp_path / "visible.md"
    visible.write_bytes(bundle.target.read_bytes() + b"\n" + foreign.data)
    install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=visible)
    assert invoke(bundle, "--format", "json", "--quiet") == 0
    captured = capsys.readouterr()
    payload = json_envelope(captured.out)
    assert payload["state"] == "CURRENT"
    assert payload["content_verified"] is True
    raw_findings = payload["diagnostics"]
    assert isinstance(raw_findings, list)
    findings = cast("list[object]", raw_findings)
    assert len(findings) == 1
    finding = cast("dict[str, object]", findings[0])
    assert finding["code"] == "CROSS_BUNDLE_CONTENT_OVERLAP"
    assert finding["severity"] == "warning"
    assert {finding["bundle_id"], finding["other_bundle_id"]} == {"selected", "foreign"}
    assert finding["location"] is not None and finding["other_location"] is not None
    assert captured.err == ""
    for line in CORE_TEXT.splitlines():
        if len(line) > 20:
            assert line not in captured.out


def test_cli_corrupt_middle_fails_without_discarding_probe_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = installed_fixture(tmp_path)
    visible = tmp_path / "visible.md"
    visible.write_bytes(bundle.target.read_bytes().replace(PYTHON_TEXT.encode(), b""))
    mock = install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=visible)
    assert invoke(bundle, "--format", "json") == 1
    captured = capsys.readouterr()
    payload = json_envelope(captured.out)
    assert payload["state"] == "RUNTIME_UNVERIFIED"
    assert payload["ok"] is False
    assert payload["content_verified"] is False
    assert payload["codex_version"] == "codex-cli 9.9.9-mock"
    assert payload["codex_path"] == str(mock.executable)
    assert payload["capability_present"] is True
    assert payload["probe_cwd"] == str(bundle.target.parent)
    assert payload["selected_bundle_occurrences"] == 1
    assert payload["modules_expected"] == 3
    assert payload["diagnostics"]
    for body in (CORE_TEXT, PYTHON_TEXT, EXTRAS_TEXT):
        for line in body.splitlines():
            if len(line) > 20:
                assert line not in captured.out + captured.err


@pytest.mark.parametrize("mode", ["ok", "probe-fails"])
def test_short_modules_never_raise_a_second_sentinel_error(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = installed_fixture(tmp_path, texts=(("short", "x\n"),))
    install_mock_codex(tmp_path, monkeypatch, mode=mode, bundle=bundle.target)
    assert invoke(bundle, "--format", "json") == (0 if mode == "ok" else 1)
    payload = json_envelope(capsys.readouterr().out)
    assert payload["sentinels_expected"] == payload["sentinels_found"] == 0
    assert payload["content_verified"] is (mode == "ok")
    if mode == "probe-fails":
        assert "exit 7" in str(payload["failure"])
        assert payload["selected_bundle_occurrences"] is None
        assert payload["modules_verified"] == 0
        assert payload["capability_present"] is True


def test_capability_failure_preserves_already_observed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = installed_fixture(tmp_path)
    mock = install_mock_codex(
        tmp_path, monkeypatch, mode="no-capability", bundle=bundle.target
    )
    assert invoke(bundle, "--format", "json") == 1
    payload = json_envelope(capsys.readouterr().out)
    assert payload["state"] == "RUNTIME_UNVERIFIED"
    assert payload["codex_path"] == str(mock.executable)
    assert payload["codex_version"] == "codex-cli 9.9.9-mock"
    assert payload["capability_present"] is False
    assert payload["selected_bundle_occurrences"] is None
    assert payload["modules_verified"] == 0


@pytest.mark.parametrize(
    "prefix_kind",
    ["end-before-begin", "wrong-end-id", "nested-open", "malformed-begin"],
)
def test_foreign_boundary_order_cannot_wrap_selected_bundle(
    prefix_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = installed_fixture(tmp_path / "selected")
    foreign = compiled_of(
        make_bundle(tmp_path / "foreign", bundle_id="foreign")
    ).rendered
    header = foreign.data.split(b"\n<!-- agents-md-compiler:module-begin", 1)[0] + b"\n"
    begin = (
        b"<!-- agents-md-compiler:module-begin id=foreign sha256="
        + b"a" * 64
        + b" bytes=999 -->\n"
    )
    end = b"<!-- agents-md-compiler:module-end id=foreign -->\n"
    if prefix_kind == "end-before-begin":
        prefix = end + begin
    elif prefix_kind == "wrong-end-id":
        prefix = begin + end.replace(b"id=foreign", b"id=wrong")
    elif prefix_kind == "nested-open":
        prefix = begin + begin + end
    else:
        prefix = b"<!-- agents-md-compiler:module-begin malformed -->\n" + end
    visible = tmp_path / "visible.md"
    visible.write_bytes(header + prefix + bundle.target.read_bytes())
    install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=visible)
    assert invoke(bundle, "--format", "json") == 1
    payload = json_envelope(capsys.readouterr().out)
    assert payload["state"] == "RUNTIME_UNVERIFIED"
    assert payload["content_verified"] is False
    assert payload["selected_bundle_occurrences"] == 1
    assert "AMBIGUOUS_IDENTITY" in json.dumps(payload["diagnostics"])


def test_cli_mock_preserves_crlf_corruption_for_byte_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = installed_fixture(tmp_path / "selected")
    visible = tmp_path / "crlf.md"
    visible.write_bytes(bundle.target.read_bytes().replace(b"\n", b"\r\n"))
    install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=visible)
    assert invoke(bundle, "--format", "json") == 1
    payload = json_envelope(capsys.readouterr().out)
    assert payload["state"] == "RUNTIME_UNVERIFIED"
    assert payload["content_verified"] is False
    assert "MALFORMED_BOUNDARIES" in json.dumps(payload["diagnostics"])

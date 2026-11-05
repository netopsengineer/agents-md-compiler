"""Boundary and recovery checks supplementing the issue acceptance matrix."""

import subprocess
from pathlib import Path

import pytest
from conftest import CORE_TEXT, compiled_of, make_bundle

from agents_md_compiler import codex
from agents_md_compiler.errors import CodexProblem, CodexVerificationError
from agents_md_compiler.runtime_evidence import (
    DiagnosticCode,
    extract_instruction_evidence,
    inspect_content,
)


@pytest.mark.parametrize(
    "document",
    [
        [1],
        [{}],
        [{"type": 1}],
        [{"type": "message", "role": None}],
        [{"type": "message", "role": "developer", "content": {}}],
        [{"type": "message", "role": "developer", "content": [None]}],
    ],
)
def test_malformed_response_items_fail_closed(document: object) -> None:
    with pytest.raises(CodexVerificationError) as raised:
        extract_instruction_evidence(document)
    assert raised.value.problem is CodexProblem.UNSUPPORTED_EVIDENCE


def _document(text: str) -> object:
    return [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {"type": "input_text", "text": text},
            ],
        }
    ]


@pytest.mark.parametrize("change", ["huge-size", "legacy-title"])
def test_structural_validation_rejects_unrepresentable_lengths_and_title_mismatch(
    tmp_path: Path,
    change: str,
) -> None:
    rendered = compiled_of(make_bundle(tmp_path)).rendered
    text = rendered.data.decode("utf-8")
    if change == "huge-size":
        text = text.replace(
            f"bytes={rendered.modules[0].size_bytes}", "bytes=" + "9" * 5000, 1
        )
    else:
        text = text.replace("# Agent Instructions", "# Global Agent Instructions", 1)
    result = inspect_content(rendered, extract_instruction_evidence(_document(text)))
    assert result.content_verified is False
    assert result.modules_verified <= len(rendered.modules)
    assert result.diagnostics[0].code in {
        DiagnosticCode.CONTENT_MISMATCH,
        DiagnosticCode.MALFORMED_BOUNDARIES,
    }


def test_invalid_foreign_header_cannot_hide_an_unclosed_module(tmp_path: Path) -> None:
    selected = compiled_of(make_bundle(tmp_path / "selected")).rendered
    foreign = compiled_of(
        make_bundle(tmp_path / "foreign", bundle_id="foreign")
    ).rendered
    prefix = foreign.data.split(CORE_TEXT.encode("utf-8"))[0]
    prefix = prefix.replace(b"# Agent Instructions", b"# Altered Instructions", 1)
    fields = extract_instruction_evidence(
        _document((prefix + selected.data).decode("utf-8"))
    )
    result = inspect_content(selected, fields)
    assert result.content_verified is False
    assert result.diagnostics[0].code is DiagnosticCode.AMBIGUOUS_IDENTITY
    assert result.overlap_scan_complete is False


@pytest.mark.parametrize(
    "payload", [b"\xffPRIVATE", b"[" * 2000, b"[" + b"9" * 5000 + b"]"]
)
def test_invalid_probe_json_retains_context_without_echoing_payload(
    payload: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = codex.CodexCapability(Path("/synthetic/codex"), "synthetic", True)

    def invalid_output(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, payload, b"")

    monkeypatch.setattr(codex, "_run", invalid_output)
    with pytest.raises(CodexVerificationError) as raised:
        codex.inspect_prompt_input(capability, expected=(), cwd=tmp_path)
    result = codex.unverified(raised.value, capability=capability)
    assert raised.value.problem is CodexProblem.INVALID_JSON
    assert result.probe_cwd == tmp_path
    assert result.codex_version == "synthetic"
    assert "PRIVATE" not in str(raised.value)
    assert result.selected_bundle_occurrences is None


def test_probe_contamination_error_never_discloses_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "Private canonical sentinel text must not be disclosed"
    monkeypatch.setattr(codex, "PROBE_PROMPT", secret)
    capability = codex.CodexCapability(Path("/synthetic/codex"), "synthetic", True)
    with pytest.raises(CodexVerificationError) as raised:
        codex.inspect_prompt_input(capability, expected=(secret,), cwd=tmp_path)
    assert raised.value.problem is CodexProblem.PROBE_CONTAMINATED
    assert secret not in str(raised.value)
    assert raised.value.command[-1] == "[REDACTED]"


def test_contaminated_directory_remains_redacted_through_probe_failure(
    tmp_path: Path,
) -> None:
    secret = "Private canonical sentinel text must not be disclosed"
    directory = tmp_path / secret
    directory.mkdir()
    capability = codex.CodexCapability(Path("/synthetic/codex"), "synthetic", True)
    with pytest.raises(CodexVerificationError) as raised:
        codex.inspect_prompt_input(capability, expected=(secret,), cwd=directory)
    assert secret not in str(raised.value)
    assert codex.unverified(raised.value).probe_cwd == Path("[REDACTED]")

"""Codex runtime verification.

Every test uses a mock ``codex`` executable installed on ``PATH``, so the verifier's
real subprocess, timeout, and output-capture code runs. No test sends a model
request, spends a token, requires authentication, or reads a real Codex home.
"""

import json
from pathlib import Path

import pytest
from conftest import (
    CORE_TEXT,
    Bundle,
    compiled_of,
    install_mock_codex,
    make_bundle,
    write_lock,
)

from agents_md_compiler import codex
from agents_md_compiler.errors import CodexProblem, CodexVerificationError
from agents_md_compiler.models import BundleState, LockedModule, RenderedBundle


@pytest.fixture
def rendered(bundle: Bundle) -> RenderedBundle:
    """Provide a rendered two-module bundle.

    Args:
        bundle: The bundle fixture.

    Returns:
        The rendered bundle.
    """
    write_lock(bundle)
    return compiled_of(bundle, target=bundle.target).rendered


def bundle_file(tmp_path: Path, rendered: RenderedBundle) -> Path:
    """Write rendered bytes where the mock can echo them.

    Args:
        tmp_path: pytest temporary directory.
        rendered: The rendered bundle.

    Returns:
        The written path.
    """
    path = tmp_path / "visible-bundle.md"
    path.write_bytes(rendered.data)
    return path


def test_string_extraction_is_exhaustive_and_order_independent() -> None:
    document = {
        "b": ["one", {"c": "two", "d": [{"e": "three"}]}],
        "a": "four",
        "n": 5,
        "t": True,
        "z": None,
    }
    found = set(codex.extract_strings(document))
    assert {"one", "two", "three", "four"} <= found
    # Keys are yielded too, so a marker used as a key could not hide.
    assert {"a", "b", "c", "d", "e", "n", "t", "z"} <= found


def test_string_extraction_handles_scalars_and_empty_containers() -> None:
    assert list(codex.extract_strings(7)) == []
    assert list(codex.extract_strings(None)) == []
    assert list(codex.extract_strings([])) == []
    assert list(codex.extract_strings({})) == []
    assert list(codex.extract_strings("solo")) == ["solo"]


def test_string_extraction_ignores_non_string_keys() -> None:
    assert list(codex.extract_strings({1: "value"})) == ["value"]


def test_required_markers_cover_the_header_and_every_module(
    rendered: RenderedBundle,
) -> None:
    markers = codex.required_markers(rendered.modules)
    assert markers[0] == codex.generated_marker()
    assert len(markers) == 1 + 2 * len(rendered.modules)
    text = rendered.data.decode("utf-8")
    for marker in markers:
        assert marker in text


def test_sentinels_come_from_the_first_and_last_modules(
    rendered: RenderedBundle,
) -> None:
    sentinels = codex.content_sentinels(rendered)
    assert len(sentinels) == 2
    text = rendered.data.decode("utf-8")
    for sentinel in sentinels:
        assert len(sentinel) >= codex.MIN_SENTINEL_LENGTH
        assert text.count(sentinel) == 1
        assert not sentinel.startswith("<!--")
    first, last = rendered.modules[0], rendered.modules[-1]
    assert text.index(sentinels[0]) < text.index(codex.end_marker(first))
    assert text.index(sentinels[1]) > text.index(codex.begin_marker(last))


def test_a_single_module_bundle_yields_one_sentinel(tmp_path: Path) -> None:
    single = make_bundle(tmp_path, module_texts=(("core", CORE_TEXT),))
    write_lock(single)
    assert len(codex.content_sentinels(compiled_of(single).rendered)) == 1


def test_identical_sentinels_are_deduplicated(tmp_path: Path) -> None:
    shared_line = "This one long shared sentence appears in both modules verbatim."
    single = make_bundle(
        tmp_path,
        module_texts=(
            ("core", f"# Core\n\n{shared_line}\n\nUnique core tail sentence here.\n"),
            ("python", f"# Python\n\n{shared_line}\n\nOther unique tail sentence.\n"),
        ),
    )
    write_lock(single)
    sentinels = codex.content_sentinels(compiled_of(single).rendered)
    # The shared line occurs twice, so it can never be selected; each module falls
    # through to its own unique line.
    assert len(set(sentinels)) == len(sentinels)
    for sentinel in sentinels:
        assert shared_line not in sentinel


def test_an_html_comment_is_never_chosen_as_a_sentinel(tmp_path: Path) -> None:
    # Real policy sources open with linter directives; a comment arriving proves
    # nothing about the prose, so selection must skip past it.
    commented = make_bundle(
        tmp_path,
        module_texts=(
            (
                "core",
                (
                    "# Core\n\n<!-- markdownlint-disable MD013 and other rules -->"
                    "\n\nThis unique core sentence is the only valid sentinel here.\n"
                ),
            ),
            (
                "python",
                "# Python\n\nThis unique python sentence is the only valid one.\n",
            ),
        ),
    )
    write_lock(commented)
    sentinels = codex.content_sentinels(compiled_of(commented).rendered)
    assert sentinels[0] == "This unique core sentence is the only valid sentinel here."
    for sentinel in sentinels:
        assert not sentinel.startswith("<!--")


def test_a_module_with_no_unique_long_line_is_refused(tmp_path: Path) -> None:
    terse = make_bundle(
        tmp_path,
        module_texts=(("core", "# A\n\nshort\n"), ("python", "# B\n\nalso short\n")),
    )
    write_lock(terse)
    with pytest.raises(CodexVerificationError) as raised:
        codex.content_sentinels(compiled_of(terse).rendered)
    assert raised.value.problem is CodexProblem.SENTINEL_ABSENT


def test_a_missing_executable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(CodexVerificationError) as raised:
        codex.resolve_executable()
    assert raised.value.problem is CodexProblem.EXECUTABLE_MISSING
    assert raised.value.state is BundleState.RUNTIME_UNVERIFIED


def test_capability_detection_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_mock_codex(tmp_path, monkeypatch, mode="ok")
    capability = codex.detect_capability()
    assert capability.version == "codex-cli 9.9.9-mock"
    assert capability.prompt_input_supported is True
    assert capability.path.exists()


def test_a_failing_version_command_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_mock_codex(tmp_path, monkeypatch, mode="version-fails")
    with pytest.raises(CodexVerificationError) as raised:
        codex.detect_capability()
    assert raised.value.problem is CodexProblem.VERSION_FAILED
    assert "--version" in " ".join(raised.value.command)


def test_a_missing_debug_capability_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_mock_codex(tmp_path, monkeypatch, mode="no-capability")
    with pytest.raises(CodexVerificationError) as raised:
        codex.detect_capability()
    assert raised.value.problem is CodexProblem.CAPABILITY_MISSING
    assert "prompt-input" in " ".join(raised.value.command)


def test_a_capability_timeout_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_mock_codex(tmp_path, monkeypatch, mode="timeout")
    with pytest.raises(CodexVerificationError) as raised:
        # A deadline test needs a real deadline; keep it as short as possible.
        codex.detect_capability(timeout_seconds=0.25)
    assert raised.value.problem is CodexProblem.PROBE_TIMEOUT
    assert "exceeded 0.25s" in str(raised.value)


def test_a_probe_timeout_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="timeout-probe",
        bundle=bundle_file(tmp_path, rendered),
    )
    # Capability detection keeps the generous default deadline: it starts a real
    # interpreter twice, and racing that against the probe deadline made this test
    # fail under load while asserting which command timed out.
    capability = codex.detect_capability()
    with pytest.raises(CodexVerificationError) as raised:
        codex.inspect_prompt_input(capability, expected=(), timeout_seconds=0.25)
    assert raised.value.problem is CodexProblem.PROBE_TIMEOUT
    assert "prompt-input" in " ".join(raised.value.command)


def test_verification_keeps_the_capability_deadline_separate_from_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="timeout-probe",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(
            rendered,
            timeout_seconds=0.25,
            capability_timeout_seconds=codex.DEFAULT_TIMEOUT_SECONDS,
        )
    assert raised.value.problem is CodexProblem.PROBE_TIMEOUT
    assert "prompt-input" in " ".join(raised.value.command)


def test_an_unstartable_executable_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "mock-bin"
    directory.mkdir()
    # Present on PATH and marked executable, but not a runnable program.
    broken = directory / "codex"
    broken.write_bytes(b"\x00\x01not a program\n")
    broken.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory))
    with pytest.raises(CodexVerificationError) as raised:
        codex.detect_capability()
    assert raised.value.problem is CodexProblem.EXECUTABLE_MISSING


def test_a_full_verification_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path, monkeypatch, mode="ok", bundle=bundle_file(tmp_path, rendered)
    )
    result = codex.verify_rendered_visibility(rendered)
    assert result.state is BundleState.CURRENT
    assert result.failure is None
    assert result.capability_present is True
    assert result.markers_found == result.markers_expected == 5
    assert result.sentinels_found == result.sentinels_expected == 2
    assert result.codex_version == "codex-cli 9.9.9-mock"
    assert result.probe_command[1:3] == ("debug", "prompt-input")


def test_verification_reports_a_probe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="probe-fails",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.PROBE_FAILED
    assert "exit 7" in str(raised.value)


def test_verification_reports_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="invalid-json",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.INVALID_JSON


def test_verification_reports_a_missing_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="missing-header",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.HEADER_ABSENT


def test_verification_reports_a_missing_module_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="missing-marker",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.MARKER_ABSENT
    assert "module-end" in str(raised.value)


def test_verification_reports_a_duplicated_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="duplicated-marker",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.MARKER_DUPLICATED
    assert "appears 2 times" in str(raised.value)


def test_verification_reports_a_truncated_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    # The head arrives and the tail does not. This is the case a header-only check
    # would pass and a head-to-tail check must fail.
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="truncated-tail",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem in {
        CodexProblem.MARKER_ABSENT,
        CodexProblem.SENTINEL_ABSENT,
    }


def test_verification_reports_a_missing_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    # Every marker present, tail content missing: the exact failure that proves
    # markers alone are not sufficient evidence.
    sentinels = codex.content_sentinels(rendered)
    stripped = rendered.data.decode("utf-8").replace(
        sentinels[-1], "REPLACED TAIL LINE"
    )
    path = tmp_path / "stripped.md"
    path.write_text(stripped, encoding="utf-8")
    install_mock_codex(tmp_path, monkeypatch, mode="ok", bundle=path)
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.SENTINEL_ABSENT


def test_verification_reports_oversized_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path,
        monkeypatch,
        mode="huge-output",
        bundle=bundle_file(tmp_path, rendered),
    )
    with pytest.raises(CodexVerificationError) as raised:
        codex.verify_rendered_visibility(rendered)
    assert raised.value.problem is CodexProblem.OUTPUT_TOO_LARGE


def test_a_contaminated_probe_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path, monkeypatch, mode="ok", bundle=bundle_file(tmp_path, rendered)
    )
    capability = codex.detect_capability()
    with pytest.raises(CodexVerificationError) as raised:
        codex.inspect_prompt_input(capability, expected=(codex.PROBE_PROMPT,))
    assert raised.value.problem is CodexProblem.PROBE_CONTAMINATED


def test_the_probe_directory_holds_no_instruction_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rendered: RenderedBundle
) -> None:
    install_mock_codex(
        tmp_path, monkeypatch, mode="ok", bundle=bundle_file(tmp_path, rendered)
    )
    capability = codex.detect_capability()
    document, command = codex.inspect_prompt_input(capability, expected=())
    assert isinstance(document, dict)
    assert command[-1] == codex.PROBE_PROMPT
    # The temporary directory is removed with the context, so nothing persists.
    assert not list(tmp_path.glob(f"{codex.PROBE_DIRECTORY_PREFIX}*"))


def test_an_unverified_summary_never_reports_current() -> None:
    error = CodexVerificationError(
        CodexProblem.CAPABILITY_MISSING, command=("codex", "debug", "prompt-input")
    )
    result = codex.unverified(error, markers_expected=5, sentinels_expected=2)
    assert result.state is BundleState.RUNTIME_UNVERIFIED
    assert result.markers_found == 0
    assert result.sentinels_found == 0
    assert result.capability_present is False
    assert result.codex_path is None
    assert result.codex_version is None
    assert result.failure is not None
    assert result.probe_command == ("codex", "debug", "prompt-input")


def test_an_unverified_summary_keeps_known_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_mock_codex(tmp_path, monkeypatch, mode="ok")
    capability = codex.detect_capability()
    error = CodexVerificationError(CodexProblem.MARKER_ABSENT, detail="core")
    result = codex.unverified(error, capability=capability, markers_expected=3)
    assert result.state is BundleState.RUNTIME_UNVERIFIED
    assert result.codex_version == "codex-cli 9.9.9-mock"
    assert result.capability_present is True


def test_recorded_prompt_input_shapes_are_searchable(rendered: RenderedBundle) -> None:
    # A recorded shape, not a live probe: proves the search does not depend on the
    # nesting or key order Codex happens to use today.
    recorded = {
        "input": [
            {"content": [{"text": "unrelated preamble"}]},
            {"content": [{"text": rendered.data.decode("utf-8")}]},
        ]
    }
    strings = tuple(codex.extract_strings(json.loads(json.dumps(recorded))))
    for marker in codex.required_markers(rendered.modules):
        assert sum(item.count(marker) for item in strings) == 1


def test_markers_are_built_from_locked_identity() -> None:
    module = LockedModule(
        id="core", resolved_source="/example/core.md", sha256="a" * 64, size_bytes=12
    )
    markers = codex.required_markers((module,))
    assert "id=core" in markers[1]
    assert "sha256=" + "a" * 64 in markers[1]
    assert "bytes=12" in markers[1]
    assert markers[2] == "<!-- agents-md-compiler:module-end id=core -->"

"""Complete-content and bundle-identity runtime regression contracts.

Evidence here is synthetic, shaped after the inspected Codex response-item
producer. No test invokes the real host or reads canonical policy sources.
"""

import json
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import pytest
from conftest import CORE_TEXT, EXTRAS_TEXT, PYTHON_TEXT, compiled_of, make_bundle

from agents_md_compiler import codex, runtime_evidence
from agents_md_compiler.errors import CodexProblem, CodexVerificationError
from agents_md_compiler.models import BundleState, RenderedBundle


def instruction_document(*texts: str, role: str = "developer") -> object:
    """Build separate typed message fields, never a synthetic object envelope."""
    return [
        {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": text}],
        }
        for text in texts
    ]


def verify_document(
    rendered: RenderedBundle, document: object, monkeypatch: pytest.MonkeyPatch
) -> codex.RuntimeVerification:
    """Exercise the verifier while replacing only host capability and probe I/O."""
    capability = codex.CodexCapability(Path("/synthetic/codex"), "synthetic", True)

    def detect(
        *, name: str = "codex", timeout_seconds: float = 60.0
    ) -> codex.CodexCapability:
        return capability

    def inspect(
        actual: codex.CodexCapability,
        *,
        expected: Sequence[str],
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[object, tuple[str, ...], Path]:
        assert actual is capability
        return (
            document,
            (str(capability.path), "debug", "prompt-input", codex.PROBE_PROMPT),
            Path("/synthetic/probe"),
        )

    monkeypatch.setattr(codex, "detect_capability", detect)
    monkeypatch.setattr(codex, "inspect_prompt_input", inspect)
    return codex.verify_rendered_visibility(rendered)


@pytest.fixture
def three_rendered(tmp_path: Path) -> RenderedBundle:
    return compiled_of(
        make_bundle(
            tmp_path / "selected",
            bundle_id="selected",
            module_texts=(
                ("core", CORE_TEXT),
                ("python", PYTHON_TEXT),
                ("extras", EXTRAS_TEXT),
            ),
        )
    ).rendered


def test_issue_31_removed_middle_body_cannot_verify(
    three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = three_rendered.data.decode("utf-8").replace(PYTHON_TEXT, "")
    with pytest.raises(CodexVerificationError):
        verify_document(three_rendered, instruction_document(text), monkeypatch)


def test_issue_32_foreign_shared_module_does_not_duplicate_selected_bundle(
    three_rendered: RenderedBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("core", CORE_TEXT),),
        )
    ).rendered
    document = instruction_document(
        three_rendered.data.decode("utf-8") + "\n" + foreign.data.decode("utf-8")
    )
    result = verify_document(three_rendered, document, monkeypatch)
    assert result.state is BundleState.CURRENT
    assert result.content_verified is True
    assert result.selected_bundle_occurrences == 1
    overlap = [
        finding
        for finding in result.diagnostics
        if finding.code == "CROSS_BUNDLE_CONTENT_OVERLAP"
    ]
    assert len(overlap) == 1
    finding = overlap[0]
    assert finding.severity == "warning"
    assert {finding.bundle_id, finding.other_bundle_id} == {"selected", "foreign"}
    assert finding.module_id == finding.other_module_id == "core"
    assert finding.sha256 == three_rendered.modules[0].sha256
    assert finding.location is not None
    assert finding.other_location is not None
    assert finding.location.json_path == finding.other_location.json_path
    assert finding.location.byte_start != finding.other_location.byte_start


def module_block(rendered: RenderedBundle, index: int) -> bytes:
    """Locate a fixture block including the compiler's separator and final LF."""
    module = rendered.modules[index]
    beginning = ("\n" + codex.begin_marker(module) + "\n").encode("ascii")
    ending = (codex.end_marker(module) + "\n").encode("ascii")
    start = rendered.data.index(beginning)
    stop = rendered.data.index(ending, start) + len(ending)
    return rendered.data[start:stop]


@pytest.mark.parametrize("role", ["system", "developer", "user"])
def test_intact_bundle_is_verified_in_supported_instruction_roles(
    role: str, three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = three_rendered.data.decode("utf-8")
    if role == "user":
        text = (
            "# AGENTS.md instructions for /workspace\n\n<INSTRUCTIONS>\n"
            + text
            + "\n</INSTRUCTIONS>"
        )
    result = verify_document(
        three_rendered, instruction_document(text, role=role), monkeypatch
    )
    assert result.state is BundleState.CURRENT
    assert result.verification_method == "complete_bundle_bytes"
    assert result.content_verified is True
    assert result.modules_verified == result.modules_expected == 3
    assert result.selected_bundle_occurrences == 1
    assert result.overlap_scan_complete is True
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "prefix,suffix", [("", ""), ("Host preamble\n", "Host tail\n"), ("\n\n", "\n\n")]
)
def test_host_prose_may_surround_an_intact_bundle(
    prefix: str,
    suffix: str,
    three_rendered: RenderedBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = prefix + three_rendered.data.decode("utf-8") + suffix
    assert verify_document(
        three_rendered, instruction_document(text), monkeypatch
    ).content_verified


@pytest.mark.parametrize(
    "texts",
    [
        (("short", "x\n"),),
        (("first", "# A\nsame\n"), ("last", "# B\nsame\n")),
        (("first", "# First\n" + CORE_TEXT), ("last", "# Last\n" + CORE_TEXT)),
    ],
)
def test_no_unique_sentinel_does_not_prevent_byte_verification(
    texts: tuple[tuple[str, str], ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = compiled_of(make_bundle(tmp_path, module_texts=texts)).rendered
    result = verify_document(
        rendered, instruction_document(rendered.data.decode("utf-8")), monkeypatch
    )
    assert result.content_verified is True
    assert result.sentinels_expected == result.sentinels_found == 0
    assert result.modules_verified == len(texts)


@pytest.mark.parametrize(
    "corruption",
    [
        "changed-middle",
        "removed-first",
        "removed-last",
        "reordered",
        "duplicated",
        "missing",
        "relocated",
        "truncated",
        "wrong-size",
        "wrong-body-digest",
        "wrong-manifest-digest",
        "wrong-lock-digest",
        "wrong-format",
        "wrong-end-id",
        "missing-separator",
        "missing-final-lf",
        "crlf",
        "whitespace",
        "nested-marker",
    ],
)
def test_complete_bundle_integrity_rejects_corruption(
    corruption: str, three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = three_rendered.data
    first, middle, last = (module_block(three_rendered, i) for i in range(3))
    match corruption:
        case "changed-middle":
            data = data.replace(
                PYTHON_TEXT.encode(), PYTHON_TEXT.replace("Type", "Wipe").encode()
            )
        case "removed-first":
            data = data.replace(CORE_TEXT.encode(), b"")
        case "removed-last":
            data = data.replace(EXTRAS_TEXT.encode(), b"")
        case "reordered":
            data = data.replace(first + middle + last, last + middle + first)
        case "duplicated":
            data = data.replace(middle, middle + middle)
        case "missing":
            data = data.replace(middle, b"")
        case "relocated":
            data = data.replace(PYTHON_TEXT.encode(), b"") + PYTHON_TEXT.encode()
        case "truncated":
            data = data[:-35]
        case "wrong-size":
            module = three_rendered.modules[1]
            data = data.replace(
                f"bytes={module.size_bytes}".encode(),
                f"bytes={module.size_bytes + 1}".encode(),
            )
        case "wrong-body-digest":
            data = data.replace(three_rendered.modules[1].sha256.encode(), b"0" * 64)
        case "wrong-manifest-digest":
            data = data.replace(three_rendered.manifest_sha256.encode(), b"0" * 64)
        case "wrong-lock-digest":
            data = data.replace(three_rendered.lock_sha256.encode(), b"0" * 64)
        case "wrong-format":
            data = data.replace(b"generated format=2", b"generated format=3")
        case "wrong-end-id":
            data = data.replace(b"module-end id=python", b"module-end id=wrong")
        case "missing-separator":
            data = data.replace(
                b"\n\n<!-- agents-md-compiler:module-begin",
                b"\n<!-- agents-md-compiler:module-begin",
                1,
            )
        case "missing-final-lf":
            data = data[:-1]
        case "crlf":
            data = data.replace(b"\n", b"\r\n")
        case "whitespace":
            data = data.replace(b"Type every public", b"Type  every public")
        case "nested-marker":
            data = data.replace(
                b"Type every public",
                b"<!-- agents-md-compiler:nested -->\nType every public",
            )
        case _:
            raise AssertionError(corruption)
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(
            three_rendered, instruction_document(data.decode("utf-8")), monkeypatch
        )
    result = codex.unverified(raised.value)
    assert result.state is BundleState.RUNTIME_UNVERIFIED
    assert result.content_verified is False
    assert result.codex_version == "synthetic"
    assert result.probe_cwd == Path("/synthetic/probe")
    assert result.modules_verified <= result.modules_expected == 3
    assert CORE_TEXT not in str(raised.value)
    assert PYTHON_TEXT not in str(raised.value)


@pytest.mark.parametrize("extra_kind", ["block", "end", "begin", "unknown"])
@pytest.mark.parametrize(
    "prose", ["", "\nOrdinary host prose cannot hide later compiler tokens.\n"]
)
def test_compiler_tokens_after_intact_prefix_cannot_be_discarded(
    extra_kind: str,
    prose: str,
    three_rendered: RenderedBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = {
        "block": module_block(three_rendered, 1).decode("utf-8"),
        "end": codex.end_marker(three_rendered.modules[1]) + "\n",
        "begin": codex.begin_marker(three_rendered.modules[1]) + "\n",
        "unknown": "<!-- agents-md-compiler:unrecognized -->\n",
    }[extra_kind]
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(
            three_rendered,
            instruction_document(three_rendered.data.decode("utf-8") + prose + extra),
            monkeypatch,
        )
    assert raised.value.problem is CodexProblem.MALFORMED_BOUNDARIES


@pytest.mark.parametrize("separate_fields", [False, True])
def test_unrelated_foreign_bundle_does_not_change_selected_result(
    separate_fields: bool,
    three_rendered: RenderedBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("unrelated", "Unrelated foreign body.\n"),),
        )
    ).rendered
    texts = (three_rendered.data.decode("utf-8"), foreign.data.decode("utf-8"))
    document = (
        instruction_document(*texts)
        if separate_fields
        else instruction_document("\n".join(texts))
    )
    result = verify_document(three_rendered, document, monkeypatch)
    assert result.state is BundleState.CURRENT
    assert result.modules_verified == 3
    assert result.markers_found == result.markers_expected == 7
    assert result.diagnostics == ()


@pytest.mark.parametrize("same_id", [False, True])
@pytest.mark.parametrize("separate_fields", [False, True])
def test_identical_bodies_are_overlap_regardless_of_module_id(
    same_id: bool,
    separate_fields: bool,
    three_rendered: RenderedBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_id = "core" if same_id else "renamed"
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=((module_id, CORE_TEXT),),
        )
    ).rendered
    texts = (three_rendered.data.decode("utf-8"), foreign.data.decode("utf-8"))
    document = (
        instruction_document(*texts)
        if separate_fields
        else instruction_document("\n".join(texts))
    )
    result = verify_document(three_rendered, document, monkeypatch)
    assert result.state is BundleState.CURRENT
    assert result.content_verified is True
    assert result.overlap_scan_complete is True
    assert len(result.diagnostics) == 1
    finding = result.diagnostics[0]
    assert finding.code == "CROSS_BUNDLE_CONTENT_OVERLAP"
    assert finding.severity == "warning"
    assert {finding.module_id, finding.other_module_id} == {"core", module_id}
    assert finding.sha256 == finding.other_sha256 == three_rendered.modules[0].sha256
    assert finding.location is not None and finding.other_location is not None
    assert (
        finding.location.json_path != finding.other_location.json_path
    ) is separate_fields
    assert finding.location.role == finding.other_location.role == "developer"
    assert finding.location.byte_end > finding.location.byte_start >= 0
    assert finding.other_location.byte_end > finding.other_location.byte_start >= 0


def test_equal_module_ids_with_different_bodies_are_only_informational(
    three_rendered: RenderedBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("core", "Different core body.\n"),),
        )
    ).rendered
    result = verify_document(
        three_rendered,
        instruction_document(
            three_rendered.data.decode("utf-8"), foreign.data.decode("utf-8")
        ),
        monkeypatch,
    )
    assert result.state is BundleState.CURRENT
    assert len(result.diagnostics) == 1
    finding = result.diagnostics[0]
    assert finding.code == "MODULE_ID_REUSE"
    assert finding.severity == "info"
    assert finding.module_id == finding.other_module_id == "core"
    assert finding.sha256 != finding.other_sha256


@pytest.mark.parametrize("separate_fields", [False, True])
@pytest.mark.parametrize("altered", [False, True])
def test_every_selected_identity_is_counted_before_success(
    separate_fields: bool,
    altered: bool,
    three_rendered: RenderedBundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = three_rendered.data.decode("utf-8")
    second = (
        current.replace(three_rendered.lock_sha256, "0" * 64) if altered else current
    )
    document = (
        instruction_document(current, second)
        if separate_fields
        else instruction_document(current + "\n" + second)
    )
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(three_rendered, document, monkeypatch)
    expected = (
        CodexProblem.AMBIGUOUS_IDENTITY
        if altered
        else CodexProblem.DUPLICATE_SELECTED_BUNDLE
    )
    assert raised.value.problem is expected
    result = codex.unverified(raised.value)
    assert result.selected_bundle_occurrences == 2
    assert result.content_verified is False


@pytest.mark.parametrize("role", ["user", "assistant", "tool"])
def test_unwrapped_non_instruction_roles_do_not_establish_visibility(
    role: str, three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(
            three_rendered,
            instruction_document(three_rendered.data.decode("utf-8"), role=role),
            monkeypatch,
        )
    assert raised.value.problem is CodexProblem.SELECTED_BUNDLE_MISSING


@pytest.mark.parametrize(
    "placement",
    ["key", "metadata", "tool-output", "output-text", "object-envelope", "scalar"],
)
def test_arbitrary_json_strings_do_not_establish_visibility(
    placement: str, three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = three_rendered.data.decode("utf-8")
    documents: dict[str, object] = {
        "key": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "ordinary", text: "value"}],
            }
        ],
        "metadata": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "ordinary"}],
                "metadata": text,
            }
        ],
        "tool-output": [
            {"type": "function_call_output", "call_id": "synthetic", "output": text}
        ],
        "output-text": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "object-envelope": {"input": instruction_document(text)},
        "scalar": text,
    }
    with pytest.raises(CodexVerificationError):
        verify_document(three_rendered, documents[placement], monkeypatch)


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {},
        1,
        "text",
        [{"type": "message", "role": "developer", "content": "text"}],
        [{"type": "message", "role": "developer", "content": [{"type": "input_text"}]}],
        [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": 7}],
            }
        ],
        [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "future", "text": "unknown"}],
            }
        ],
    ],
)
def test_unsupported_or_malformed_evidence_is_explicit(
    malformed: object, three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(three_rendered, malformed, monkeypatch)
    assert raised.value.problem is CodexProblem.UNSUPPORTED_EVIDENCE
    assert codex.unverified(raised.value).selected_bundle_occurrences is None


def test_complete_bundle_split_between_fields_is_not_reassembled(
    three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = three_rendered.data.decode("utf-8")
    cut = text.index(PYTHON_TEXT) + len(PYTHON_TEXT) // 2
    with pytest.raises(CodexVerificationError):
        verify_document(
            three_rendered, instruction_document(text[:cut], text[cut:]), monkeypatch
        )


def test_supported_non_text_content_does_not_hide_an_intact_bundle(
    three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {"type": "input_image", "image_url": "synthetic"},
                {"type": "input_audio", "audio_url": "synthetic"},
                {"type": "input_text", "text": three_rendered.data.decode("utf-8")},
            ],
        }
    ]
    assert verify_document(three_rendered, document, monkeypatch).content_verified


@pytest.mark.parametrize("json_escaped", [False, True])
def test_multibyte_content_uses_utf8_bytes_not_character_lengths(
    json_escaped: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "# UTF-8\n\nCaf\u00e9 and \U0001f680 are exact source bytes.\n"
    rendered = compiled_of(
        make_bundle(tmp_path, module_texts=(("unicode", body),))
    ).rendered
    document = instruction_document(rendered.data.decode("utf-8"))
    document = json.loads(json.dumps(document, ensure_ascii=json_escaped))
    result = verify_document(rendered, document, monkeypatch)
    assert result.content_verified is True
    assert result.modules_verified == 1


def test_unicode_normalization_is_not_permitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "# Unicode\n\nCaf\u00e9 is a deliberately composed source word.\n"
    rendered = compiled_of(
        make_bundle(tmp_path, module_texts=(("unicode", body),))
    ).rendered
    text = unicodedata.normalize("NFD", rendered.data.decode("utf-8"))
    with pytest.raises(CodexVerificationError):
        verify_document(rendered, instruction_document(text), monkeypatch)


def test_unpaired_surrogate_cannot_be_instruction_evidence(
    three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = instruction_document(three_rendered.data.decode("utf-8") + "\ud800")
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(three_rendered, document, monkeypatch)
    assert raised.value.problem is CodexProblem.UNSUPPORTED_EVIDENCE


@pytest.mark.parametrize("separate_fields", [False, True])
def test_malformed_foreign_bundle_does_not_invent_overlap(
    separate_fields: bool,
    three_rendered: RenderedBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("core", CORE_TEXT),),
        )
    ).rendered
    malformed = foreign.data.decode("utf-8").replace(CORE_TEXT, "broken\n")
    current = three_rendered.data.decode("utf-8")
    document = (
        instruction_document(current, malformed)
        if separate_fields
        else instruction_document(current + "\n" + malformed)
    )
    result = verify_document(three_rendered, document, monkeypatch)
    assert result.state is BundleState.CURRENT
    assert result.content_verified is True
    assert result.overlap_scan_complete is False
    assert result.diagnostics
    assert not any(
        finding.code == "CROSS_BUNDLE_CONTENT_OVERLAP" for finding in result.diagnostics
    )


def test_failure_reporting_keeps_probe_metadata_and_hides_policy_prose(
    three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = three_rendered.data.decode("utf-8").replace(EXTRAS_TEXT, "missing\n")
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(three_rendered, instruction_document(text), monkeypatch)
    result = codex.unverified(raised.value)
    assert result.codex_path == Path("/synthetic/codex")
    assert result.codex_version == "synthetic"
    assert result.capability_present is True
    assert result.probe_cwd == Path("/synthetic/probe")
    assert result.probe_command[-1] == codex.PROBE_PROMPT
    assert result.selected_bundle_occurrences == 1
    serialized = str(raised.value) + repr(result)
    for body in (CORE_TEXT, PYTHON_TEXT, EXTRAS_TEXT):
        for line in body.splitlines():
            if len(line) >= codex.MIN_SENTINEL_LENGTH:
                assert line not in serialized


def test_agents_wrapper_without_directory_preserves_final_lf(
    three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = "# AGENTS.md instructions\n\n<INSTRUCTIONS>\n"
    suffix = "\n</INSTRUCTIONS>"
    text = three_rendered.data.decode("utf-8")
    result = verify_document(
        three_rendered,
        instruction_document(prefix + text + suffix, role="user"),
        monkeypatch,
    )
    assert result.content_verified is True
    with pytest.raises(CodexVerificationError):
        verify_document(
            three_rendered,
            instruction_document(
                prefix + text.removesuffix("\n") + suffix, role="user"
            ),
            monkeypatch,
        )


@pytest.mark.parametrize("suffix", ["", "</INSTRUCTIONS>", "\n</INSTRUCTIONS>\n"])
def test_malformed_agents_wrapper_is_not_normalized(
    suffix: str, three_rendered: RenderedBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (
        "# AGENTS.md instructions for /workspace\n\n<INSTRUCTIONS>\n"
        + three_rendered.data.decode("utf-8")
        + suffix
    )
    with pytest.raises(CodexVerificationError):
        verify_document(
            three_rendered, instruction_document(text, role="user"), monkeypatch
        )


def test_partial_foreign_candidate_does_not_contribute_completed_prefix_modules(
    three_rendered: RenderedBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("core", CORE_TEXT), ("tail", "Foreign tail.\n")),
        )
    ).rendered
    damaged_foreign = foreign.data.decode("utf-8").replace("Foreign tail.\n", "")
    result = verify_document(
        three_rendered,
        instruction_document(three_rendered.data.decode("utf-8"), damaged_foreign),
        monkeypatch,
    )
    assert result.content_verified is True
    assert result.overlap_scan_complete is False
    assert not any(
        finding.code == "CROSS_BUNDLE_CONTENT_OVERLAP" for finding in result.diagnostics
    )


def test_selected_header_inside_an_unclosed_foreign_module_is_ambiguous(
    three_rendered: RenderedBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = compiled_of(
        make_bundle(
            tmp_path / "foreign",
            bundle_id="foreign",
            module_texts=(("foreign", "Foreign module bytes.\n"),),
        )
    ).rendered
    open_foreign = foreign.data[: foreign.data.index(b"Foreign module bytes.")]
    document = instruction_document(
        (open_foreign + three_rendered.data).decode("utf-8")
    )
    with pytest.raises(CodexVerificationError) as raised:
        verify_document(three_rendered, document, monkeypatch)
    assert raised.value.problem is CodexProblem.AMBIGUOUS_IDENTITY
    assert codex.unverified(raised.value).selected_bundle_occurrences == 1


def test_overlap_diagnostic_bound_retains_selected_integrity_and_prior_findings(
    three_rendered: RenderedBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign_texts = tuple(
        compiled_of(
            make_bundle(
                tmp_path / identity,
                bundle_id=identity,
                module_texts=(("renamed", CORE_TEXT),),
            )
        ).rendered.data.decode("utf-8")
        for identity in ("foreign-one", "foreign-two")
    )
    monkeypatch.setattr(runtime_evidence, "MAX_OVERLAP_DIAGNOSTICS", 1)
    result = verify_document(
        three_rendered,
        instruction_document(three_rendered.data.decode("utf-8"), *foreign_texts),
        monkeypatch,
    )
    assert result.state is BundleState.CURRENT
    assert result.content_verified is True
    assert result.modules_verified == 3
    assert result.overlap_scan_complete is False
    assert [finding.code for finding in result.diagnostics] == [
        "CROSS_BUNDLE_CONTENT_OVERLAP",
        "OVERLAP_SCAN_LIMIT",
    ]
    assert result.diagnostics[0].bundle_id == "selected"
    assert result.diagnostics[0].other_bundle_id == "foreign-one"
    assert result.diagnostics[1].severity == "warning"
    for line in CORE_TEXT.splitlines():
        if len(line) >= codex.MIN_SENTINEL_LENGTH:
            assert line not in repr(result)

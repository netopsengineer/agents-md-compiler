"""The public exception taxonomy: state mapping, message composition, path pairs.

Every exception is part of the public API, so every class is constructed directly
here rather than only reached through a scenario. That proves the state mapping the
CLI relies on for exit codes, and it proves no message leaks policy content.
"""

from pathlib import Path

import pytest

from agents_md_compiler import errors
from agents_md_compiler.models import BundleState, PathPair

POLICY_BODY = "SECRET POLICY PROSE THAT MUST NEVER APPEAR IN A DIAGNOSTIC"

STATE_MAPPING: list[tuple[type[errors.CompilerError], BundleState | None]] = [
    (errors.CompilerError, None),
    (errors.UsageError, None),
    (errors.OutputExistsError, None),
    (errors.TargetError, None),
    (errors.ReceiptError, None),
    (errors.MutationError, None),
    (errors.ManifestError, BundleState.INVALID_MANIFEST),
    (errors.SourceError, BundleState.INVALID_SOURCE),
    (errors.RenderError, BundleState.INVALID_SOURCE),
    (errors.LockError, BundleState.INVALID_LOCK),
    (errors.LockMissingError, BundleState.LOCK_MISSING),
    (errors.LockStaleError, BundleState.LOCK_STALE),
    (errors.ShadowedError, BundleState.SHADOWED),
    (errors.UnmanagedTargetError, BundleState.UNMANAGED_TARGET),
    (errors.ConcurrentChangeError, BundleState.CONCURRENT_CHANGE),
    (errors.CodexVerificationError, BundleState.RUNTIME_UNVERIFIED),
]


@pytest.mark.parametrize(
    ("error_type", "expected"),
    STATE_MAPPING,
    ids=[error_type.__name__ for error_type, _ in STATE_MAPPING],
)
def test_every_public_error_declares_its_state(
    error_type: type[errors.CompilerError], expected: BundleState | None
) -> None:
    assert error_type.state is expected
    assert issubclass(error_type, Exception)


def test_the_base_error_carries_a_message_and_no_paths() -> None:
    error = errors.CompilerError("plain failure")
    assert str(error) == "plain failure"
    assert error.paths is None


def test_the_base_error_can_carry_a_path_pair() -> None:
    pair = PathPair(lexical="~/x.md", resolved="/home/u/x.md")
    assert errors.CompilerError("failure", paths=pair).paths is pair


def test_usage_errors_name_the_invocation_problem() -> None:
    error = errors.UsageError("--expect-target-sha256 requires --replace-unmanaged")
    assert "invalid invocation" in str(error)
    assert "--replace-unmanaged" in str(error)


def test_manifest_errors_without_a_path_still_compose() -> None:
    error = errors.ManifestError(errors.ManifestProblem.NO_MODULES)
    assert str(error) == errors.ManifestProblem.NO_MODULES.value
    assert error.paths is None


def test_manifest_errors_with_a_path_record_both_forms() -> None:
    error = errors.ManifestError(
        errors.ManifestProblem.UNKNOWN_KEY,
        detail="strict",
        manifest=Path("/policy/global-agents.toml"),
        lexical="global-agents.toml",
    )
    assert "unknown top-level key" in str(error)
    assert "(strict)" in str(error)
    assert error.paths is not None
    assert error.paths.lexical == "global-agents.toml"
    assert error.paths.resolved == "/policy/global-agents.toml"


def test_a_path_pair_defaults_its_lexical_form_to_the_resolved_form() -> None:
    error = errors.ManifestError(
        errors.ManifestProblem.SYNTAX, manifest=Path("/policy/global-agents.toml")
    )
    assert error.paths is not None
    assert error.paths.lexical == "/policy/global-agents.toml"


def test_source_errors_report_the_module_and_a_link_target() -> None:
    error = errors.SourceError(
        errors.SourceProblem.SYMLINK,
        module_id="core",
        source=Path("/policy/modules/core.md"),
        lexical="modules/core.md",
        link_target=Path("/elsewhere/core.md"),
    )
    text = str(error)
    assert "module 'core'" in text
    assert "symbolic link" in text
    assert "declared as 'modules/core.md'" in text
    assert "linking to /elsewhere/core.md" in text
    assert error.module_id == "core"


def test_source_errors_omit_the_declared_form_when_it_matches() -> None:
    error = errors.SourceError(
        errors.SourceProblem.EMPTY,
        module_id="core",
        source=Path("/policy/core.md"),
        lexical="/policy/core.md",
    )
    assert "declared as" not in str(error)


def test_lock_errors_compose_with_and_without_a_path() -> None:
    bare = errors.LockError(errors.LockProblem.SYNTAX)
    assert bare.paths is None
    located = errors.LockError(
        errors.LockProblem.UNKNOWN_KEY, detail="extra", lock=Path("/policy/lock.json")
    )
    assert "in /policy/lock.json" in str(located)


def test_lock_missing_errors_tell_the_operator_what_to_run() -> None:
    error = errors.LockMissingError(lock=Path("/policy/lock.json"), lexical="lock.json")
    assert "run 'lock'" in str(error)


def test_lock_stale_errors_explain_the_difference() -> None:
    error = errors.LockStaleError(
        errors.LockStaleProblem.SOURCES_CHANGED,
        lock=Path("/policy/lock.json"),
        lexical="lock.json",
        detail="core",
    )
    assert "source bytes or paths differ" in str(error)
    assert "(core)" in str(error)
    assert "run 'lock' to refresh it" in str(error)


def test_lock_stale_errors_compose_without_detail() -> None:
    error = errors.LockStaleError(
        errors.LockStaleProblem.MANIFEST_CHANGED,
        lock=Path("/policy/lock.json"),
        lexical="lock.json",
    )
    assert "manifest bytes differ from the lock at /policy/lock.json" in str(error)
    assert "(" not in str(error)


def test_render_errors_compose_with_and_without_detail() -> None:
    bare = errors.RenderError(errors.RenderProblem.HEADER_MISMATCH)
    assert bare.paths is None
    detailed = errors.RenderError(errors.RenderProblem.TRUNCATED, detail="python")
    assert "(python)" in str(detailed)


def test_output_exists_errors_point_at_install() -> None:
    error = errors.OutputExistsError(
        output=Path("/policy/out/AGENTS.md"), lexical="out/AGENTS.md"
    )
    assert "refusing to write" in str(error)
    assert "'install'" in str(error)


def test_shadowed_errors_name_both_files() -> None:
    error = errors.ShadowedError(
        override=Path("/home/u/.codex/AGENTS.override.md"),
        target=Path("/home/u/.codex/AGENTS.md"),
    )
    assert "AGENTS.override.md" in str(error)
    assert "would be loaded instead of" in str(error)


def test_unmanaged_target_errors_compose_with_and_without_detail() -> None:
    bare = errors.UnmanagedTargetError(
        errors.UnmanagedTargetProblem.NO_AUTHORIZATION,
        target=Path("/home/u/AGENTS.md"),
    )
    assert "requires --replace-unmanaged" in str(bare)
    detailed = errors.UnmanagedTargetError(
        errors.UnmanagedTargetProblem.DIGEST_MISMATCH,
        target=Path("/home/u/AGENTS.md"),
        detail="expected abc, observed def",
    )
    assert "(expected abc, observed def)" in str(detailed)


def test_concurrent_change_errors_compose_with_and_without_detail() -> None:
    bare = errors.ConcurrentChangeError(
        errors.ConcurrentChangeProblem.TARGET_VANISHED, path=Path("/home/u/AGENTS.md")
    )
    assert "disappeared" in str(bare)
    detailed = errors.ConcurrentChangeError(
        errors.ConcurrentChangeProblem.TARGET_CHANGED,
        path=Path("/home/u/AGENTS.md"),
        detail="expected abc",
    )
    assert "(expected abc)" in str(detailed)


def test_target_errors_record_both_path_forms() -> None:
    error = errors.TargetError(
        errors.TargetProblem.SYMLINK,
        target=Path("/home/u/.codex/AGENTS.md"),
        lexical="~/.codex/AGENTS.md",
    )
    assert error.paths is not None
    assert error.paths.lexical == "~/.codex/AGENTS.md"
    assert "will not be followed" in str(error)


def test_receipt_errors_compose_with_and_without_detail() -> None:
    bare = errors.ReceiptError(
        errors.ReceiptProblem.SYMLINK, receipt=Path("/state/receipt.json")
    )
    assert "symbolic link" in str(bare)
    assert bare.paths is not None
    assert bare.paths.lexical == "/state/receipt.json"
    detailed = errors.ReceiptError(
        errors.ReceiptProblem.TARGET_MISMATCH,
        receipt=Path("/state/receipt.json"),
        lexical="receipt.json",
        detail="records /other/AGENTS.md",
    )
    assert "(records /other/AGENTS.md)" in str(detailed)
    assert detailed.paths is not None
    assert detailed.paths.lexical == "receipt.json"


def test_mutation_errors_compose_with_and_without_a_path() -> None:
    bare = errors.MutationError(errors.MutationProblem.SYNC_FAILED)
    assert bare.paths is None
    assert "syncing" in str(bare)
    located = errors.MutationError(
        errors.MutationProblem.REPLACE_FAILED,
        path=Path("/home/u/AGENTS.md"),
        detail="Read-only file system",
    )
    assert "(Read-only file system)" in str(located)
    assert located.paths is not None


def test_codex_errors_record_the_exact_command() -> None:
    error = errors.CodexVerificationError(
        errors.CodexProblem.MARKER_ABSENT,
        detail="core",
        command=("codex", "debug", "prompt-input", "ping"),
    )
    assert error.command == ("codex", "debug", "prompt-input", "ping")
    assert "running: codex debug prompt-input ping" in str(error)
    assert "(core)" in str(error)


def test_codex_errors_compose_without_a_command() -> None:
    error = errors.CodexVerificationError(errors.CodexProblem.EXECUTABLE_MISSING)
    assert error.command == ()
    assert "running:" not in str(error)


def test_every_problem_enum_member_has_unique_prose() -> None:
    enums = [
        errors.ManifestProblem,
        errors.SourceProblem,
        errors.LockProblem,
        errors.LockStaleProblem,
        errors.RenderProblem,
        errors.UnmanagedTargetProblem,
        errors.ConcurrentChangeProblem,
        errors.TargetProblem,
        errors.ReceiptProblem,
        errors.MutationProblem,
        errors.CodexProblem,
    ]
    for enum_type in enums:
        values = [member.value for member in enum_type]
        assert len(values) == len(set(values)), enum_type.__name__
        for value in values:
            assert value == value.strip()
            assert value


def test_no_diagnostic_echoes_policy_content() -> None:
    # Errors are built from paths, identifiers, digests, sizes, and fixed prose.
    # None of the constructors accept content, so none can leak it.
    error = errors.SourceError(
        errors.SourceProblem.HAS_MARKER,
        module_id="core",
        source=Path("/policy/core.md"),
        lexical="core.md",
        detail="at byte 42",
    )
    assert POLICY_BODY not in str(error)
    assert "at byte 42" in str(error)

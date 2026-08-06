"""Installation and rollback: preconditions, atomicity, receipts, and refusals.

Every test operates on a disposable bundle and a disposable state root under
``tmp_path``. No test reads or writes a real user configuration path, a real Codex
home, or a canonical policy source.

Clock and operation identifier are injected, so receipt and backup names are
deterministic and no assertion depends on wall-clock timing.
"""

import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    FIXED_MOMENT,
    FIXED_OPERATION_ID,
    Bundle,
    compiled_of,
    fixed_clock,
    fixed_operation_id,
    write_text_file,
)

from agents_md_compiler import installation
from agents_md_compiler.errors import (
    ConcurrentChangeError,
    ConcurrentChangeProblem,
    MutationError,
    MutationProblem,
    ReceiptError,
    ReceiptProblem,
    ShadowedError,
    TargetError,
    TargetProblem,
    UnmanagedTargetError,
    UnmanagedTargetProblem,
)
from agents_md_compiler.hashing import sha256_file
from agents_md_compiler.installation import (
    format_file_stamp,
    install_bundle,
    plan_install,
    rollback_install,
)
from agents_md_compiler.locking import lock_path_for
from agents_md_compiler.models import (
    BackupRecord,
    BundleState,
    InstallOutcome,
    InstallReceipt,
    PathPair,
    TargetInspection,
    TargetKind,
)
from agents_md_compiler.receipts import (
    BACKUPS_DIRNAME,
    LAST_INSTALLED_FILENAME,
    LOCKS_DIRNAME,
    PRESERVED_DIRNAME,
    RECEIPTS_DIRNAME,
    load_install_receipt,
    receipt_name,
)

UNMANAGED_TEXT = "# Hand written policy\n\nBody that must be preserved.\n"
FAILED_INSTALL_BYTES = b"failed install bytes\n"


def state_dir_of(bundle: Bundle) -> Path:
    """Locate the disposable per-bundle state directory.

    Args:
        bundle: The bundle.

    Returns:
        The state directory path.
    """
    return bundle.state_root / "test-bundle"


def install(
    bundle: Bundle,
    *,
    apply: bool = False,
    replace_unmanaged: bool = False,
    expect_target_sha256: str | None = None,
    target: Path | None = None,
    lock_timeout_seconds: float | None = None,
) -> InstallOutcome:
    """Install a bundle with pinned clock and identifier.

    Args:
        bundle: The bundle to install.
        apply: Perform the mutation.
        replace_unmanaged: Authorize replacing an unmanaged target.
        expect_target_sha256: Digest captured before the dry run.
        target: Override the bundle's default target.
        lock_timeout_seconds: Advisory lock deadline.

    Returns:
        The install outcome.
    """
    effective = bundle.target if target is None else target
    return install_bundle(
        compiled_of(bundle, target=effective),
        lock=PathPair(lexical=str(bundle.lock), resolved=str(bundle.lock)),
        target=effective,
        state_dir=state_dir_of(bundle),
        apply=apply,
        replace_unmanaged=replace_unmanaged,
        expect_target_sha256=expect_target_sha256,
        clock=fixed_clock,
        operation_id_factory=fixed_operation_id,
        lock_timeout_seconds=lock_timeout_seconds,
    )


def failed_recovery_case(
    bundle: Bundle, *, existing: bool
) -> tuple[TargetInspection, BackupRecord | None, str, Path]:
    """Create the filesystem state seen immediately after a failed install.

    Args:
        bundle: Disposable bundle whose target and state root to use.
        existing: Whether the target had predecessor bytes.

    Returns:
        The predecessor inspection, optional backup, installed digest, and selected
        receipt path.
    """
    state_dir = state_dir_of(bundle)
    for name in (BACKUPS_DIRNAME, PRESERVED_DIRNAME, RECEIPTS_DIRNAME):
        (state_dir / name).mkdir(parents=True, exist_ok=True)
    backup: BackupRecord | None = None
    if existing:
        write_text_file(bundle.target, UNMANAGED_TEXT)
    before = installation.inspect_target(bundle.target)
    if existing:
        assert before.sha256 is not None
        assert before.size_bytes is not None
        backup_path = state_dir / BACKUPS_DIRNAME / "prior.bak"
        backup_path.write_bytes(bundle.target.read_bytes())
        backup = BackupRecord(
            path=backup_path,
            sha256=before.sha256,
            size_bytes=before.size_bytes,
        )
    bundle.target.write_bytes(FAILED_INSTALL_BYTES)
    installed_sha256 = sha256_file(bundle.target)
    receipt_path = state_dir / RECEIPTS_DIRNAME / "failed-install.json"
    return before, backup, installed_sha256, receipt_path


def recover_failed_install(
    bundle: Bundle,
    before: TargetInspection,
    backup: BackupRecord | None,
    installed_sha256: str,
    receipt_path: Path,
    *,
    receipt_written: bool = False,
) -> str:
    """Call the internal recovery operation with deterministic evidence names.

    Args:
        bundle: Disposable bundle whose target and state root to use.
        before: Target state captured before replacement.
        backup: Optional exact predecessor backup.
        installed_sha256: Digest of the failed operation's target bytes.
        receipt_path: Receipt path selected by the failed operation.
        receipt_written: Whether the receipt was committed before failure.

    Returns:
        Policy-free recovery detail.
    """
    return installation._recover_failed_install(  # pyright: ignore[reportPrivateUsage]
        before=before,
        backup=backup,
        target=bundle.target,
        target_lexical=str(bundle.target),
        state_dir=state_dir_of(bundle),
        installed_sha256=installed_sha256,
        stamp=format_file_stamp(FIXED_MOMENT),
        operation_id=FIXED_OPERATION_ID,
        receipt_path=receipt_path,
        receipt_written=receipt_written,
    )


def receipt_of(bundle: Bundle) -> Path:
    """Locate the single receipt recorded for a bundle.

    Args:
        bundle: The bundle.

    Returns:
        The receipt path.
    """
    receipts = sorted(
        (state_dir_of(bundle) / RECEIPTS_DIRNAME).glob("*-install-*.json")
    )
    assert len(receipts) == 1
    return receipts[0]


def loaded_receipt(bundle: Bundle) -> InstallReceipt:
    """Load and validate the receipt recorded for a bundle.

    Args:
        bundle: The bundle.

    Returns:
        The validated receipt.
    """
    return load_install_receipt(
        receipt_of(bundle),
        state_root=state_dir_of(bundle),
        bundle_id="test-bundle",
        target=bundle.target,
    )


def test_a_dry_run_creates_nothing_at_all(locked_bundle: Bundle) -> None:
    before = sorted(p for p in locked_bundle.root.rglob("*"))
    outcome = install(locked_bundle)
    assert outcome.applied is False
    assert outcome.state is BundleState.MISSING
    assert outcome.backup is None
    assert outcome.installed is None
    assert outcome.receipt_path is None
    assert not locked_bundle.target.exists()
    assert not locked_bundle.state_root.exists(), "no state directory may be created"
    assert sorted(p for p in locked_bundle.root.rglob("*")) == before


def test_a_dry_run_reports_the_complete_plan(locked_bundle: Bundle) -> None:
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)
    plan, inspection = plan_install(
        compiled, target=locked_bundle.target, state_dir=state_dir_of(locked_bundle)
    )
    assert plan.output_sha256 == compiled.rendered.sha256
    assert plan.output_bytes == compiled.rendered.size_bytes
    assert plan.previous_target.state is TargetKind.MISSING
    assert plan.target_mode == 0o600
    assert plan.adoption_required is False
    assert plan.already_current is False
    assert inspection.kind is TargetKind.MISSING


def test_installing_a_new_target(locked_bundle: Bundle) -> None:
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)
    outcome = install(locked_bundle, apply=True)
    assert outcome.applied is True
    assert outcome.state is BundleState.CURRENT
    assert locked_bundle.target.read_bytes() == compiled.rendered.data
    assert outcome.backup is None, "a new target has nothing to back up"
    assert outcome.installed is not None
    assert outcome.installed.sha256 == compiled.rendered.sha256
    assert outcome.completed_at == "2026-08-04T21:30:15Z"


def test_a_new_target_gets_owner_only_permissions(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    assert stat.S_IMODE(locked_bundle.target.stat().st_mode) == 0o600


def test_an_existing_mode_is_preserved(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    locked_bundle.target.chmod(0o644)
    install(locked_bundle, apply=True)
    assert stat.S_IMODE(locked_bundle.target.stat().st_mode) == 0o644


def test_reinstalling_a_managed_target_is_idempotent(locked_bundle: Bundle) -> None:
    first = install(locked_bundle, apply=True)
    digest = locked_bundle.target.read_bytes()
    second = install(locked_bundle, apply=True)
    assert second.state is BundleState.CURRENT
    assert locked_bundle.target.read_bytes() == digest
    assert second.plan.already_current is True
    assert first.plan.already_current is False


def test_an_unmanaged_target_is_refused_without_authorization(
    locked_bundle: Bundle,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    with pytest.raises(UnmanagedTargetError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is UnmanagedTargetProblem.NO_AUTHORIZATION
    assert raised.value.state is BundleState.UNMANAGED_TARGET
    assert locked_bundle.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_adoption_requires_an_expected_digest(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    with pytest.raises(UnmanagedTargetError) as raised:
        install(locked_bundle, apply=True, replace_unmanaged=True)
    assert raised.value.problem is UnmanagedTargetProblem.DIGEST_REQUIRED
    assert locked_bundle.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_adoption_refuses_a_mismatched_digest(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    with pytest.raises(UnmanagedTargetError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256="0" * 64,
        )
    assert raised.value.problem is UnmanagedTargetProblem.DIGEST_MISMATCH
    assert locked_bundle.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_adoption_succeeds_with_a_matching_digest(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=digest,
    )
    assert outcome.state is BundleState.CURRENT
    assert locked_bundle.target.read_bytes() == compiled.rendered.data
    assert outcome.backup is not None
    assert outcome.backup.sha256 == digest
    assert outcome.backup.path.read_text(encoding="utf-8") == UNMANAGED_TEXT
    assert outcome.plan.adoption_required is True


def test_an_expected_digest_also_guards_a_managed_target(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    with pytest.raises(UnmanagedTargetError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256="1" * 64,
        )
    assert raised.value.problem is UnmanagedTargetProblem.DIGEST_MISMATCH


def test_a_future_format_target_is_refused_as_unmanaged(locked_bundle: Bundle) -> None:
    write_text_file(
        locked_bundle.target,
        "# Global Agent Instructions\n\n"
        "<!-- agents-md-compiler:generated format=99 -->\n",
    )
    with pytest.raises(UnmanagedTargetError) as raised:
        install(locked_bundle, apply=True)
    assert "unrecognized generated format" in str(raised.value)


def test_a_symlinked_target_is_refused(locked_bundle: Bundle, tmp_path: Path) -> None:
    real = write_text_file(tmp_path / "real.md", UNMANAGED_TEXT)
    locked_bundle.target.symlink_to(real)
    with pytest.raises(TargetError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is TargetProblem.SYMLINK
    assert real.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_a_shadowing_override_refuses_the_install(locked_bundle: Bundle) -> None:
    global_target = locked_bundle.target.parent / "AGENTS.md"
    (global_target.parent / "AGENTS.override.md").write_text(
        "# Override\n", encoding="utf-8"
    )
    with pytest.raises(ShadowedError) as raised:
        install(locked_bundle, apply=True, target=global_target)
    assert raised.value.state is BundleState.SHADOWED
    assert not global_target.exists()


def test_a_target_that_appears_before_the_lock_is_refused(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_inspect = installation.inspect_target
    calls: list[int] = []

    def _appear(path: Path, *, lexical: str | None = None) -> object:
        calls.append(1)
        if len(calls) == 2:
            write_text_file(path, UNMANAGED_TEXT)
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _appear)
    with pytest.raises(ConcurrentChangeError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_APPEARED


def test_a_target_that_vanishes_before_the_lock_is_refused(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(locked_bundle, apply=True)
    real_inspect = installation.inspect_target
    calls: list[int] = []

    def _vanish(path: Path, *, lexical: str | None = None) -> object:
        calls.append(1)
        if len(calls) == 2:
            path.unlink()
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _vanish)
    with pytest.raises(ConcurrentChangeError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_VANISHED


def test_a_target_that_changes_before_the_lock_is_refused(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    real_inspect = installation.inspect_target
    calls: list[int] = []

    def _change(path: Path, *, lexical: str | None = None) -> object:
        calls.append(1)
        if len(calls) == 2:
            write_text_file(path, "# Changed by someone else\n")
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _change)
    with pytest.raises(ConcurrentChangeError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_CHANGED


def test_a_target_that_becomes_a_symlink_under_the_lock_is_concurrent(
    locked_bundle: Bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    link_target = write_text_file(tmp_path / "link-target.md", "# Other\n")
    real_inspect = installation.inspect_target
    calls: list[int] = []

    def _replace_with_symlink(path: Path, *, lexical: str | None = None) -> object:
        calls.append(1)
        if len(calls) == 2:
            path.unlink()
            path.symlink_to(link_target)
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _replace_with_symlink)
    with pytest.raises(ConcurrentChangeError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )

    assert raised.value.problem is ConcurrentChangeProblem.TARGET_BECAME_SYMLINK
    assert raised.value.state is BundleState.CONCURRENT_CHANGE
    assert locked_bundle.target.is_symlink()
    assert link_target.read_text(encoding="utf-8") == "# Other\n"


def test_a_target_that_becomes_a_directory_under_the_lock_is_unusable(
    locked_bundle: Bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    real_inspect = installation.inspect_target
    calls: list[int] = []

    def _replace_with_directory(path: Path, *, lexical: str | None = None) -> object:
        calls.append(1)
        if len(calls) == 2:
            path.unlink()
            path.mkdir()
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _replace_with_directory)
    with pytest.raises(TargetError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )

    assert raised.value.problem is TargetProblem.NOT_A_FILE
    assert locked_bundle.target.is_dir()


def test_an_existing_backup_with_matching_content_is_reused(
    locked_bundle: Bundle,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    backups = state_dir_of(locked_bundle) / BACKUPS_DIRNAME
    backups.mkdir(parents=True)
    existing = backups / f"{format_file_stamp(FIXED_MOMENT)}.{digest}.bak"
    existing.write_text(UNMANAGED_TEXT, encoding="utf-8")
    outcome = install(
        locked_bundle, apply=True, replace_unmanaged=True, expect_target_sha256=digest
    )
    assert outcome.backup is not None
    assert outcome.backup.path == existing


def test_an_existing_backup_with_different_content_is_refused(
    locked_bundle: Bundle,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    backups = state_dir_of(locked_bundle) / BACKUPS_DIRNAME
    backups.mkdir(parents=True)
    collided = backups / f"{format_file_stamp(FIXED_MOMENT)}.{digest}.bak"
    collided.write_text("# Different content under the same name\n", encoding="utf-8")
    with pytest.raises(ConcurrentChangeError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )
    assert raised.value.problem is ConcurrentChangeProblem.BACKUP_EXISTS_DIFFERENT
    assert locked_bundle.target.read_text(encoding="utf-8") == UNMANAGED_TEXT


def test_an_unreadable_target_during_backup_is_reported(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    real_read = Path.read_bytes
    # The target is read three times: inspected before the lock, inspected again
    # under it, then read for the backup. Only the backup read may fail here, or the
    # adoption digest check would refuse first and never reach the backup step.
    reads: list[int] = []

    def _refuse(self: Path) -> bytes:
        if self == locked_bundle.target:
            reads.append(1)
            if len(reads) >= 3:
                message = "Permission denied"
                raise PermissionError(13, message)
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _refuse)
    with pytest.raises(MutationError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )
    assert raised.value.problem is MutationProblem.BACKUP_FAILED


def test_a_target_changed_between_hash_and_backup_read_is_refused(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    digest = sha256_file(locked_bundle.target)
    real_read = Path.read_bytes
    reads: list[int] = []

    def _swap(self: Path) -> bytes:
        if self == locked_bundle.target:
            reads.append(1)
            if len(reads) >= 3:
                return b"# Swapped between hash and backup read\n"
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _swap)
    with pytest.raises(ConcurrentChangeError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_CHANGED


def test_a_failed_postcondition_is_reported(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)

    def _wrong_digest(_path: Path) -> str:
        return "9" * 64

    monkeypatch.setattr(installation, "sha256_file", _wrong_digest)
    with pytest.raises(MutationError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is MutationProblem.POSTCONDITION_FAILED
    assert "prior absence restored" in str(raised.value)
    assert not locked_bundle.target.exists()
    preserved = list(
        (state_dir_of(locked_bundle) / PRESERVED_DIRNAME).glob("*.generated")
    )
    assert len(preserved) == 1
    assert sha256_file(preserved[0]) == compiled.rendered.sha256


def test_an_unreadable_target_during_the_postcondition_is_reported(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(_path: Path) -> str:
        message = "Input/output error"
        raise OSError(5, message)

    monkeypatch.setattr(installation, "sha256_file", _refuse)
    with pytest.raises(MutationError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is MutationProblem.POSTCONDITION_FAILED
    assert not locked_bundle.target.exists()


def test_a_failed_postcondition_restores_an_existing_target(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    original = locked_bundle.target.read_bytes()
    digest = sha256_file(locked_bundle.target)

    def _wrong_digest(_path: Path) -> str:
        return "9" * 64

    monkeypatch.setattr(installation, "sha256_file", _wrong_digest)
    with pytest.raises(MutationError) as raised:
        install(
            locked_bundle,
            apply=True,
            replace_unmanaged=True,
            expect_target_sha256=digest,
        )
    assert raised.value.problem is MutationProblem.POSTCONDITION_FAILED
    assert "prior target restored" in str(raised.value)
    assert locked_bundle.target.read_bytes() == original


def test_a_failed_receipt_write_restores_the_prior_absence(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_receipt(_path: Path, _payload: dict[str, Any]) -> str:
        raise MutationError(MutationProblem.WRITE_FAILED, path=_path)

    monkeypatch.setattr(installation, "write_receipt", _fail_receipt)
    with pytest.raises(MutationError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is MutationProblem.WRITE_FAILED
    assert "prior absence restored" in str(raised.value)
    assert not locked_bundle.target.exists()
    assert not list((state_dir_of(locked_bundle) / RECEIPTS_DIRNAME).glob("*.json"))


def test_a_failed_last_installed_write_restores_and_archives_the_receipt(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write_receipt = installation.write_receipt
    calls = 0

    def _fail_second(path: Path, payload: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MutationError(MutationProblem.WRITE_FAILED, path=path)
        return real_write_receipt(path, payload)

    monkeypatch.setattr(installation, "write_receipt", _fail_second)
    with pytest.raises(MutationError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is MutationProblem.WRITE_FAILED
    assert "failed receipt preserved" in str(raised.value)
    assert not locked_bundle.target.exists()
    assert not list((state_dir_of(locked_bundle) / RECEIPTS_DIRNAME).glob("*.json"))
    archived = list((state_dir_of(locked_bundle) / PRESERVED_DIRNAME).glob("*.failed"))
    assert len(archived) == 1


def test_a_failed_install_reports_when_recovery_cannot_be_proven(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _wrong_digest(_path: Path) -> str:
        return "9" * 64

    def _fail_recovery(**_kwargs: object) -> str:
        raise MutationError(MutationProblem.RECOVERY_FAILED, path=locked_bundle.target)

    monkeypatch.setattr(installation, "sha256_file", _wrong_digest)
    monkeypatch.setattr(installation, "_recover_failed_install", _fail_recovery)
    with pytest.raises(MutationError) as raised:
        install(locked_bundle, apply=True)
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED
    assert "install failure" in str(raised.value)


def test_failed_install_recovery_refuses_a_changed_target(
    locked_bundle: Bundle,
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )
    locked_bundle.target.write_bytes(b"later external bytes\n")
    with pytest.raises(ConcurrentChangeError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_CHANGED


@pytest.mark.parametrize("dangling_symlink", [False, True])
def test_failed_install_recovery_refuses_a_colliding_evidence_path(
    locked_bundle: Bundle, dangling_symlink: bool
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )
    preserved = (
        state_dir_of(locked_bundle)
        / PRESERVED_DIRNAME
        / (
            f"{format_file_stamp(FIXED_MOMENT)}-failed-install-"
            f"{FIXED_OPERATION_ID}.{installed_sha256}.generated"
        )
    )
    if dangling_symlink:
        preserved.symlink_to(preserved.parent / "absent")
    else:
        preserved.write_bytes(b"existing evidence\n")
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_reports_a_preserve_failure(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )

    def _refuse_move(_source: str, _destination: str) -> str:
        message = "Read-only file system"
        raise OSError(30, message)

    monkeypatch.setattr(installation.shutil, "move", _refuse_move)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_verifies_preserved_evidence(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )

    def _wrong_digest(_data: bytes) -> str:
        return "0" * 64

    monkeypatch.setattr(installation, "sha256_bytes", _wrong_digest)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED
    assert "preserved evidence" in str(raised.value)


def test_failed_install_recovery_verifies_the_missing_postcondition(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )
    real_inspect = installation.inspect_target
    installed = real_inspect(locked_bundle.target)
    calls = 0

    def _stale_inspection(
        path: Path, *, lexical: str | None = None
    ) -> TargetInspection:
        nonlocal calls
        calls += 1
        if calls == 2:
            return installed
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _stale_inspection)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED
    assert "missing state" in str(raised.value)


def test_failed_install_recovery_requires_an_existing_target_backup(
    locked_bundle: Bundle,
) -> None:
    before, _backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, None, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_requires_a_predecessor_digest(
    locked_bundle: Bundle,
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    without_digest = replace(before, sha256=None)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, without_digest, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_refuses_a_symlinked_backup(
    locked_bundle: Bundle,
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    assert backup is not None
    backup.path.unlink()
    backup.path.symlink_to(locked_bundle.modules["core"])
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_reports_an_unreadable_backup(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    assert backup is not None
    real_read = Path.read_bytes

    def _refuse_read(self: Path) -> bytes:
        if self == backup.path:
            message = "Input/output error"
            raise OSError(5, message)
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _refuse_read)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_rejects_a_mismatched_backup_record(
    locked_bundle: Bundle,
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    assert backup is not None
    mismatched = replace(backup, sha256="0" * 64)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, mismatched, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_rejects_changed_backup_bytes(
    locked_bundle: Bundle,
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    assert backup is not None
    backup.path.write_bytes(b"changed backup bytes\n")
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_uses_owner_only_mode_without_a_recorded_mode(
    locked_bundle: Bundle,
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    without_mode = replace(before, mode=None)
    detail = recover_failed_install(
        locked_bundle, without_mode, backup, installed_sha256, receipt_path
    )
    assert "prior target restored" in detail
    assert stat.S_IMODE(locked_bundle.target.stat().st_mode) == 0o600


def test_failed_install_recovery_verifies_restored_bytes(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=True
    )
    real_inspect = installation.inspect_target
    calls = 0

    def _wrong_restored(path: Path, *, lexical: str | None = None) -> TargetInspection:
        nonlocal calls
        calls += 1
        inspected = real_inspect(path, lexical=lexical)
        if calls == 2:
            return replace(inspected, sha256="0" * 64)
        return inspected

    monkeypatch.setattr(installation, "inspect_target", _wrong_restored)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle, before, backup, installed_sha256, receipt_path
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED
    assert "restored target" in str(raised.value)


@pytest.mark.parametrize("dangling_symlink", [False, True])
def test_failed_install_recovery_refuses_a_colliding_receipt_archive(
    locked_bundle: Bundle, dangling_symlink: bool
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )
    receipt_path.write_text("{}\n", encoding="utf-8")
    archived = (
        state_dir_of(locked_bundle)
        / PRESERVED_DIRNAME
        / (f"{receipt_path.name}.failed")
    )
    if dangling_symlink:
        archived.symlink_to(archived.parent / "absent")
    else:
        archived.write_bytes(b"existing archive\n")
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle,
            before,
            backup,
            installed_sha256,
            receipt_path,
            receipt_written=True,
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_failed_install_recovery_reports_a_receipt_archive_failure(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, backup, installed_sha256, receipt_path = failed_recovery_case(
        locked_bundle, existing=False
    )
    receipt_path.write_text("{}\n", encoding="utf-8")
    real_replace = Path.replace

    def _refuse_replace(self: Path, target: Path) -> Path:
        if self == receipt_path:
            message = "Read-only file system"
            raise OSError(30, message)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _refuse_replace)
    with pytest.raises(MutationError) as raised:
        recover_failed_install(
            locked_bundle,
            before,
            backup,
            installed_sha256,
            receipt_path,
            receipt_written=True,
        )
    assert raised.value.problem is MutationProblem.RECOVERY_FAILED


def test_the_receipt_records_the_full_operation(locked_bundle: Bundle) -> None:
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)
    outcome = install(locked_bundle, apply=True)
    assert outcome.receipt_path is not None
    document = json.loads(outcome.receipt_path.read_text(encoding="utf-8"))
    assert document["receipt_schema_version"] == 1
    assert document["operation"] == "install"
    assert document["operation_id"] == FIXED_OPERATION_ID
    assert document["bundle_id"] == "test-bundle"
    assert document["manifest_sha256"] == compiled.manifest.sha256
    assert document["lock_sha256"] == compiled.lock_sha256
    assert [m["id"] for m in document["modules"]] == ["core", "python"]
    assert document["previous_target"]["state"] == "MISSING"
    assert document["backup"] is None
    assert document["installed"]["sha256"] == compiled.rendered.sha256
    assert document["installed"]["mode"] == "0600"
    assert document["completed_at"] == "2026-08-04T21:30:15Z"
    assert document["runtime_verification"] is None


def test_the_receipt_contains_no_policy_content(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    text = receipt_of(locked_bundle).read_text(encoding="utf-8")
    for phrase in ("Core Policy", "Python Policy", "smallest correct change"):
        assert phrase not in text


def test_the_receipt_and_backup_are_owner_only(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    assert outcome.receipt_path is not None
    assert outcome.backup is not None
    assert stat.S_IMODE(outcome.receipt_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(outcome.backup.path.stat().st_mode) == 0o600


def test_the_last_installed_digest_is_recorded(locked_bundle: Bundle) -> None:
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)
    install(locked_bundle, apply=True)
    document = json.loads(
        (state_dir_of(locked_bundle) / LAST_INSTALLED_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert document["installed_sha256"] == compiled.rendered.sha256
    # The recorded name leads with the UTC stamp so receipts sort chronologically.
    assert document["receipt"] == receipt_name(
        "install", format_file_stamp(FIXED_MOMENT), FIXED_OPERATION_ID
    )


def test_state_directories_are_owner_only(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    for name in (RECEIPTS_DIRNAME, BACKUPS_DIRNAME, PRESERVED_DIRNAME):
        directory = state_dir_of(locked_bundle) / name
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_a_lock_timeout_is_reported(locked_bundle: Bundle) -> None:
    locks = state_dir_of(locked_bundle) / LOCKS_DIRNAME
    locks.mkdir(parents=True)
    held = lock_path_for(locked_bundle.target, lock_dir=locks)
    held.write_text(json.dumps({"pid": 99, "acquired_at": "held"}), encoding="utf-8")
    with pytest.raises(MutationError) as raised:
        install(locked_bundle, apply=True, lock_timeout_seconds=0.0)
    assert raised.value.problem is MutationProblem.LOCK_UNAVAILABLE
    assert not locked_bundle.target.exists()


def test_rolling_back_a_replaced_target_restores_exact_bytes(
    locked_bundle: Bundle,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    original = locked_bundle.target.read_bytes()
    install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    receipt = loaded_receipt(locked_bundle)
    outcome = rollback_install(
        receipt,
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=True,
        clock=fixed_clock,
        operation_id_factory=lambda: "f" * 32,
    )
    assert outcome.applied is True
    assert locked_bundle.target.read_bytes() == original
    assert outcome.restored is not None
    assert outcome.preserved_path is None
    assert outcome.receipt_path is not None
    document = json.loads(outcome.receipt_path.read_text(encoding="utf-8"))
    assert document["operation"] == "rollback"
    assert document["source_receipt"]["operation_id"] == FIXED_OPERATION_ID
    assert document["preserved_path"] is None


def test_a_rollback_dry_run_changes_nothing(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    installed = locked_bundle.target.read_bytes()
    receipt = loaded_receipt(locked_bundle)
    outcome = rollback_install(
        receipt,
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=False,
        clock=fixed_clock,
    )
    assert outcome.applied is False
    assert outcome.receipt_path is None
    assert locked_bundle.target.read_bytes() == installed


def test_rolling_back_a_created_target_preserves_it(locked_bundle: Bundle) -> None:
    compiled = compiled_of(locked_bundle, target=locked_bundle.target)
    install(locked_bundle, apply=True)
    receipt = loaded_receipt(locked_bundle)
    outcome = rollback_install(
        receipt,
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=True,
        clock=fixed_clock,
        operation_id_factory=lambda: "e" * 32,
    )
    assert outcome.state is BundleState.MISSING
    assert not locked_bundle.target.exists()
    assert outcome.preserved_path is not None
    assert outcome.preserved_path.read_bytes() == compiled.rendered.data
    assert stat.S_IMODE(outcome.preserved_path.stat().st_mode) == 0o600
    assert outcome.preserved_path.parent.name == PRESERVED_DIRNAME


def test_rollback_refuses_after_the_target_changed(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    receipt = loaded_receipt(locked_bundle)
    locked_bundle.target.write_text(
        "# Edited by hand after install\n", encoding="utf-8"
    )
    with pytest.raises(ConcurrentChangeError) as raised:
        rollback_install(
            receipt,
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_CHANGED
    assert (
        locked_bundle.target.read_text(encoding="utf-8")
        == "# Edited by hand after install\n"
    )


def test_rollback_refuses_a_missing_backup(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    assert outcome.backup is not None
    outcome.backup.path.unlink()
    with pytest.raises(ReceiptError) as raised:
        rollback_install(
            loaded_receipt(locked_bundle),
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is ReceiptProblem.BACKUP_MISSING


def test_rollback_refuses_a_corrupted_backup(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    assert outcome.backup is not None
    outcome.backup.path.write_text("# Corrupted backup\n", encoding="utf-8")
    with pytest.raises(ReceiptError) as raised:
        rollback_install(
            loaded_receipt(locked_bundle),
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is ReceiptProblem.BACKUP_DIGEST_MISMATCH


def test_rollback_refuses_a_symlinked_backup(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    assert outcome.backup is not None
    content = outcome.backup.path.read_bytes()
    outcome.backup.path.unlink()
    elsewhere = locked_bundle.root / "elsewhere.bak"
    elsewhere.write_bytes(content)
    outcome.backup.path.symlink_to(elsewhere)
    with pytest.raises(ReceiptError) as raised:
        rollback_install(
            loaded_receipt(locked_bundle),
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is ReceiptProblem.BACKUP_MISSING


def test_rollback_reports_an_unreadable_backup(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    assert outcome.backup is not None
    receipt = loaded_receipt(locked_bundle)

    def _refuse(_path: Path) -> str:
        message = "Input/output error"
        raise OSError(5, message)

    monkeypatch.setattr(installation, "sha256_file", _refuse)
    with pytest.raises(ReceiptError) as raised:
        rollback_install(
            receipt,
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is ReceiptProblem.BACKUP_MISSING


def test_rollback_reports_a_failed_backup_read(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    outcome = install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    assert outcome.backup is not None
    receipt = loaded_receipt(locked_bundle)
    backup_path = outcome.backup.path
    real_read = Path.read_bytes

    def _refuse(self: Path) -> bytes:
        if self == backup_path:
            message = "Input/output error"
            raise OSError(5, message)
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _refuse)
    with pytest.raises(MutationError) as raised:
        rollback_install(
            receipt,
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is MutationProblem.BACKUP_FAILED


def test_rollback_reports_a_failed_preserve(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(locked_bundle, apply=True)
    receipt = loaded_receipt(locked_bundle)

    def _refuse(source: str, destination: str) -> str:
        message = "Cross-device link"
        raise OSError(18, message)

    monkeypatch.setattr(installation.shutil, "move", _refuse)
    with pytest.raises(MutationError) as raised:
        rollback_install(
            receipt,
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is MutationProblem.REPLACE_FAILED


def test_rollback_restores_the_recorded_mode(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    locked_bundle.target.chmod(0o640)
    install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    rollback_install(
        loaded_receipt(locked_bundle),
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=True,
        clock=fixed_clock,
        operation_id_factory=lambda: "d" * 32,
    )
    assert stat.S_IMODE(locked_bundle.target.stat().st_mode) == 0o640


def test_rollback_falls_back_to_owner_only_when_no_mode_was_recorded(
    locked_bundle: Bundle,
) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    install(
        locked_bundle,
        apply=True,
        replace_unmanaged=True,
        expect_target_sha256=sha256_file(locked_bundle.target),
    )
    receipt = loaded_receipt(locked_bundle)
    stripped = replace(
        receipt,
        previous_target=replace(receipt.previous_target, mode=None),  # pyright: ignore[reportAttributeAccessIssue]
    )
    rollback_install(
        stripped,
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=True,
        clock=fixed_clock,
        operation_id_factory=lambda: "c" * 32,
    )
    assert stat.S_IMODE(locked_bundle.target.stat().st_mode) == 0o600


def test_rollback_refuses_when_the_target_changes_under_the_lock(
    locked_bundle: Bundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(locked_bundle, apply=True)
    receipt = loaded_receipt(locked_bundle)
    real_inspect = installation.inspect_target
    calls: list[int] = []

    def _change(path: Path, *, lexical: str | None = None) -> object:
        calls.append(1)
        if len(calls) == 2:
            path.write_text("# Changed under the lock\n", encoding="utf-8")
        return real_inspect(path, lexical=lexical)

    monkeypatch.setattr(installation, "inspect_target", _change)
    with pytest.raises(ConcurrentChangeError) as raised:
        rollback_install(
            receipt,
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
        )
    assert raised.value.problem is ConcurrentChangeProblem.TARGET_CHANGED


def test_the_operation_id_and_clock_default_to_real_values(
    locked_bundle: Bundle,
) -> None:
    outcome = install_bundle(
        compiled_of(locked_bundle, target=locked_bundle.target),
        lock=PathPair(
            lexical=str(locked_bundle.lock), resolved=str(locked_bundle.lock)
        ),
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=True,
    )
    assert outcome.completed_at is not None
    assert outcome.completed_at.endswith("Z")
    assert len(outcome.operation_id) == 32
    assert int(outcome.operation_id, 16) >= 0


def test_the_rollback_operation_id_defaults_to_a_real_value(
    locked_bundle: Bundle,
) -> None:
    install(locked_bundle, apply=True)
    outcome = rollback_install(
        loaded_receipt(locked_bundle),
        target=locked_bundle.target,
        state_dir=state_dir_of(locked_bundle),
        apply=True,
    )
    assert len(outcome.operation_id) == 32
    assert outcome.completed_at is not None


def test_a_rollback_lock_timeout_is_reported(locked_bundle: Bundle) -> None:
    install(locked_bundle, apply=True)
    receipt = loaded_receipt(locked_bundle)
    locks = state_dir_of(locked_bundle) / LOCKS_DIRNAME
    held = lock_path_for(locked_bundle.target, lock_dir=locks)
    held.write_text(json.dumps({"pid": 5, "acquired_at": "held"}), encoding="utf-8")
    with pytest.raises(MutationError) as raised:
        rollback_install(
            receipt,
            target=locked_bundle.target,
            state_dir=state_dir_of(locked_bundle),
            apply=True,
            clock=fixed_clock,
            lock_timeout_seconds=0.0,
        )
    assert raised.value.problem is MutationProblem.LOCK_UNAVAILABLE


def test_an_interrupted_install_leaves_complete_bytes(locked_bundle: Bundle) -> None:
    write_text_file(locked_bundle.target, UNMANAGED_TEXT)
    original = locked_bundle.target.read_bytes()
    real_replace = os.replace

    def _interrupt(source: object, destination: object) -> None:
        message = "Interrupted system call"
        raise OSError(4, message)

    try:
        os.replace = _interrupt  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(MutationError):
            install(
                locked_bundle,
                apply=True,
                replace_unmanaged=True,
                expect_target_sha256=sha256_file(locked_bundle.target),
            )
    finally:
        os.replace = real_replace  # pyright: ignore[reportAttributeAccessIssue]
    assert locked_bundle.target.read_bytes() == original
    assert not list(locked_bundle.target.parent.glob(".agents-md-compiler-*"))

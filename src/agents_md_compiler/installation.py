"""The explicit mutation boundary: install and rollback.

This is the only module that replaces a file. It accepts already validated rendered
bytes plus an explicit target precondition, and it refuses rather than guesses.

Order of operations for an install, and every step is load-bearing:

1. Refuse a non-empty sibling override, which would make the file invisible.
2. Inspect the target without following a link, and capture its digest.
3. Require explicit adoption for a target this tool did not generate.
4. Compute the complete plan. A dry run stops here having created nothing at all.
5. Create deployment and shared-lock directories, then take a per-target lock.
6. Recapture the target under the lock and refuse any change since step 2.
7. Back up the prior bytes immutably.
8. Write the new bytes atomically, preserving an existing permission mode.
9. Re-read the target and prove it matches what was rendered.
10. Record the receipt and the last-installed digest only after that proof.
11. If a step after replacement fails, restore the prior target under the same lock.
"""

import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agents_md_compiler._version import distribution_version
from agents_md_compiler.atomic import atomic_write, create_state_directory
from agents_md_compiler.errors import (
    CompilerError,
    ConcurrentChangeError,
    ConcurrentChangeProblem,
    MutationError,
    MutationProblem,
    ReceiptError,
    ReceiptProblem,
    TargetError,
    TargetProblem,
    UnmanagedTargetError,
    UnmanagedTargetProblem,
)
from agents_md_compiler.hashing import sha256_bytes, sha256_file
from agents_md_compiler.locking import (
    DEFAULT_TIMEOUT_SECONDS,
    advisory_lock,
    lock_path_for,
)
from agents_md_compiler.models import (
    NEW_TARGET_MODE,
    STATE_DIR_MODE,
    STATE_FILE_MODE,
    BackupRecord,
    BundleState,
    CompiledBundle,
    InstallOutcome,
    InstallPlan,
    InstallReceipt,
    ModuleDigest,
    PathPair,
    PreviousTargetRecord,
    RollbackOutcome,
    SourceReceiptRef,
    TargetInspection,
    TargetKind,
    WrittenRecord,
)
from agents_md_compiler.receipts import (
    BACKUPS_DIRNAME,
    INSTALL_OPERATION,
    LAST_INSTALLED_FILENAME,
    PRESERVED_DIRNAME,
    RECEIPTS_DIRNAME,
    ROLLBACK_OPERATION,
    build_install_payload,
    build_rollback_payload,
    receipt_name,
    write_receipt,
)
from agents_md_compiler.state import (
    inspect_override,
    inspect_target,
    require_not_shadowed,
    require_target_parent,
)

Clock = Callable[[], datetime]
"""Injectable wall clock. Receipts carry timestamps, so tests pin it."""

IdFactory = Callable[[], str]
"""Injectable operation-identifier factory, so receipt names are deterministic in tests."""


def _default_clock() -> datetime:
    """Read the current UTC time.

    Returns:
        A timezone-aware UTC timestamp.
    """
    return datetime.now(UTC)


def _default_operation_id() -> str:
    """Mint an operation identifier.

    Returns:
        A 32-character lowercase hexadecimal identifier.
    """
    return uuid.uuid4().hex


def format_timestamp(moment: datetime) -> str:
    """Render a receipt timestamp.

    Args:
        moment: Timezone-aware moment.

    Returns:
        UTC time at second precision with a Zulu suffix.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_file_stamp(moment: datetime) -> str:
    """Render the compact UTC stamp used in backup and receipt file names.

    Microsecond precision is load-bearing, not decoration. Receipt order is derived
    from these names without opening a single file, so the stamp is the only thing
    that distinguishes two mutations of one bundle. At second precision two installs
    a few hundred milliseconds apart produced equal stamps, and the tie fell through
    to the random operation id, which made "the latest receipt" a coin flip.

    Args:
        moment: Timezone-aware moment.

    Returns:
        UTC time at microsecond precision, safe for a file name on every platform.
    """
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _previous_record(inspection: TargetInspection) -> PreviousTargetRecord:
    """Summarize an inspection for a receipt.

    Args:
        inspection: Target inspection.

    Returns:
        The prior-state record.
    """
    return PreviousTargetRecord(
        state=inspection.kind,
        sha256=inspection.sha256,
        size_bytes=inspection.size_bytes,
        mode=inspection.mode,
    )


def _module_digests(compiled: CompiledBundle) -> tuple[ModuleDigest, ...]:
    """Reduce locked modules to receipt identity.

    Args:
        compiled: The compiled bundle.

    Returns:
        Module identity in manifest order.
    """
    return tuple(
        ModuleDigest(id=module.id, sha256=module.sha256, size_bytes=module.size_bytes)
        for module in compiled.lock.modules
    )


def _check_adoption(
    inspection: TargetInspection,
    *,
    replace_unmanaged: bool,
    expect_target_sha256: str | None,
) -> None:
    """Enforce the adoption rules for an existing target.

    Args:
        inspection: Target inspection captured before the mutation.
        replace_unmanaged: Whether the operator authorized replacing an unmanaged file.
        expect_target_sha256: Digest the operator captured before the dry run.

    Raises:
        UnmanagedTargetError: The target is unmanaged and adoption was not authorized,
            or the supplied digest does not match the current bytes.
    """
    if inspection.kind is TargetKind.UNMANAGED:
        if not replace_unmanaged:
            raise UnmanagedTargetError(
                UnmanagedTargetProblem.NO_AUTHORIZATION,
                target=inspection.path,
                detail=(
                    "declares an unrecognized generated format"
                    if inspection.declared_format is not None
                    else "no generated header"
                ),
            )
        if expect_target_sha256 is None:
            raise UnmanagedTargetError(
                UnmanagedTargetProblem.DIGEST_REQUIRED, target=inspection.path
            )
    if expect_target_sha256 is not None and expect_target_sha256 != inspection.sha256:
        raise UnmanagedTargetError(
            UnmanagedTargetProblem.DIGEST_MISMATCH,
            target=inspection.path,
            detail=f"expected {expect_target_sha256}, observed {inspection.sha256}",
        )


def _refuse_if_changed(
    before: TargetInspection,
    target: Path,
    *,
    lexical: str | None = None,
) -> TargetInspection:
    """Recapture and refuse a target changed between the plan and the lock.

    Args:
        before: Inspection captured before the lock.
        target: Resolved target path.
        lexical: Target path as supplied.

    Returns:
        The target inspection captured under the lock.

    Raises:
        ConcurrentChangeError: The target appeared, vanished, became a symlink, or
            changed content.
        TargetError: The target became unusable for another reason.
    """
    try:
        after = inspect_target(target, lexical=lexical)
    except TargetError as error:
        if error.problem is TargetProblem.SYMLINK:
            raise ConcurrentChangeError(
                ConcurrentChangeProblem.TARGET_BECAME_SYMLINK, path=target
            ) from error
        raise
    if before.kind is TargetKind.MISSING and after.kind is not TargetKind.MISSING:
        raise ConcurrentChangeError(
            ConcurrentChangeProblem.TARGET_APPEARED, path=target
        )
    if before.kind is not TargetKind.MISSING and after.kind is TargetKind.MISSING:
        raise ConcurrentChangeError(
            ConcurrentChangeProblem.TARGET_VANISHED, path=target
        )
    if before.sha256 != after.sha256:
        raise ConcurrentChangeError(
            ConcurrentChangeProblem.TARGET_CHANGED,
            path=target,
            detail=f"expected {before.sha256}, observed {after.sha256}",
        )
    return after


def _write_backup(
    inspection: TargetInspection, *, backups_dir: Path, stamp: str
) -> BackupRecord:
    """Copy the prior target bytes into an immutable backup.

    A backup name carries the UTC stamp and the pre-change digest, so re-running an
    install in the same second with identical prior content maps to the same name.
    That name is reused only when its content already matches; different content
    under the same name is a refusal rather than an overwrite.

    Args:
        inspection: Target inspection describing the bytes to preserve.
        backups_dir: Directory that holds backups.
        stamp: Compact UTC stamp for the file name.

    Returns:
        The backup record.

    Raises:
        ConcurrentChangeError: A backup already exists at that name with different
            content.
        MutationError: The backup could not be written.
    """
    digest = inspection.sha256
    size = inspection.size_bytes
    if (
        digest is None or size is None
    ):  # pragma: no cover - callers pass an existing target
        message = "backup requested for a target that does not exist"
        raise MutationError(
            MutationProblem.BACKUP_FAILED, path=inspection.path, detail=message
        )
    destination = backups_dir / f"{stamp}.{digest}.bak"
    if destination.exists():
        if sha256_file(destination) != digest:
            raise ConcurrentChangeError(
                ConcurrentChangeProblem.BACKUP_EXISTS_DIFFERENT, path=destination
            )
        return BackupRecord(path=destination, sha256=digest, size_bytes=size)
    try:
        data = inspection.path.read_bytes()
    except OSError as error:
        raise MutationError(
            MutationProblem.BACKUP_FAILED,
            path=inspection.path,
            detail=error.strerror or type(error).__name__,
        ) from error
    if sha256_bytes(data) != digest:
        raise ConcurrentChangeError(
            ConcurrentChangeProblem.TARGET_CHANGED,
            path=inspection.path,
            detail="target changed while the backup was being read",
        )
    atomic_write(destination, data, mode=STATE_FILE_MODE)
    return BackupRecord(path=destination, sha256=digest, size_bytes=size)


def _verify_postcondition(target: Path, expected: str) -> None:
    """Prove the installed bytes are the bytes that were rendered.

    Args:
        target: Resolved target path.
        expected: Digest the render produced.

    Raises:
        MutationError: The target is unreadable or does not match.
    """
    try:
        observed = sha256_file(target)
    except OSError as error:
        raise MutationError(
            MutationProblem.POSTCONDITION_FAILED,
            path=target,
            detail=error.strerror or type(error).__name__,
        ) from error
    if observed != expected:
        raise MutationError(
            MutationProblem.POSTCONDITION_FAILED,
            path=target,
            detail=f"expected {expected}, observed {observed}",
        )


def _recover_failed_install(
    *,
    before: TargetInspection,
    backup: BackupRecord | None,
    target: Path,
    target_lexical: str | None,
    state_dir: Path,
    installed_sha256: str,
    stamp: str,
    operation_id: str,
    receipt_path: Path,
    receipt_written: bool,
) -> str:
    """Recover the exact target state captured before a failed install.

    Recovery runs while the install's per-target advisory lock remains held. It
    first proves that the target still contains the bytes this operation installed,
    so it never overwrites a later external change. An existing predecessor is
    restored from its verified backup. A predecessor that was missing is restored
    to absence by preserving the generated bytes under the private state root.

    Args:
        before: Target state captured under the lock before replacement.
        backup: Immutable backup for an existing predecessor.
        target: Resolved installation target.
        target_lexical: Target path as supplied.
        state_dir: Target-qualified deployment state directory.
        installed_sha256: Digest of the bytes this install wrote.
        stamp: Operation timestamp used for evidence names.
        operation_id: Operation identifier used for evidence names.
        receipt_path: Receipt path selected for the failed install.
        receipt_written: Whether the install receipt was committed before failure.

    Returns:
        Policy-free recovery detail for the surfaced installation error.

    Raises:
        ConcurrentChangeError: The target no longer contains this operation's bytes.
        MutationError: Exact recovery could not be completed and verified.
    """
    current = inspect_target(target, lexical=target_lexical)
    if current.sha256 != installed_sha256:
        raise ConcurrentChangeError(
            ConcurrentChangeProblem.TARGET_CHANGED,
            path=target,
            detail=(
                "failed-install recovery expected "
                f"{installed_sha256}, observed {current.sha256}"
            ),
        )

    if before.kind is TargetKind.MISSING:
        preserved = (
            state_dir
            / PRESERVED_DIRNAME
            / f"{stamp}-failed-install-{operation_id}.{installed_sha256}.generated"
        )
        if preserved.exists() or preserved.is_symlink():
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=f"evidence path already exists: {preserved}",
            )
        try:
            shutil.move(str(target), str(preserved))
            preserved.chmod(STATE_FILE_MODE)
            preserved_data = preserved.read_bytes()
        except OSError as error:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
        observed = sha256_bytes(preserved_data)
        if observed != installed_sha256:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=(
                    f"preserved evidence expected {installed_sha256}, "
                    f"observed {observed}"
                ),
            )
        restored = inspect_target(target, lexical=target_lexical)
        if restored.kind is not TargetKind.MISSING:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail="target did not return to its missing state",
            )
        recovery_detail = (
            f"prior absence restored; generated bytes preserved at {preserved}"
        )
    else:
        if backup is None or before.sha256 is None:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail="existing predecessor has no usable backup",
            )
        if backup.path.is_symlink():
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=f"backup became a symlink: {backup.path}",
            )
        try:
            prior_data = backup.path.read_bytes()
        except OSError as error:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
        observed_backup = sha256_bytes(prior_data)
        if backup.sha256 != before.sha256 or observed_backup != before.sha256:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=(f"backup expected {before.sha256}, observed {observed_backup}"),
            )
        mode = NEW_TARGET_MODE if before.mode is None else before.mode
        atomic_write(target, prior_data, mode=mode)
        restored = inspect_target(target, lexical=target_lexical)
        if restored.sha256 != before.sha256:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=(
                    f"restored target expected {before.sha256}, "
                    f"observed {restored.sha256}"
                ),
            )
        recovery_detail = f"prior target restored from {backup.path}"

    if receipt_written:
        archived_receipt = state_dir / PRESERVED_DIRNAME / f"{receipt_path.name}.failed"
        if archived_receipt.exists() or archived_receipt.is_symlink():
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=f"failed receipt evidence already exists: {archived_receipt}",
            )
        try:
            receipt_path.replace(archived_receipt)
        except OSError as error:
            raise MutationError(
                MutationProblem.RECOVERY_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
        recovery_detail += f"; failed receipt preserved at {archived_receipt}"
    return recovery_detail


def plan_install(
    compiled: CompiledBundle,
    *,
    target: Path,
    target_lexical: str | None = None,
    state_dir: Path,
    replace_unmanaged: bool = False,
    expect_target_sha256: str | None = None,
) -> tuple[InstallPlan, TargetInspection]:
    """Compute what an install would do, without creating anything.

    Propagated failures: :class:`ShadowedError` when a non-empty sibling override
    would shadow the target, :class:`UnmanagedTargetError` when the adoption rules
    are not satisfied, and :class:`TargetError` for an unusable target path.

    Args:
        compiled: The compiled bundle to install.
        target: Resolved target path.
        target_lexical: Target path as supplied.
        state_dir: Target-qualified deployment state directory.
        replace_unmanaged: Whether replacing an unmanaged target is authorized.
        expect_target_sha256: Digest captured immediately before this call.

    Returns:
        The plan and the inspection it was computed from.
    """
    require_not_shadowed(inspect_override(target), target)
    require_target_parent(target, lexical=target_lexical)
    inspection = inspect_target(target, lexical=target_lexical)
    _check_adoption(
        inspection,
        replace_unmanaged=replace_unmanaged,
        expect_target_sha256=expect_target_sha256,
    )
    mode = NEW_TARGET_MODE if inspection.mode is None else inspection.mode
    plan = InstallPlan(
        target=PathPair(lexical=inspection.lexical_path, resolved=str(inspection.path)),
        previous_target=_previous_record(inspection),
        output_sha256=compiled.rendered.sha256,
        output_bytes=compiled.rendered.size_bytes,
        target_mode=mode,
        backup_path=None
        if inspection.kind is TargetKind.MISSING
        else state_dir / BACKUPS_DIRNAME,
        adoption_required=inspection.kind is TargetKind.UNMANAGED,
        state_dir=state_dir,
        already_current=inspection.sha256 == compiled.rendered.sha256,
    )
    return plan, inspection


def install_bundle(
    compiled: CompiledBundle,
    *,
    lock: PathPair,
    target: Path,
    state_dir: Path,
    lock_dir: Path,
    target_lexical: str | None = None,
    apply: bool = False,
    replace_unmanaged: bool = False,
    expect_target_sha256: str | None = None,
    clock: Clock = _default_clock,
    operation_id_factory: IdFactory = _default_operation_id,
    lock_timeout_seconds: float | None = None,
) -> InstallOutcome:
    """Install a compiled bundle, or preview the install.

    Propagated failures: :class:`ShadowedError` for a non-empty override,
    :class:`UnmanagedTargetError` when adoption rules are unmet,
    :class:`TargetError` for an unusable target path,
    :class:`ConcurrentChangeError` when the target changed after its precondition
    was captured, and :class:`MutationError` when a mutation step failed or the
    postcondition did not hold.

    Args:
        compiled: The compiled bundle to install.
        lock: Lock path pair used to compile the bundle.
        target: Resolved target path.
        state_dir: Target-qualified deployment state directory.
        lock_dir: Required distribution-wide advisory-lock directory. Callers
            must share it across bundles that can select the same target.
        target_lexical: Target path as supplied.
        apply: Perform the mutation. Without it nothing is created.
        replace_unmanaged: Authorize replacing a target with no generated header.
        expect_target_sha256: Digest captured immediately before the dry run.
        clock: Injectable wall clock.
        operation_id_factory: Injectable operation-identifier factory.
        lock_timeout_seconds: Advisory lock deadline, or ``None`` for the default.

    Returns:
        The outcome, including the plan and, when applied, the backup and receipt.

    Raises:
        MutationError: A step after target replacement failed. The error reports
            whether exact recovery completed or also failed.
    """
    plan, before = plan_install(
        compiled,
        target=target,
        target_lexical=target_lexical,
        state_dir=state_dir,
        replace_unmanaged=replace_unmanaged,
        expect_target_sha256=expect_target_sha256,
    )
    operation_id = operation_id_factory()
    if not apply:
        return InstallOutcome(
            state=BundleState.CURRENT
            if plan.already_current
            else BundleState.MISSING
            if before.kind is TargetKind.MISSING
            else BundleState.DRIFTED,
            applied=False,
            plan=plan,
            backup=None,
            installed=None,
            receipt_path=None,
            operation_id=operation_id,
            completed_at=None,
        )

    for name in (
        RECEIPTS_DIRNAME,
        BACKUPS_DIRNAME,
        PRESERVED_DIRNAME,
    ):
        create_state_directory(state_dir / name, mode=STATE_DIR_MODE)
    create_state_directory(lock_dir, mode=STATE_DIR_MODE)
    timeout = (
        DEFAULT_TIMEOUT_SECONDS
        if lock_timeout_seconds is None
        else lock_timeout_seconds
    )
    lock_file = lock_path_for(target, lock_dir=lock_dir)
    with advisory_lock(lock_file, timeout_seconds=timeout):
        after = _refuse_if_changed(before, target, lexical=target_lexical)
        moment = clock()
        stamp = format_file_stamp(moment)
        backup = (
            None
            if after.kind is TargetKind.MISSING
            else _write_backup(
                after,
                backups_dir=state_dir / BACKUPS_DIRNAME,
                stamp=stamp,
            )
        )
        mode = NEW_TARGET_MODE if after.mode is None else after.mode
        installed = WrittenRecord(
            sha256=compiled.rendered.sha256,
            size_bytes=compiled.rendered.size_bytes,
            mode=mode,
        )
        receipt_path = (
            state_dir
            / RECEIPTS_DIRNAME
            / receipt_name(INSTALL_OPERATION, stamp, operation_id)
        )
        completed_at = format_timestamp(moment)
        atomic_write(target, compiled.rendered.data, mode=mode)
        receipt_written = False
        try:
            _verify_postcondition(target, compiled.rendered.sha256)
            write_receipt(
                receipt_path,
                build_install_payload(
                    operation_id=operation_id,
                    compiler_version=distribution_version(),
                    bundle_id=compiled.manifest.bundle_id,
                    manifest=PathPair(
                        lexical=compiled.manifest.lexical_path,
                        resolved=str(compiled.manifest.path),
                    ),
                    lock=lock,
                    target=PathPair(
                        lexical=after.lexical_path, resolved=str(after.path)
                    ),
                    manifest_sha256=compiled.manifest.sha256,
                    lock_sha256=compiled.lock_sha256,
                    modules=_module_digests(compiled),
                    previous_target=_previous_record(after),
                    backup=backup,
                    installed=installed,
                    completed_at=completed_at,
                ),
            )
            receipt_written = True
            _record_last_installed(state_dir, installed, receipt_path, completed_at)
        except MutationError as install_error:
            try:
                recovery_detail = _recover_failed_install(
                    before=after,
                    backup=backup,
                    target=target,
                    target_lexical=target_lexical,
                    state_dir=state_dir,
                    installed_sha256=compiled.rendered.sha256,
                    stamp=stamp,
                    operation_id=operation_id,
                    receipt_path=receipt_path,
                    receipt_written=receipt_written,
                )
            except CompilerError as recovery_error:
                detail = (
                    f"install failure: {install_error}; "
                    f"recovery failure: {recovery_error}"
                )
                raise MutationError(
                    MutationProblem.RECOVERY_FAILED, path=target, detail=detail
                ) from recovery_error
            detail = f"{install_error}; {recovery_detail}"
            raise MutationError(
                install_error.problem, path=target, detail=detail
            ) from install_error
    return InstallOutcome(
        state=BundleState.CURRENT,
        applied=True,
        plan=plan,
        backup=backup,
        installed=installed,
        receipt_path=receipt_path,
        operation_id=operation_id,
        completed_at=completed_at,
    )


def _record_last_installed(
    state_dir: Path, installed: WrittenRecord, receipt_path: Path, completed_at: str
) -> None:
    """Record the digest of the most recent successful install.

    Args:
        state_dir: Target-qualified deployment state directory.
        installed: Bytes that were written.
        receipt_path: Receipt describing the install.
        completed_at: UTC completion time.
    """
    write_receipt(
        state_dir / LAST_INSTALLED_FILENAME,
        {
            "completed_at": completed_at,
            "installed_sha256": installed.sha256,
            "installed_size_bytes": installed.size_bytes,
            "receipt": receipt_path.name,
        },
    )


def rollback_install(
    receipt: InstallReceipt,
    *,
    target: Path,
    state_dir: Path,
    lock_dir: Path,
    target_lexical: str | None = None,
    apply: bool = False,
    clock: Clock = _default_clock,
    operation_id_factory: IdFactory = _default_operation_id,
    lock_timeout_seconds: float | None = None,
) -> RollbackOutcome:
    """Restore the bytes one install replaced, or preview the restore.

    Propagated failures: :class:`ReceiptError` when the recorded backup is missing
    or altered, :class:`MutationError` when a mutation step failed, and
    :class:`TargetError` for an unusable target path.

    Args:
        receipt: A receipt already validated for this invocation.
        target: Resolved target path.
        state_dir: Target-qualified deployment state directory.
        lock_dir: Required distribution-wide advisory-lock directory. Callers
            must share it across bundles that can select the same target.
        target_lexical: Target path as supplied.
        apply: Perform the mutation. Without it nothing is changed.
        clock: Injectable wall clock.
        operation_id_factory: Injectable operation-identifier factory.
        lock_timeout_seconds: Advisory lock deadline, or ``None`` for the default.

    Returns:
        The outcome, including what was restored or preserved.

    Raises:
        ConcurrentChangeError: The target no longer matches the receipt's installed
            digest, so restoring would discard someone else's change.
    """
    current = inspect_target(target, lexical=target_lexical)
    if current.sha256 != receipt.installed.sha256:
        raise ConcurrentChangeError(
            ConcurrentChangeProblem.TARGET_CHANGED,
            path=target,
            detail=(
                f"receipt installed {receipt.installed.sha256}, "
                f"target is {current.sha256}"
            ),
        )
    if receipt.backup is not None:
        _verify_backup(receipt)
    operation_id = operation_id_factory()
    if not apply:
        return RollbackOutcome(
            state=BundleState.DRIFTED
            if receipt.backup is not None
            else BundleState.MISSING,
            applied=False,
            receipt=receipt,
            restored=None,
            preserved_path=None,
            receipt_path=None,
            operation_id=operation_id,
            completed_at=None,
        )

    for name in (RECEIPTS_DIRNAME, PRESERVED_DIRNAME):
        create_state_directory(state_dir / name, mode=STATE_DIR_MODE)
    create_state_directory(lock_dir, mode=STATE_DIR_MODE)
    timeout = (
        DEFAULT_TIMEOUT_SECONDS
        if lock_timeout_seconds is None
        else lock_timeout_seconds
    )
    lock_file = lock_path_for(target, lock_dir=lock_dir)
    with advisory_lock(lock_file, timeout_seconds=timeout):
        under_lock = _refuse_if_changed(current, target, lexical=target_lexical)
        moment = clock()
        stamp = format_file_stamp(moment)
        restored, preserved_path, state = _restore(receipt, target, state_dir, stamp)
        completed_at = format_timestamp(moment)
        receipt_path = (
            state_dir
            / RECEIPTS_DIRNAME
            / receipt_name(ROLLBACK_OPERATION, stamp, operation_id)
        )
        write_receipt(
            receipt_path,
            build_rollback_payload(
                operation_id=operation_id,
                compiler_version=distribution_version(),
                source=receipt,
                previous_target=_previous_record(under_lock),
                restored=restored,
                preserved_path=preserved_path,
                source_ref=SourceReceiptRef(
                    path=receipt.path,
                    sha256=receipt.sha256,
                    operation_id=receipt.operation_id,
                ),
                completed_at=completed_at,
            ),
        )
    return RollbackOutcome(
        state=state,
        applied=True,
        receipt=receipt,
        restored=restored,
        preserved_path=preserved_path,
        receipt_path=receipt_path,
        operation_id=operation_id,
        completed_at=completed_at,
    )


def _verify_backup(receipt: InstallReceipt) -> None:
    """Prove the recorded backup still exists and still matches its digest.

    Args:
        receipt: The install receipt being rolled back.

    Raises:
        ReceiptError: The backup is absent, unreadable, or altered.
    """
    backup = receipt.backup
    if backup is None:  # pragma: no cover - callers check for a recorded backup
        raise ReceiptError(ReceiptProblem.NO_BACKUP_RECORDED, receipt=receipt.path)
    if backup.path.is_symlink() or not backup.path.is_file():
        raise ReceiptError(
            ReceiptProblem.BACKUP_MISSING,
            receipt=receipt.path,
            detail=str(backup.path),
        )
    try:
        observed = sha256_file(backup.path)
    except OSError as error:
        raise ReceiptError(
            ReceiptProblem.BACKUP_MISSING,
            receipt=receipt.path,
            detail=error.strerror or type(error).__name__,
        ) from error
    if observed != backup.sha256:
        raise ReceiptError(
            ReceiptProblem.BACKUP_DIGEST_MISMATCH,
            receipt=receipt.path,
            detail=f"expected {backup.sha256}, observed {observed}",
        )


def _restore(
    receipt: InstallReceipt, target: Path, state_dir: Path, stamp: str
) -> tuple[WrittenRecord | None, Path | None, BundleState]:
    """Restore a backup, or preserve a generated target that had no predecessor.

    Args:
        receipt: The install receipt being rolled back.
        target: Resolved target path.
        state_dir: Target-qualified deployment state directory.
        stamp: Compact UTC stamp for a preserved file name.

    Returns:
        The restored record or ``None``, the preserved path or ``None``, and the
        resulting state.

    Raises:
        MutationError: Restoring or preserving failed.
    """
    backup = receipt.backup
    if backup is None:
        # The install created a previously missing target. Move the generated file
        # into the state directory rather than deleting it irrecoverably.
        preserved = (
            state_dir
            / PRESERVED_DIRNAME
            / f"{stamp}.{receipt.installed.sha256}.generated"
        )
        try:
            shutil.move(str(target), str(preserved))
            preserved.chmod(STATE_FILE_MODE)
        except OSError as error:
            raise MutationError(
                MutationProblem.REPLACE_FAILED,
                path=target,
                detail=error.strerror or type(error).__name__,
            ) from error
        return None, preserved, BundleState.MISSING
    try:
        data = backup.path.read_bytes()
    except OSError as error:
        raise MutationError(
            MutationProblem.BACKUP_FAILED,
            path=backup.path,
            detail=error.strerror or type(error).__name__,
        ) from error
    mode = (
        NEW_TARGET_MODE
        if receipt.previous_target.mode is None
        else receipt.previous_target.mode
    )
    atomic_write(target, data, mode=mode)
    _verify_postcondition(target, backup.sha256)
    restored = WrittenRecord(
        sha256=backup.sha256, size_bytes=backup.size_bytes, mode=mode
    )
    return restored, None, BundleState.DRIFTED

"""Install and rollback receipts.

A receipt is an operational record, so it carries timestamps and the compiler
version, which deterministic rendered output deliberately never does. A receipt
never carries policy content: only paths, identifiers, digests, sizes, and modes.

A receipt path handed to ``rollback`` is untrusted input. Loading one therefore
proves, before touching either recorded path, that the file is a regular
non-symlink file under the expected per-bundle state root, that it validates
against schema version 1, that it records an install for this bundle, and that its
target and backup paths belong to this invocation and that same state root.
"""

import json
import re
import stat
from pathlib import Path
from typing import Any, cast

from agents_md_compiler.atomic import atomic_write
from agents_md_compiler.errors import ReceiptError, ReceiptProblem
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import (
    IDENTIFIER_PATTERN,
    RECEIPT_SCHEMA_VERSION,
    SHA256_PATTERN,
    STATE_FILE_MODE,
    BackupRecord,
    InstallReceipt,
    ModuleDigest,
    PathPair,
    PreviousTargetRecord,
    SourceReceiptRef,
    TargetKind,
    WrittenRecord,
)
from agents_md_compiler.paths import is_within

TIMESTAMP_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
"""UTC completion time, second precision, Zulu suffix."""

OPERATION_ID_PATTERN = re.compile(r"\A[0-9a-f]{8,64}\Z")
"""Operation identifier, used in receipt and backup file names."""

MODE_PATTERN = re.compile(r"\A0[0-7]{3,4}\Z")
"""Octal permission bits as written into a receipt."""

INSTALL_OPERATION = "install"
ROLLBACK_OPERATION = "rollback"

RECEIPTS_DIRNAME = "receipts"
"""Subdirectory of the bundle state root that holds receipts."""

BACKUPS_DIRNAME = "backups"
"""Subdirectory of the bundle state root that holds immutable backups."""

PRESERVED_DIRNAME = "preserved"
"""Where rollback moves a generated target instead of deleting it."""

LOCKS_DIRNAME = "locks"
"""Subdirectory of the bundle state root that holds advisory lock files."""

LAST_INSTALLED_FILENAME = "last-installed.json"
"""Records the digest of the most recent successful install."""

MAX_RECEIPT_BYTES = 1024 * 1024
"""A receipt is small; a larger file is refused rather than parsed."""


def format_mode(mode: int) -> str:
    """Render permission bits the way a receipt records them.

    Args:
        mode: Permission bits.

    Returns:
        Zero-prefixed octal text, for example ``0600``.
    """
    return f"0{mode:03o}"


def parse_mode(text: str) -> int:
    """Read permission bits from a receipt.

    Args:
        text: Zero-prefixed octal text.

    Returns:
        The permission bits.
    """
    return int(text, 8)


def _serialize(payload: dict[str, Any]) -> bytes:
    """Serialize a receipt deterministically.

    Args:
        payload: Receipt document.

    Returns:
        UTF-8 bytes with sorted keys, two-space indentation, and one final LF.
    """
    text = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _module_payload(modules: tuple[ModuleDigest, ...]) -> list[dict[str, Any]]:
    """Render module identity for a receipt.

    Args:
        modules: Module identity in manifest order.

    Returns:
        One object per module, in order.
    """
    return [
        {"id": module.id, "sha256": module.sha256, "size_bytes": module.size_bytes}
        for module in modules
    ]


def _path_payload(pair: PathPair) -> dict[str, Any]:
    """Render a path pair for a receipt.

    Args:
        pair: Lexical and resolved forms.

    Returns:
        The path pair object.
    """
    return {"lexical": pair.lexical, "resolved": pair.resolved}


def _previous_payload(previous: PreviousTargetRecord) -> dict[str, Any]:
    """Render the prior target state for a receipt.

    Args:
        previous: Target state before the mutation.

    Returns:
        The previous-target object.
    """
    return {
        "state": previous.state.value,
        "sha256": previous.sha256,
        "size_bytes": previous.size_bytes,
        "mode": None if previous.mode is None else format_mode(previous.mode),
    }


def _backup_payload(backup: BackupRecord | None) -> dict[str, Any] | None:
    """Render a backup record for a receipt.

    Args:
        backup: Backup record, or ``None``.

    Returns:
        The backup object, or ``None``.
    """
    if backup is None:
        return None
    return {
        "path": str(backup.path),
        "sha256": backup.sha256,
        "size_bytes": backup.size_bytes,
    }


def _written_payload(written: WrittenRecord | None) -> dict[str, Any] | None:
    """Render written bytes for a receipt.

    Args:
        written: Written record, or ``None``.

    Returns:
        The written object, or ``None``.
    """
    if written is None:
        return None
    return {
        "sha256": written.sha256,
        "size_bytes": written.size_bytes,
        "mode": format_mode(written.mode),
    }


def build_install_payload(
    *,
    operation_id: str,
    compiler_version: str,
    bundle_id: str,
    manifest: PathPair,
    lock: PathPair,
    target: PathPair,
    manifest_sha256: str,
    lock_sha256: str,
    modules: tuple[ModuleDigest, ...],
    previous_target: PreviousTargetRecord,
    backup: BackupRecord | None,
    installed: WrittenRecord,
    completed_at: str,
) -> dict[str, Any]:
    """Build an install receipt document.

    Args:
        operation_id: Identifier for this mutation.
        compiler_version: Distribution version performing the install.
        bundle_id: Bundle identifier.
        manifest: Manifest path pair.
        lock: Lock path pair.
        target: Target path pair.
        manifest_sha256: Manifest digest.
        lock_sha256: Canonical lock digest.
        modules: Module identity in manifest order.
        previous_target: Target state before the write.
        backup: Backup of the prior bytes, or ``None``.
        installed: Bytes written.
        completed_at: UTC completion time.

    Returns:
        The receipt document.
    """
    return {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "operation": INSTALL_OPERATION,
        "operation_id": operation_id,
        "compiler_version": compiler_version,
        "bundle_id": bundle_id,
        "manifest_path": _path_payload(manifest),
        "lock_path": _path_payload(lock),
        "target_path": _path_payload(target),
        "manifest_sha256": manifest_sha256,
        "lock_sha256": lock_sha256,
        "modules": _module_payload(modules),
        "previous_target": _previous_payload(previous_target),
        "backup": _backup_payload(backup),
        "installed": _written_payload(installed),
        "completed_at": completed_at,
        "runtime_verification": None,
    }


def build_rollback_payload(
    *,
    operation_id: str,
    compiler_version: str,
    source: InstallReceipt,
    previous_target: PreviousTargetRecord,
    restored: WrittenRecord | None,
    preserved_path: Path | None,
    source_ref: SourceReceiptRef,
    completed_at: str,
) -> dict[str, Any]:
    """Build a rollback receipt document.

    Args:
        operation_id: Identifier for this mutation.
        compiler_version: Distribution version performing the rollback.
        source: The install receipt being rolled back.
        previous_target: Target state before the restore.
        restored: Bytes restored, or ``None`` when the target was preserved instead.
        preserved_path: Where a generated target was moved, or ``None``.
        source_ref: Reference to the consumed install receipt.
        completed_at: UTC completion time.

    Returns:
        The receipt document.
    """
    return {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "operation": ROLLBACK_OPERATION,
        "operation_id": operation_id,
        "compiler_version": compiler_version,
        "bundle_id": source.bundle_id,
        "manifest_path": _path_payload(source.manifest),
        "lock_path": _path_payload(source.lock),
        "target_path": _path_payload(source.target),
        "manifest_sha256": source.manifest_sha256,
        "lock_sha256": source.lock_sha256,
        "modules": _module_payload(source.modules),
        "previous_target": _previous_payload(previous_target),
        "backup": _backup_payload(source.backup),
        "restored": _written_payload(restored),
        "source_receipt": {
            "path": str(source_ref.path),
            "sha256": source_ref.sha256,
            "operation_id": source_ref.operation_id,
        },
        "preserved_path": None if preserved_path is None else str(preserved_path),
        "completed_at": completed_at,
        "runtime_verification": None,
    }


def write_receipt(path: Path, payload: dict[str, Any]) -> str:
    """Write a receipt atomically with owner-only permissions.

    Args:
        path: Receipt path.
        payload: Receipt document.

    Returns:
        Digest of the written receipt bytes.

    Raises:
        ReceiptError: The receipt could not be written.
    """
    data = _serialize(payload)
    try:
        atomic_write(path, data, mode=STATE_FILE_MODE)
    except OSError as error:  # pragma: no cover - atomic_write raises MutationError
        raise ReceiptError(
            ReceiptProblem.SYNTAX, receipt=path, detail=str(error)
        ) from error
    return sha256_bytes(data)


def _require(
    document: dict[str, object], key: str, path: Path, lexical: str | None
) -> object:
    """Fetch a required receipt key.

    Args:
        document: Decoded receipt.
        key: Key that must be present.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The value.

    Raises:
        ReceiptError: The key is absent.
    """
    if key not in document:
        raise ReceiptError.at(ReceiptProblem.SCHEMA, path, lexical, f"missing {key}")
    return document[key]


def _require_pattern(
    document: dict[str, object],
    key: str,
    pattern: re.Pattern[str],
    path: Path,
    lexical: str | None,
) -> str:
    """Fetch a required string matching a pattern.

    Args:
        document: Decoded receipt.
        key: Key to read.
        pattern: Pattern the value must match.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The validated string.

    Raises:
        ReceiptError: The value is missing, not a string, or does not match.
    """
    value = _require(document, key, path, lexical)
    if not isinstance(value, str) or pattern.match(value) is None:
        raise ReceiptError.at(ReceiptProblem.SCHEMA, path, lexical, f"invalid {key}")
    return value


def _require_object(
    document: dict[str, object], key: str, path: Path, lexical: str | None
) -> dict[str, object]:
    """Fetch a required nested object.

    Args:
        document: Decoded receipt.
        key: Key to read.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The nested object.

    Raises:
        ReceiptError: The value is missing or is not an object.
    """
    value = _require(document, key, path, lexical)
    if not isinstance(value, dict):
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, f"{key} is not an object"
        )
    return cast("dict[str, object]", value)


def _parse_path_pair(
    document: dict[str, object], key: str, path: Path, lexical: str | None
) -> PathPair:
    """Parse a path pair from a receipt.

    Args:
        document: Decoded receipt.
        key: Key holding the pair.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The path pair.

    Raises:
        ReceiptError: Either form is missing or not a non-empty string.
    """
    nested = _require_object(document, key, path, lexical)
    values: list[str] = []
    for field in ("lexical", "resolved"):
        value = _require(nested, field, path, lexical)
        if not isinstance(value, str) or not value:
            raise ReceiptError.at(
                ReceiptProblem.SCHEMA, path, lexical, f"invalid {key}.{field}"
            )
        values.append(value)
    return PathPair(lexical=values[0], resolved=values[1])


def _parse_optional_int(
    nested: dict[str, object], key: str, path: Path, lexical: str | None
) -> int | None:
    """Parse a nullable non-negative integer.

    Args:
        nested: Decoded object.
        key: Key to read.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The integer, or ``None``.

    Raises:
        ReceiptError: The value is neither null nor a non-negative integer.
    """
    value = _require(nested, key, path, lexical)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ReceiptError.at(ReceiptProblem.SCHEMA, path, lexical, f"invalid {key}")
    return value


def _parse_optional_text(
    nested: dict[str, object],
    key: str,
    pattern: re.Pattern[str],
    path: Path,
    lexical: str | None,
) -> str | None:
    """Parse a nullable pattern-checked string.

    Args:
        nested: Decoded object.
        key: Key to read.
        pattern: Pattern a present value must match.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The string, or ``None``.

    Raises:
        ReceiptError: A present value does not match the pattern.
    """
    value = _require(nested, key, path, lexical)
    if value is None:
        return None
    if not isinstance(value, str) or pattern.match(value) is None:
        raise ReceiptError.at(ReceiptProblem.SCHEMA, path, lexical, f"invalid {key}")
    return value


def _parse_modules(
    document: dict[str, object], path: Path, lexical: str | None
) -> tuple[ModuleDigest, ...]:
    """Parse recorded module identity.

    Args:
        document: Decoded receipt.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        Module identity in recorded order.

    Raises:
        ReceiptError: The array is missing, empty, or structurally invalid.
    """
    raw = _require(document, "modules", path, lexical)
    if not isinstance(raw, list) or not raw:
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, "modules must be non-empty"
        )
    entries = cast("list[object]", raw)
    modules: list[ModuleDigest] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReceiptError.at(
                ReceiptProblem.SCHEMA, path, lexical, "module entry is not an object"
            )
        module = cast("dict[str, object]", entry)
        module_id = _require_pattern(module, "id", IDENTIFIER_PATTERN, path, lexical)
        digest = _require_pattern(module, "sha256", SHA256_PATTERN, path, lexical)
        size = _require(module, "size_bytes", path, lexical)
        if type(size) is not int or size < 1:
            raise ReceiptError.at(
                ReceiptProblem.SCHEMA, path, lexical, "invalid module size_bytes"
            )
        modules.append(ModuleDigest(id=module_id, sha256=digest, size_bytes=size))
    return tuple(modules)


def _parse_previous(
    document: dict[str, object], path: Path, lexical: str | None
) -> PreviousTargetRecord:
    """Parse the recorded prior target state.

    Args:
        document: Decoded receipt.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The prior target state.

    Raises:
        ReceiptError: The object is missing or structurally invalid.
    """
    nested = _require_object(document, "previous_target", path, lexical)
    state_text = _require(nested, "state", path, lexical)
    if not isinstance(state_text, str) or state_text not in tuple(TargetKind):
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, "invalid previous state"
        )
    mode_text = _parse_optional_text(nested, "mode", MODE_PATTERN, path, lexical)
    return PreviousTargetRecord(
        state=TargetKind(state_text),
        sha256=_parse_optional_text(nested, "sha256", SHA256_PATTERN, path, lexical),
        size_bytes=_parse_optional_int(nested, "size_bytes", path, lexical),
        mode=None if mode_text is None else parse_mode(mode_text),
    )


def _parse_backup(
    document: dict[str, object], path: Path, lexical: str | None
) -> BackupRecord | None:
    """Parse the recorded backup, when one exists.

    Args:
        document: Decoded receipt.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The backup record, or ``None``.

    Raises:
        ReceiptError: A present backup is structurally invalid.
    """
    value = _require(document, "backup", path, lexical)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, "backup is not an object"
        )
    nested = cast("dict[str, object]", value)
    backup_path = _require(nested, "path", path, lexical)
    if not isinstance(backup_path, str) or not backup_path:
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, "invalid backup path"
        )
    digest = _require_pattern(nested, "sha256", SHA256_PATTERN, path, lexical)
    size = _parse_optional_int(nested, "size_bytes", path, lexical)
    if size is None:
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, "invalid backup size_bytes"
        )
    return BackupRecord(path=Path(backup_path), sha256=digest, size_bytes=size)


def _parse_installed(
    document: dict[str, object], path: Path, lexical: str | None
) -> WrittenRecord:
    """Parse the recorded installed bytes.

    Args:
        document: Decoded receipt.
        path: Resolved receipt path.
        lexical: Receipt path as supplied.

    Returns:
        The installed record.

    Raises:
        ReceiptError: The object is missing or structurally invalid.
    """
    nested = _require_object(document, "installed", path, lexical)
    digest = _require_pattern(nested, "sha256", SHA256_PATTERN, path, lexical)
    size = _parse_optional_int(nested, "size_bytes", path, lexical)
    if not size:
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA, path, lexical, "invalid installed size_bytes"
        )
    mode_text = _require_pattern(nested, "mode", MODE_PATTERN, path, lexical)
    return WrittenRecord(sha256=digest, size_bytes=size, mode=parse_mode(mode_text))


def _read_receipt_bytes(path: Path, lexical: str | None, state_root: Path) -> bytes:
    """Read a receipt after proving the path itself is acceptable.

    Args:
        path: Resolved receipt path.
        lexical: Receipt path as supplied.
        state_root: Expected per-bundle state root.

    Returns:
        The exact receipt bytes.

    Raises:
        ReceiptError: The path is a link, missing, not a regular file, outside the
            expected state root, unreadable, or larger than the accepted bound.
    """
    if not is_within(path, state_root):
        raise ReceiptError.at(
            ReceiptProblem.OUTSIDE_STATE_ROOT,
            path,
            lexical,
            f"expected under {state_root}",
        )
    if path.is_symlink():
        raise ReceiptError.at(ReceiptProblem.SYMLINK, path, lexical)
    try:
        result = path.stat()
    except FileNotFoundError as error:
        raise ReceiptError.at(ReceiptProblem.MISSING, path, lexical) from error
    except OSError as error:
        raise ReceiptError.at(
            ReceiptProblem.SYNTAX, path, lexical, error.strerror or type(error).__name__
        ) from error
    if not stat.S_ISREG(result.st_mode):
        raise ReceiptError.at(ReceiptProblem.NOT_A_FILE, path, lexical)
    if result.st_size > MAX_RECEIPT_BYTES:
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA,
            path,
            lexical,
            f"{result.st_size} bytes exceeds {MAX_RECEIPT_BYTES}",
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise ReceiptError.at(
            ReceiptProblem.SYNTAX, path, lexical, error.strerror or type(error).__name__
        ) from error


def load_install_receipt(
    path: Path,
    *,
    state_root: Path,
    bundle_id: str,
    target: Path,
    lexical: str | None = None,
) -> InstallReceipt:
    """Load and fully validate an install receipt for this invocation.

    Every check runs before either recorded path is read or written, so a forged
    receipt cannot direct this tool at an arbitrary file.

    Args:
        path: Resolved receipt path.
        state_root: Expected per-bundle state root.
        bundle_id: Bundle identifier this invocation is operating on.
        target: Target path this invocation resolved.
        lexical: Receipt path as supplied.

    Returns:
        The validated receipt.

    Raises:
        ReceiptError: The receipt is unusable for this invocation for any reason.
    """
    data = _read_receipt_bytes(path, lexical, state_root)
    try:
        decoded: object = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ReceiptError.at(
            ReceiptProblem.SYNTAX, path, lexical, str(error)
        ) from error
    if not isinstance(decoded, dict):
        raise ReceiptError.at(ReceiptProblem.NOT_AN_OBJECT, path, lexical)
    document = cast("dict[str, object]", decoded)

    version = _require(document, "receipt_schema_version", path, lexical)
    if type(version) is not int:
        raise ReceiptError.at(
            ReceiptProblem.SCHEMA,
            path,
            lexical,
            "receipt_schema_version is not an integer",
        )
    if version != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError.at(
            ReceiptProblem.UNSUPPORTED_VERSION,
            path,
            lexical,
            f"{version}, expected {RECEIPT_SCHEMA_VERSION}",
        )
    operation = _require(document, "operation", path, lexical)
    if operation != INSTALL_OPERATION:
        raise ReceiptError.at(
            ReceiptProblem.WRONG_OPERATION, path, lexical, f"records {operation!r}"
        )
    recorded_bundle = _require_pattern(
        document, "bundle_id", IDENTIFIER_PATTERN, path, lexical
    )
    if recorded_bundle != bundle_id:
        raise ReceiptError.at(
            ReceiptProblem.BUNDLE_MISMATCH,
            path,
            lexical,
            f"records {recorded_bundle!r}, this invocation is {bundle_id!r}",
        )
    target_pair = _parse_path_pair(document, "target_path", path, lexical)
    if target_pair.resolved != str(target):
        raise ReceiptError.at(
            ReceiptProblem.TARGET_MISMATCH,
            path,
            lexical,
            f"records {target_pair.resolved}, this invocation resolved {target}",
        )
    backup = _parse_backup(document, path, lexical)
    if backup is not None and not is_within(backup.path, state_root):
        raise ReceiptError.at(
            ReceiptProblem.BACKUP_OUTSIDE_STATE_ROOT,
            path,
            lexical,
            f"{backup.path} is not under {state_root}",
        )
    return InstallReceipt(
        path=path,
        sha256=sha256_bytes(data),
        schema_version=version,
        operation_id=_require_pattern(
            document, "operation_id", OPERATION_ID_PATTERN, path, lexical
        ),
        compiler_version=str(_require(document, "compiler_version", path, lexical)),
        bundle_id=recorded_bundle,
        manifest=_parse_path_pair(document, "manifest_path", path, lexical),
        lock=_parse_path_pair(document, "lock_path", path, lexical),
        target=target_pair,
        manifest_sha256=_require_pattern(
            document, "manifest_sha256", SHA256_PATTERN, path, lexical
        ),
        lock_sha256=_require_pattern(
            document, "lock_sha256", SHA256_PATTERN, path, lexical
        ),
        modules=_parse_modules(document, path, lexical),
        previous_target=_parse_previous(document, path, lexical),
        backup=backup,
        installed=_parse_installed(document, path, lexical),
        completed_at=_require_pattern(
            document, "completed_at", TIMESTAMP_PATTERN, path, lexical
        ),
    )


def receipt_name(operation: str, stamp: str, operation_id: str) -> str:
    """Build the file name for one receipt.

    The UTC stamp comes first and the operation second. That order is the whole
    point of this function: with the operation first, every ``install`` name sorted
    before every ``rollback`` name, so a rollback performed at noon sorted after an
    install performed at midnight and :func:`list_receipts` reported the older
    receipt as the newest one. Sorting by time first makes the name order match the
    order the operations actually happened in.

    Args:
        operation: Operation that produced the receipt.
        stamp: UTC stamp from ``format_file_stamp``, at microsecond precision.
        operation_id: Identifier for this mutation, which makes the name unique.

    Returns:
        The receipt file name, without a directory.
    """
    return f"{stamp}-{operation}-{operation_id}.json"


def list_receipts(state_dir: Path) -> tuple[Path, ...]:
    """List receipts recorded for one bundle, newest name last.

    Receipt names begin with a fixed-width UTC timestamp at microsecond precision,
    so lexical order is chronological without reading any file. See
    :func:`receipt_name` for why the timestamp must lead.

    Args:
        state_dir: Per-bundle state directory.

    Returns:
        Receipt paths in sorted order, empty when none exist.
    """
    directory = state_dir / RECEIPTS_DIRNAME
    if not directory.is_dir():
        return ()
    return tuple(sorted(p for p in directory.glob("*.json") if p.is_file()))


def list_backups(state_dir: Path) -> tuple[Path, ...]:
    """List backups recorded for one bundle.

    Args:
        state_dir: Per-bundle state directory.

    Returns:
        Backup paths in sorted order, empty when none exist.
    """
    directory = state_dir / BACKUPS_DIRNAME
    if not directory.is_dir():
        return ()
    return tuple(sorted(p for p in directory.glob("*.bak") if p.is_file()))

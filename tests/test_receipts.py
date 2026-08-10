"""Receipt loading treats its input as untrusted.

A receipt path handed to ``rollback`` can be forged, so every check below must run
before either recorded path is read or written.
"""

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from conftest import RECEIPT_FIXTURES

from agents_md_compiler import receipts
from agents_md_compiler.errors import ReceiptError, ReceiptProblem
from agents_md_compiler.hashing import sha256_file
from agents_md_compiler.models import InstallReceipt, TargetKind

TARGET = Path("/example/home/.codex/AGENTS.md")
BUNDLE = "fixture-bundle"


def valid_document() -> dict[str, Any]:
    """Read the valid install receipt fixture.

    Returns:
        The decoded receipt document.
    """
    decoded: dict[str, Any] = json.loads(
        (RECEIPT_FIXTURES / "install-valid.json").read_text(encoding="utf-8")
    )
    return decoded


def place(
    state_root: Path, document: dict[str, Any], *, name: str = "install-x.json"
) -> Path:
    """Write a receipt document into a disposable state root.

    Args:
        state_root: Accepted state root.
        document: Receipt document.
        name: Receipt file name.

    Returns:
        The receipt path.
    """
    directory = state_root / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def load(state_root: Path, path: Path) -> InstallReceipt:
    """Load a receipt with the fixture's bundle and target.

    Args:
        state_root: Accepted state root.
        path: Receipt path.

    Returns:
        The validated receipt.
    """
    return receipts.load_install_receipt(
        path, state_root=state_root, bundle_id=BUNDLE, target=TARGET
    )


def prepare_backup(state_root: Path, document: dict[str, Any]) -> None:
    """Point the document's backup at a real file inside the state root.

    Args:
        state_root: Accepted state root.
        document: Receipt document to adjust in place.
    """
    backups = state_root / receipts.BACKUPS_DIRNAME
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / "20260804T210000Z.backup.bak"
    backup.write_text("# Prior content\n", encoding="utf-8")
    document["backup"]["path"] = str(backup)


def test_mode_round_trips_through_its_recorded_form() -> None:
    assert receipts.format_mode(0o600) == "0600"
    assert receipts.format_mode(0o644) == "0644"
    assert receipts.parse_mode("0600") == 0o600
    assert receipts.parse_mode("0644") == 0o644


def test_a_valid_receipt_loads(tmp_path: Path) -> None:
    document = valid_document()
    prepare_backup(tmp_path, document)
    receipt = load(tmp_path, place(tmp_path, document))
    assert receipt.bundle_id == BUNDLE
    assert receipt.schema_version == 1
    assert receipt.previous_target.state is TargetKind.UNMANAGED
    assert receipt.previous_target.mode == 0o644
    assert receipt.installed.mode == 0o644
    assert [module.id for module in receipt.modules] == ["core"]
    assert receipt.completed_at == "2026-08-04T21:00:00Z"


def test_a_receipt_outside_the_state_root_is_refused(tmp_path: Path) -> None:
    document = valid_document()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    forged = elsewhere / "install-forged.json"
    forged.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path / "state", forged)
    assert raised.value.problem is ReceiptProblem.OUTSIDE_STATE_ROOT


def test_a_symlinked_receipt_is_refused(tmp_path: Path) -> None:
    document = valid_document()
    real = place(tmp_path, document, name="real.json")
    link = real.parent / "install-link.json"
    link.symlink_to(real)
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, link)
    assert raised.value.problem is ReceiptProblem.SYMLINK


def test_a_missing_receipt_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, directory / "absent.json")
    assert raised.value.problem is ReceiptProblem.MISSING


def test_a_directory_receipt_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME / "install-dir.json"
    directory.mkdir(parents=True)
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, directory)
    assert raised.value.problem is ReceiptProblem.NOT_A_FILE


def test_an_unreadable_receipt_is_refused(tmp_path: Path) -> None:
    path = place(tmp_path, valid_document())
    path.chmod(0o000)
    try:
        with pytest.raises(ReceiptError) as raised:
            load(tmp_path, path)
        assert raised.value.problem is ReceiptProblem.SYNTAX
    finally:
        path.chmod(0o600)


def test_a_stat_failure_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = place(tmp_path, valid_document())
    real_stat = Path.stat

    def _refuse(self: Path, *, follow_symlinks: bool = True) -> object:
        if self == path:
            message = "Stale file handle"
            raise OSError(116, message)
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _refuse)
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, path)
    assert raised.value.problem is ReceiptProblem.SYNTAX


def test_an_oversized_receipt_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    path = directory / "install-huge.json"
    path.write_bytes(b"{" + b" " * (receipts.MAX_RECEIPT_BYTES + 1))
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, path)
    assert raised.value.problem is ReceiptProblem.SCHEMA


def test_malformed_receipt_json_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    path = directory / "install-broken.json"
    path.write_bytes(b"{not json")
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, path)
    assert raised.value.problem is ReceiptProblem.SYNTAX


def test_non_utf8_receipt_bytes_are_refused(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    path = directory / "install-binary.json"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, path)
    assert raised.value.problem is ReceiptProblem.SYNTAX


def test_an_array_receipt_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    path = directory / "install-array.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, path)
    assert raised.value.problem is ReceiptProblem.NOT_AN_OBJECT


DAMAGE: list[tuple[str, dict[str, Any] | None, ReceiptProblem]] = [
    ("receipt_schema_version", None, ReceiptProblem.SCHEMA),
    # A missing key is a schema failure; WRONG_OPERATION is for a key that is
    # present and names something other than an install.
    ("operation", None, ReceiptProblem.SCHEMA),
    ("operation_id", None, ReceiptProblem.SCHEMA),
    ("compiler_version", None, ReceiptProblem.SCHEMA),
    ("bundle_id", None, ReceiptProblem.SCHEMA),
    ("manifest_path", None, ReceiptProblem.SCHEMA),
    ("lock_path", None, ReceiptProblem.SCHEMA),
    ("target_path", None, ReceiptProblem.SCHEMA),
    ("manifest_sha256", None, ReceiptProblem.SCHEMA),
    ("lock_sha256", None, ReceiptProblem.SCHEMA),
    ("modules", None, ReceiptProblem.SCHEMA),
    ("previous_target", None, ReceiptProblem.SCHEMA),
    ("backup", None, ReceiptProblem.SCHEMA),
    ("installed", None, ReceiptProblem.SCHEMA),
    ("completed_at", None, ReceiptProblem.SCHEMA),
]


@pytest.mark.parametrize(
    ("key", "problem"),
    [(key, problem) for key, _unused, problem in DAMAGE],
    ids=[key for key, _unused, _p in DAMAGE],
)
def test_a_missing_required_key_is_refused(
    tmp_path: Path, key: str, problem: ReceiptProblem
) -> None:
    document = valid_document()
    prepare_backup(tmp_path, document)
    del document[key]
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is problem


REPLACEMENTS: list[tuple[str, str, object, ReceiptProblem]] = [
    ("string-version", "receipt_schema_version", "1", ReceiptProblem.SCHEMA),
    ("future-version", "receipt_schema_version", 2, ReceiptProblem.UNSUPPORTED_VERSION),
    ("rollback-operation", "operation", "rollback", ReceiptProblem.WRONG_OPERATION),
    ("bad-operation-id", "operation_id", "NOTHEX", ReceiptProblem.SCHEMA),
    ("bad-bundle-id", "bundle_id", "Bad_Id", ReceiptProblem.SCHEMA),
    ("other-bundle", "bundle_id", "other-bundle", ReceiptProblem.BUNDLE_MISMATCH),
    ("bad-manifest-digest", "manifest_sha256", "abc", ReceiptProblem.SCHEMA),
    ("bad-lock-digest", "lock_sha256", "abc", ReceiptProblem.SCHEMA),
    ("empty-modules", "modules", [], ReceiptProblem.SCHEMA),
    ("modules-not-array", "modules", {}, ReceiptProblem.SCHEMA),
    ("module-not-object", "modules", ["core"], ReceiptProblem.SCHEMA),
    ("naive-timestamp", "completed_at", "2026-08-04 21:00:00", ReceiptProblem.SCHEMA),
    ("previous-not-object", "previous_target", "MISSING", ReceiptProblem.SCHEMA),
    ("installed-not-object", "installed", "x", ReceiptProblem.SCHEMA),
    ("backup-not-object", "backup", "x", ReceiptProblem.SCHEMA),
]


@pytest.mark.parametrize(
    ("key", "value", "problem"),
    [(key, value, problem) for _label, key, value, problem in REPLACEMENTS],
    ids=[label for label, _k, _v, _p in REPLACEMENTS],
)
def test_a_damaged_value_is_refused(
    tmp_path: Path, key: str, value: object, problem: ReceiptProblem
) -> None:
    document = valid_document()
    prepare_backup(tmp_path, document)
    document[key] = value
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is problem


def test_a_receipt_for_another_target_is_refused(tmp_path: Path) -> None:
    document = valid_document()
    prepare_backup(tmp_path, document)
    document["target_path"]["resolved"] = "/example/elsewhere/AGENTS.md"
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is ReceiptProblem.TARGET_MISMATCH


def test_a_backup_path_escaping_the_state_root_is_refused(tmp_path: Path) -> None:
    document = valid_document()
    document["backup"]["path"] = "/etc/passwd"
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is ReceiptProblem.BACKUP_OUTSIDE_STATE_ROOT


def test_a_backup_path_using_traversal_is_refused(tmp_path: Path) -> None:
    document = valid_document()
    document["backup"]["path"] = str(tmp_path / ".." / "escaped.bak")
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is ReceiptProblem.BACKUP_OUTSIDE_STATE_ROOT


def test_a_null_backup_is_accepted(tmp_path: Path) -> None:
    document = valid_document()
    document["backup"] = None
    document["previous_target"] = {
        "state": "MISSING",
        "sha256": None,
        "size_bytes": None,
        "mode": None,
    }
    receipt = load(tmp_path, place(tmp_path, document))
    assert receipt.backup is None
    assert receipt.previous_target.state is TargetKind.MISSING
    assert receipt.previous_target.mode is None


NESTED_DAMAGE: list[tuple[str, str, str, object, ReceiptProblem]] = [
    ("no-lexical", "target_path", "lexical", None, ReceiptProblem.SCHEMA),
    ("empty-resolved", "target_path", "resolved", "", ReceiptProblem.SCHEMA),
    (
        "bad-previous-state",
        "previous_target",
        "state",
        "PARTIAL",
        ReceiptProblem.SCHEMA,
    ),
    ("bad-previous-mode", "previous_target", "mode", "644", ReceiptProblem.SCHEMA),
    ("bad-previous-size", "previous_target", "size_bytes", -1, ReceiptProblem.SCHEMA),
    ("bad-previous-digest", "previous_target", "sha256", "zz", ReceiptProblem.SCHEMA),
    ("bad-installed-mode", "installed", "mode", "644", ReceiptProblem.SCHEMA),
    ("zero-installed-size", "installed", "size_bytes", 0, ReceiptProblem.SCHEMA),
    ("bad-installed-digest", "installed", "sha256", "abc", ReceiptProblem.SCHEMA),
    ("empty-backup-path", "backup", "path", "", ReceiptProblem.SCHEMA),
    ("bad-backup-digest", "backup", "sha256", "abc", ReceiptProblem.SCHEMA),
    ("null-backup-size", "backup", "size_bytes", None, ReceiptProblem.SCHEMA),
]


@pytest.mark.parametrize(
    ("outer", "inner", "value", "problem"),
    [
        (outer, inner, value, problem)
        for _l, outer, inner, value, problem in NESTED_DAMAGE
    ],
    ids=[label for label, _o, _i, _v, _p in NESTED_DAMAGE],
)
def test_damaged_nested_values_are_refused(
    tmp_path: Path, outer: str, inner: str, value: object, problem: ReceiptProblem
) -> None:
    document = valid_document()
    prepare_backup(tmp_path, document)
    if value is None and inner in {"lexical"}:
        del document[outer][inner]
    else:
        document[outer][inner] = value
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is problem


MODULE_DAMAGE: list[tuple[str, str, object]] = [
    ("bad-module-id", "id", "Core"),
    ("bad-module-digest", "sha256", "abc"),
    ("zero-module-size", "size_bytes", 0),
    ("string-module-size", "size_bytes", "122"),
]


@pytest.mark.parametrize(
    ("key", "value"),
    [(key, value) for _label, key, value in MODULE_DAMAGE],
    ids=[label for label, _k, _v in MODULE_DAMAGE],
)
def test_damaged_module_identity_is_refused(
    tmp_path: Path, key: str, value: object
) -> None:
    document = valid_document()
    prepare_backup(tmp_path, document)
    document["modules"][0][key] = value
    with pytest.raises(ReceiptError) as raised:
        load(tmp_path, place(tmp_path, document))
    assert raised.value.problem is ReceiptProblem.SCHEMA


def test_writing_a_receipt_returns_its_digest(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    digest = receipts.write_receipt(path, {"b": 1, "a": 2})
    assert digest == sha256_file(path)
    assert path.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "b": 1\n}\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_listing_receipts_and_backups_on_an_empty_state_root(tmp_path: Path) -> None:
    assert receipts.list_receipts(tmp_path) == ()
    assert receipts.list_backups(tmp_path) == ()


def test_listing_receipts_is_chronological(tmp_path: Path) -> None:
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    # The rollback here happened between the two installs, and sorts between them
    # only because the timestamp leads the name. Ordering by operation first would
    # push both installs ahead of it and report the middle receipt as the newest.
    for name in (
        receipts.receipt_name("install", "20260804T210000.000000Z", "a"),
        receipts.receipt_name("rollback", "20260804T210500.000000Z", "b"),
        receipts.receipt_name("install", "20260804T211000.000000Z", "c"),
    ):
        (directory / name).write_text("{}", encoding="utf-8")
    (directory / "notes.txt").write_text("ignored", encoding="utf-8")
    listed = [path.name for path in receipts.list_receipts(tmp_path)]
    assert listed == [
        "20260804T210000.000000Z-install-a.json",
        "20260804T210500.000000Z-rollback-b.json",
        "20260804T211000.000000Z-install-c.json",
    ]


def test_the_newest_receipt_is_last_even_when_operations_alternate(
    tmp_path: Path,
) -> None:
    """A rollback older than an install must not be reported as the newest receipt.

    This is the regression that made ``status``'s ``latest_receipt`` unusable: an
    operator who rolled back "the latest receipt" could restore the bytes from
    before an earlier install instead of the ones the most recent install replaced.
    """
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    older_rollback = receipts.receipt_name(
        "rollback", "20260804T210000.000000Z", "older"
    )
    newer_install = receipts.receipt_name("install", "20260804T220000.000000Z", "newer")
    for name in (older_rollback, newer_install):
        (directory / name).write_text("{}", encoding="utf-8")
    assert receipts.list_receipts(tmp_path)[-1].name == newer_install


def test_two_mutations_in_the_same_second_stay_ordered(tmp_path: Path) -> None:
    """Sub-second stamps decide the order, never the random operation id.

    At second precision these two names were equal up to the operation id, so which
    one sorted last depended on random hex rather than on which install ran first.
    """
    directory = tmp_path / receipts.RECEIPTS_DIRNAME
    directory.mkdir(parents=True)
    # The earlier mutation deliberately carries the id that sorts last.
    first = receipts.receipt_name("install", "20260804T210000.000001Z", "ffffffff")
    second = receipts.receipt_name("install", "20260804T210000.000002Z", "00000000")
    for name in (first, second):
        (directory / name).write_text("{}", encoding="utf-8")
    assert [p.name for p in receipts.list_receipts(tmp_path)] == [first, second]


def test_a_receipt_name_leads_with_the_stamp(tmp_path: Path) -> None:
    del tmp_path
    name = receipts.receipt_name("install", "20260804T210000.123456Z", "abc")
    assert name == "20260804T210000.123456Z-install-abc.json"
    assert name.startswith("20260804")


def test_listing_backups_finds_only_backup_files(tmp_path: Path) -> None:
    directory = tmp_path / receipts.BACKUPS_DIRNAME
    directory.mkdir(parents=True)
    (directory / "20260804T210000Z.aa.bak").write_text("x", encoding="utf-8")
    (directory / "notes.txt").write_text("ignored", encoding="utf-8")
    assert [p.name for p in receipts.list_backups(tmp_path)] == [
        "20260804T210000Z.aa.bak"
    ]

"""The published JSON schemas accept every valid fixture and reject every invalid one.

These schemas ship as package data for editors and external validators. The runtime
never validates with them, so this module is what keeps them honest. Rules the
schemas provably cannot express, listed in ``COMPILER_ONLY``, are enforced by the
compiler's own parser and covered by ``test_manifest.py``.

Structural damage is expressed as typed :class:`Mutation` data rather than as
lambdas so the whole module type-checks under strict Pyright.
"""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "src" / "agents_md_compiler" / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFESTS = FIXTURES / "manifests"

COMPILER_ONLY = {
    # JSON Schema "integer" accepts a number with a zero fractional part, and
    # "const": 1 compares numerically, so 1.0 satisfies both. TOML decodes it to a
    # float, and the compiler rejects it on exact type.
    "schema-version-float.toml",
    # Uniqueness across a property of array items is not expressible.
    "duplicate-module-id.toml",
    # Path equality after resolution is a filesystem fact, not a document fact.
    "duplicate-source-path.toml",
}

# jsonschema exposes partially unknown member types, which strict Pyright rejects
# at every call site. Confining it to one explicitly typed Any here keeps the rest
# of the module fully typed instead of scattering suppressions.
_VALIDATOR: Any = Draft202012Validator


@dataclass(frozen=True, slots=True)
class Mutation:
    """One structural damage operation applied to a decoded document.

    Attributes:
        label: Test identifier.
        key: Key to replace or delete in the addressed container.
        path: Keys and indices addressing the container that holds ``key``.
        value: Replacement value. Ignored when ``delete`` is set.
        delete: Remove ``key`` instead of replacing it.
    """

    label: str
    key: str
    path: tuple[str | int, ...] = ()
    value: object = None
    delete: bool = False


def _apply(document: dict[str, Any], mutation: Mutation) -> None:
    """Apply one mutation in place.

    Args:
        document: Decoded document to damage.
        mutation: The damage to apply.
    """
    container: Any = document
    for step in mutation.path:
        container = container[step]
    if mutation.delete:
        del container[mutation.key]
    else:
        container[mutation.key] = mutation.value


def _load_schema(name: str) -> dict[str, Any]:
    """Read one shipped schema.

    Args:
        name: Schema file name.

    Returns:
        The decoded schema document.
    """
    decoded: dict[str, Any] = json.loads(
        (SCHEMA_DIR / name).read_text(encoding="utf-8")
    )
    return decoded


def _check_schema(name: str) -> dict[str, Any]:
    """Assert a shipped schema is itself a valid Draft 2020-12 schema.

    Args:
        name: Schema file name.

    Returns:
        The decoded schema document.
    """
    schema = _load_schema(name)
    _VALIDATOR.check_schema(schema)
    return schema


def _is_valid(schema_name: str, instance: object) -> bool:
    """Report whether an instance satisfies a shipped schema.

    Args:
        schema_name: Schema file name.
        instance: Decoded instance to check.

    Returns:
        ``True`` when the instance validates.
    """
    return bool(_VALIDATOR(_load_schema(schema_name)).is_valid(instance))


def _assert_valid(schema_name: str, instance: object) -> None:
    """Fail with the schema's own diagnostic when an instance is invalid.

    Args:
        schema_name: Schema file name.
        instance: Decoded instance to check.
    """
    _VALIDATOR(_load_schema(schema_name)).validate(instance)


def _invalid_manifest_paths() -> list[Path]:
    """List every committed invalid manifest fixture.

    Returns:
        Sorted fixture paths.
    """
    return sorted((MANIFESTS / "invalid").glob("*.toml"))


def _decoded_toml(path: Path) -> dict[str, Any]:
    """Decode one TOML fixture.

    Args:
        path: Fixture path.

    Returns:
        The decoded mapping.
    """
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _golden_lock() -> dict[str, Any]:
    """Decode the golden lock template with a fixed placeholder substitution.

    Returns:
        The decoded lock document.
    """
    template = (FIXTURES / "golden" / "minimal.lock.json.tmpl").read_text(
        encoding="utf-8"
    )
    decoded: dict[str, Any] = json.loads(
        template.replace("__SOURCE_DIR__", "/example/modules")
    )
    return decoded


def _receipt(name: str) -> dict[str, Any]:
    """Decode one receipt fixture.

    Args:
        name: Fixture file name.

    Returns:
        The decoded receipt document.
    """
    decoded: dict[str, Any] = json.loads(
        (FIXTURES / "receipts" / name).read_text(encoding="utf-8")
    )
    return decoded


@pytest.mark.parametrize(
    "schema_name",
    ["manifest-v1.schema.json", "lock-v1.schema.json", "receipt-v1.schema.json"],
)
def test_every_shipped_schema_is_a_valid_draft_2020_12_schema(schema_name: str) -> None:
    schema = _check_schema(schema_name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert str(schema["$id"]).endswith(schema_name)


def test_schema_directory_holds_exactly_the_documented_schemas() -> None:
    assert sorted(p.name for p in SCHEMA_DIR.glob("*.json")) == [
        "lock-v1.schema.json",
        "manifest-v1.schema.json",
        "receipt-v1.schema.json",
    ]


@pytest.mark.parametrize("manifest_name", ["minimal.toml", "three-modules.toml"])
def test_manifest_schema_accepts_valid_manifests(manifest_name: str) -> None:
    _assert_valid("manifest-v1.schema.json", _decoded_toml(MANIFESTS / manifest_name))


def test_manifest_schema_rejects_every_expressible_invalid_fixture() -> None:
    accepted = [
        path.name
        for path in _invalid_manifest_paths()
        if _is_valid("manifest-v1.schema.json", _decoded_toml(path))
    ]
    assert set(accepted) == COMPILER_ONLY


def test_the_invalid_fixture_corpus_is_not_empty() -> None:
    assert len(_invalid_manifest_paths()) >= 25


def test_every_invalid_fixture_is_syntactically_valid_toml() -> None:
    # The repository's check-toml hook parses these files, so a fixture meant to
    # fail schema or compiler validation must still decode as TOML.
    for path in _invalid_manifest_paths():
        assert _decoded_toml(path) != {}


def test_lock_schema_accepts_the_golden_lock() -> None:
    _assert_valid("lock-v1.schema.json", _golden_lock())


def test_lock_schema_accepts_the_committed_example_lock() -> None:
    decoded = json.loads(
        (REPO_ROOT / "examples" / "minimal" / "global-agents.toml.lock.json").read_text(
            encoding="utf-8"
        )
    )
    _assert_valid("lock-v1.schema.json", decoded)


LOCK_DAMAGE = [
    Mutation("missing-bundle-id", "bundle_id", delete=True),
    Mutation("missing-format-version", "format_version", delete=True),
    Mutation("missing-manifest-digest", "manifest_sha256", delete=True),
    Mutation("missing-modules", "modules", delete=True),
    Mutation("unsupported-format-version", "format_version", value=2),
    Mutation("string-format-version", "format_version", value="1"),
    Mutation("unknown-top-level-key", "strict", value=True),
    Mutation("empty-modules", "modules", value=[]),
    Mutation("modules-not-an-array", "modules", value={"id": "core"}),
    Mutation("short-manifest-digest", "manifest_sha256", value="abc"),
    Mutation("uppercase-manifest-digest", "manifest_sha256", value="A" * 64),
    Mutation("bad-bundle-id-pattern", "bundle_id", value="Bad_Id"),
    Mutation("zero-size", "size_bytes", path=("modules", 0), value=0),
    Mutation("float-size", "size_bytes", path=("modules", 0), value=1.5),
    Mutation("non-hex-digest", "sha256", path=("modules", 0), value="z" * 64),
    Mutation(
        "missing-resolved-source", "resolved_source", path=("modules", 0), delete=True
    ),
    Mutation("empty-resolved-source", "resolved_source", path=("modules", 0), value=""),
    Mutation("unknown-module-key", "unexpected", path=("modules", 0), value=1),
    Mutation("bad-module-id", "id", path=("modules", 0), value="Core"),
]


@pytest.mark.parametrize("mutation", LOCK_DAMAGE, ids=[m.label for m in LOCK_DAMAGE])
def test_lock_schema_rejects_structural_damage(mutation: Mutation) -> None:
    lock = _golden_lock()
    _apply(lock, mutation)
    assert _is_valid("lock-v1.schema.json", lock) is False


@pytest.mark.parametrize("receipt_name", ["install-valid.json", "rollback-valid.json"])
def test_receipt_schema_accepts_valid_receipts(receipt_name: str) -> None:
    _assert_valid("receipt-v1.schema.json", _receipt(receipt_name))


INSTALL_DAMAGE = [
    Mutation("install-without-installed", "installed", delete=True),
    Mutation("install-with-restored", "restored", value=None),
    Mutation("install-with-source-receipt", "source_receipt", value={}),
    Mutation("install-with-preserved-path", "preserved_path", value=None),
    Mutation("unknown-operation", "operation", value="restore"),
    Mutation("unsupported-receipt-version", "receipt_schema_version", value=2),
    Mutation("non-hex-operation-id", "operation_id", value="NOTHEX00"),
    Mutation("short-operation-id", "operation_id", value="abc"),
    Mutation("empty-compiler-version", "compiler_version", value=""),
    Mutation("naive-timestamp", "completed_at", value="2026-08-04 21:00:00"),
    Mutation("offset-timestamp", "completed_at", value="2026-08-04T21:00:00+00:00"),
    Mutation("unknown-top-level-key", "notes", value="anything"),
    Mutation("missing-modules", "modules", delete=True),
    Mutation("empty-modules", "modules", value=[]),
    Mutation(
        "unknown-previous-state", "state", path=("previous_target",), value="PARTIAL"
    ),
    Mutation("previous-target-not-a-mapping", "previous_target", value="MISSING"),
    Mutation("non-octal-mode", "mode", path=("installed",), value="644"),
    Mutation("zero-installed-size", "size_bytes", path=("installed",), value=0),
    Mutation("unknown-installed-key", "checksum", path=("installed",), value="x"),
    Mutation(
        "path-pair-without-resolved", "resolved", path=("target_path",), delete=True
    ),
    Mutation("path-pair-extra-key", "canonical", path=("target_path",), value="/x"),
    Mutation("backup-without-digest", "sha256", path=("backup",), delete=True),
    Mutation("missing-runtime-verification", "runtime_verification", delete=True),
    Mutation(
        "bad-runtime-state",
        "runtime_verification",
        value={"state": "OK", "codex_version": None},
    ),
]


@pytest.mark.parametrize(
    "mutation", INSTALL_DAMAGE, ids=[m.label for m in INSTALL_DAMAGE]
)
def test_receipt_schema_rejects_damaged_install_receipts(mutation: Mutation) -> None:
    receipt = _receipt("install-valid.json")
    _apply(receipt, mutation)
    assert _is_valid("receipt-v1.schema.json", receipt) is False


ROLLBACK_DAMAGE = [
    Mutation("rollback-without-source-receipt", "source_receipt", delete=True),
    Mutation("rollback-without-restored", "restored", delete=True),
    Mutation("rollback-without-preserved-path", "preserved_path", delete=True),
    Mutation("rollback-with-installed", "installed", value={}),
    Mutation(
        "source-receipt-missing-digest", "sha256", path=("source_receipt",), delete=True
    ),
    Mutation(
        "source-receipt-unknown-key", "kind", path=("source_receipt",), value="install"
    ),
    Mutation("restored-not-a-mapping", "restored", value="restored"),
]


@pytest.mark.parametrize(
    "mutation", ROLLBACK_DAMAGE, ids=[m.label for m in ROLLBACK_DAMAGE]
)
def test_receipt_schema_rejects_damaged_rollback_receipts(mutation: Mutation) -> None:
    receipt = _receipt("rollback-valid.json")
    _apply(receipt, mutation)
    assert _is_valid("receipt-v1.schema.json", receipt) is False

"""Strict manifest parsing.

The parser rejects rather than repairs. Unknown keys, wrong types, blank values,
duplicate identifiers, and duplicate resolved paths are all failures, because a
manifest is a reviewed artifact and silently ignoring part of it would mean the
review covered something other than what was compiled.

Types are checked exactly. ``bool`` is rejected where an ``int`` is required,
because Python treats ``True`` as equal to ``1`` and a coerced boolean would pass a
naive equality check.
"""

import tomllib
from pathlib import Path
from typing import cast

from agents_md_compiler.errors import ManifestError, ManifestProblem
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import (
    IDENTIFIER_PATTERN,
    MANIFEST_SCHEMA_VERSION,
    BundleLimits,
    BundleManifest,
    ModuleSpec,
)
from agents_md_compiler.paths import resolve_against

TOP_LEVEL_KEYS = frozenset({"schema_version", "bundle_id", "default_target", "modules"})
"""The only accepted top-level manifest keys."""

MODULE_KEYS = frozenset({"id", "source"})
"""The only accepted module keys."""


def _read_manifest_bytes(path: Path, lexical: str, limits: BundleLimits) -> bytes:
    """Read the exact manifest bytes with a size bound.

    Args:
        path: Resolved manifest path.
        lexical: Manifest path as supplied.
        limits: Configured safeguards.

    Returns:
        The exact file bytes.

    Raises:
        ManifestError: The manifest is absent, not a regular file, too large, or
            unreadable.
    """
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ManifestError(ManifestProblem.NOT_A_FILE, manifest=path, lexical=lexical)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ManifestError(
            ManifestProblem.UNREADABLE,
            detail=error.strerror or type(error).__name__,
            manifest=path,
            lexical=lexical,
        ) from error
    if size > limits.max_manifest_bytes:
        raise ManifestError(
            ManifestProblem.TOO_LARGE,
            detail=f"{size} > {limits.max_manifest_bytes}",
            manifest=path,
            lexical=lexical,
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise ManifestError(
            ManifestProblem.UNREADABLE,
            detail=error.strerror or type(error).__name__,
            manifest=path,
            lexical=lexical,
        ) from error


def _decode(data: bytes, path: Path, lexical: str) -> dict[str, object]:
    """Decode manifest bytes as TOML.

    Args:
        data: Exact manifest bytes.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.

    Returns:
        The decoded mapping.

    Raises:
        ManifestError: The bytes are not valid TOML.
    """
    try:
        return tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ManifestError(
            ManifestProblem.SYNTAX,
            detail="not valid UTF-8",
            manifest=path,
            lexical=lexical,
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(
            ManifestProblem.SYNTAX, detail=str(error), manifest=path, lexical=lexical
        ) from error


def _reject_unknown_keys(
    present: frozenset[str],
    allowed: frozenset[str],
    problem: ManifestProblem,
    path: Path,
    lexical: str,
) -> None:
    """Reject any key outside the allowed set.

    Args:
        present: Keys found in the document.
        allowed: Keys this schema version defines.
        problem: Which unknown-key rule applies.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.

    Raises:
        ManifestError: At least one key is not allowed.
    """
    unknown = sorted(present - allowed)
    if unknown:
        raise ManifestError(
            problem, detail=", ".join(unknown), manifest=path, lexical=lexical
        )


def _require(document: dict[str, object], key: str, path: Path, lexical: str) -> object:
    """Fetch a required key.

    Args:
        document: Decoded mapping.
        key: Key that must be present.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.

    Returns:
        The value.

    Raises:
        ManifestError: The key is absent.
    """
    if key not in document:
        raise ManifestError(
            ManifestProblem.MISSING_KEY, detail=key, manifest=path, lexical=lexical
        )
    value: object = document[key]
    return value


def _require_text(
    document: dict[str, object], key: str, path: Path, lexical: str
) -> str:
    """Fetch a required non-blank string.

    Args:
        document: Decoded mapping.
        key: Key that must hold a non-blank string.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.

    Returns:
        The string value.

    Raises:
        ManifestError: The value is missing, not a string, or blank.
    """
    value = _require(document, key, path, lexical)
    if not isinstance(value, str):
        raise ManifestError(
            ManifestProblem.WRONG_TYPE,
            detail=f"{key} is {type(value).__name__}, expected string",
            manifest=path,
            lexical=lexical,
        )
    if not value.strip():
        raise ManifestError(
            ManifestProblem.BLANK_VALUE, detail=key, manifest=path, lexical=lexical
        )
    return value


def _require_identifier(
    document: dict[str, object], key: str, path: Path, lexical: str
) -> str:
    """Fetch a required identifier.

    Args:
        document: Decoded mapping.
        key: Key that must hold an identifier.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.

    Returns:
        The identifier.

    Raises:
        ManifestError: The value is missing, not a string, blank, or does not match
            the identifier pattern.
    """
    value = _require_text(document, key, path, lexical)
    if IDENTIFIER_PATTERN.match(value) is None:
        raise ManifestError(
            ManifestProblem.BAD_IDENTIFIER,
            detail=f"{key}={value!r}",
            manifest=path,
            lexical=lexical,
        )
    return value


def _require_schema_version(
    document: dict[str, object], path: Path, lexical: str
) -> int:
    """Fetch and check ``schema_version``.

    Args:
        document: Decoded mapping.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.

    Returns:
        The schema version.

    Raises:
        ManifestError: The value is missing, not exactly an ``int``, or unsupported.
    """
    value = _require(document, "schema_version", path, lexical)
    # `type(...) is int` rather than isinstance: bool is an int subclass, and TOML
    # `true` must not satisfy an integer requirement.
    if type(value) is not int:
        raise ManifestError(
            ManifestProblem.WRONG_TYPE,
            detail=f"schema_version is {type(value).__name__}, expected integer",
            manifest=path,
            lexical=lexical,
        )
    if value != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            ManifestProblem.UNSUPPORTED_SCHEMA_VERSION,
            detail=f"{value}, expected {MANIFEST_SCHEMA_VERSION}",
            manifest=path,
            lexical=lexical,
        )
    return value


def _parse_modules(
    document: dict[str, object],
    path: Path,
    lexical: str,
    limits: BundleLimits,
) -> tuple[ModuleSpec, ...]:
    """Parse and resolve the module array.

    Args:
        document: Decoded mapping.
        path: Resolved manifest path.
        lexical: Manifest path as supplied.
        limits: Configured safeguards.

    Returns:
        Modules in manifest order.

    Raises:
        ManifestError: The array is missing, empty, wrongly typed, over the module
            limit, or contains a duplicate identifier or resolved path.
    """
    raw = _require(document, "modules", path, lexical)
    if not isinstance(raw, list) or not raw:
        raise ManifestError(ManifestProblem.NO_MODULES, manifest=path, lexical=lexical)
    # Narrowing cast justified by the isinstance check above: pyright narrows a
    # value of static type `object` to `list[Unknown]`, and this restates the
    # element type as `object` so every element is still isinstance-checked below.
    entries = cast("list[object]", raw)
    if len(entries) > limits.max_modules:
        raise ManifestError(
            ManifestProblem.TOO_MANY_MODULES,
            detail=f"{len(entries)} > {limits.max_modules}",
            manifest=path,
            lexical=lexical,
        )
    manifest_dir = path.parent
    specs: list[ModuleSpec] = []
    seen_ids: dict[str, int] = {}
    seen_paths: dict[Path, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ManifestError(
                ManifestProblem.MODULE_NOT_A_TABLE,
                detail=f"modules[{index}] is {type(entry).__name__}",
                manifest=path,
                lexical=lexical,
            )
        module = cast("dict[str, object]", entry)
        _reject_unknown_keys(
            frozenset(module),
            MODULE_KEYS,
            ManifestProblem.UNKNOWN_MODULE_KEY,
            path,
            lexical,
        )
        module_id = _require_identifier(module, "id", path, lexical)
        if module_id in seen_ids:
            raise ManifestError(
                ManifestProblem.DUPLICATE_MODULE_ID,
                detail=f"{module_id!r} at modules[{seen_ids[module_id]}] and modules[{index}]",
                manifest=path,
                lexical=lexical,
            )
        source_text = _require_text(module, "source", path, lexical)
        resolved = resolve_against(manifest_dir, source_text)
        if resolved in seen_paths:
            raise ManifestError(
                ManifestProblem.DUPLICATE_SOURCE_PATH,
                detail=f"{resolved} used by {seen_paths[resolved]!r} and {module_id!r}",
                manifest=path,
                lexical=lexical,
            )
        seen_ids[module_id] = index
        seen_paths[resolved] = module_id
        specs.append(
            ModuleSpec(id=module_id, lexical_source=source_text, source=resolved)
        )
    return tuple(specs)


def load_manifest(
    path: Path,
    *,
    lexical_path: str | None = None,
    limits: BundleLimits | None = None,
) -> BundleManifest:
    """Read, decode, validate, and path-resolve a manifest.

    Every schema version 1 violation propagates as a :class:`ManifestError` from the
    reading, decoding, and validation helpers this function composes.

    Args:
        path: Resolved manifest path.
        lexical_path: Manifest path as the operator supplied it. Defaults to the
            resolved path.
        limits: Configured safeguards. Defaults to :class:`BundleLimits`.

    Returns:
        The validated manifest with every path already resolved.
    """
    effective_limits = BundleLimits() if limits is None else limits
    lexical = str(path) if lexical_path is None else lexical_path
    data = _read_manifest_bytes(path, lexical, effective_limits)
    document = _decode(data, path, lexical)
    _reject_unknown_keys(
        frozenset(document),
        TOP_LEVEL_KEYS,
        ManifestProblem.UNKNOWN_KEY,
        path,
        lexical,
    )
    schema_version = _require_schema_version(document, path, lexical)
    bundle_id = _require_identifier(document, "bundle_id", path, lexical)
    default_target_text = _require_text(document, "default_target", path, lexical)
    modules = _parse_modules(document, path, lexical, effective_limits)
    return BundleManifest(
        schema_version=schema_version,
        bundle_id=bundle_id,
        default_target=resolve_against(path.parent, default_target_text),
        lexical_default_target=default_target_text,
        modules=modules,
        path=path,
        lexical_path=lexical,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
    )

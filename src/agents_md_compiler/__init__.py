"""Deterministic compiler for a single global ``AGENTS.md`` policy bundle.

The public surface is intentionally small. Pure computation (manifest parsing,
source validation, locking, rendering, state comparison) lives here; every
filesystem mutation lives in :mod:`agents_md_compiler.installation` so a library
consumer cannot confuse rendering with installing.

Typical read-only use::

    from pathlib import Path
    from agents_md_compiler import compile_bundle, load_manifest

    manifest = load_manifest(Path("global-agents.toml").absolute())
    compiled = compile_bundle(manifest)
    rendered_bytes = compiled.rendered.data
"""

from agents_md_compiler._version import distribution_version
from agents_md_compiler.codex import (
    CodexCapability,
    RuntimeVerification,
    detect_capability,
    verify_rendered_visibility,
)
from agents_md_compiler.errors import (
    CodexVerificationError,
    CompilerError,
    ConcurrentChangeError,
    LockError,
    LockMissingError,
    LockStaleError,
    ManifestError,
    MutationError,
    OutputExistsError,
    ReceiptError,
    RenderError,
    ShadowedError,
    SourceError,
    TargetError,
    UnmanagedTargetError,
    UsageError,
)
from agents_md_compiler.lockfile import build_lock, load_lock, serialize_lock
from agents_md_compiler.manifest import load_manifest
from agents_md_compiler.models import (
    LOCK_FORMAT_VERSION,
    MANIFEST_SCHEMA_VERSION,
    RECEIPT_SCHEMA_VERSION,
    RENDER_FORMAT_VERSION,
    BundleLimits,
    BundleLock,
    BundleManifest,
    BundleState,
    BundleStatus,
    CompiledBundle,
    LockedModule,
    ModuleSpec,
    OverrideInspection,
    RenderedBundle,
    SourceSnapshot,
    TargetInspection,
    TargetKind,
)
from agents_md_compiler.rendering import render_bundle, validate_rendered
from agents_md_compiler.sources import read_source, read_sources
from agents_md_compiler.state import (
    compile_bundle,
    evaluate,
    inspect_override,
    inspect_target,
)

__all__ = [
    "LOCK_FORMAT_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "RENDER_FORMAT_VERSION",
    "BundleLimits",
    "BundleLock",
    "BundleManifest",
    "BundleState",
    "BundleStatus",
    "CodexCapability",
    "CodexVerificationError",
    "CompiledBundle",
    "CompilerError",
    "ConcurrentChangeError",
    "LockError",
    "LockMissingError",
    "LockStaleError",
    "LockedModule",
    "ManifestError",
    "ModuleSpec",
    "MutationError",
    "OutputExistsError",
    "OverrideInspection",
    "ReceiptError",
    "RenderError",
    "RenderedBundle",
    "RuntimeVerification",
    "ShadowedError",
    "SourceError",
    "SourceSnapshot",
    "TargetError",
    "TargetInspection",
    "TargetKind",
    "UnmanagedTargetError",
    "UsageError",
    "__version__",
    "build_lock",
    "compile_bundle",
    "detect_capability",
    "distribution_version",
    "evaluate",
    "inspect_override",
    "inspect_target",
    "load_lock",
    "load_manifest",
    "read_source",
    "read_sources",
    "render_bundle",
    "serialize_lock",
    "validate_rendered",
    "verify_rendered_visibility",
]

__version__ = distribution_version()

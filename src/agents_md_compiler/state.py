"""Bundle compilation and read-only state evaluation.

This module orchestrates the read-only half of the tool: parse, read sources, build
the lock, render, inspect the target, and report the first applicable state.

Nothing here writes a file. A genuinely malformed input raises, because there is
nothing to report about it. A *difference* is returned as a state, so ``status`` can
present the full picture, including override and receipt information, instead of
failing at the first disagreement.
"""

import stat
from pathlib import Path

from agents_md_compiler.errors import (
    ManifestError,
    ManifestProblem,
    ShadowedError,
    TargetError,
    TargetProblem,
)
from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.lockfile import (
    build_lock,
    compare_locks,
    parse_lock,
    read_lock_bytes,
    serialize_lock,
)
from agents_md_compiler.models import (
    GLOBAL_TARGET_FILENAME,
    OVERRIDE_FILENAME,
    RENDER_FORMAT_VERSION,
    BundleLimits,
    BundleManifest,
    BundleState,
    BundleStatus,
    CompiledBundle,
    OverrideInspection,
    TargetInspection,
    TargetKind,
)
from agents_md_compiler.rendering import (
    declared_format,
    render_bundle,
    validate_rendered,
)
from agents_md_compiler.sources import read_sources


def compile_bundle(
    manifest: BundleManifest,
    *,
    limits: BundleLimits | None = None,
    target: Path | None = None,
) -> CompiledBundle:
    """Read every source, build the lock, and render, without mutating anything.

    A source failure propagates as :class:`SourceError` and a structural rendering
    failure as :class:`RenderError`.

    Args:
        manifest: The parsed manifest.
        limits: Configured safeguards. Defaults to :class:`BundleLimits`.
        target: Effective target path, used only to refuse a manifest whose source
            aliases the output.

    Returns:
        The compiled bundle.

    Raises:
        ManifestError: A source resolves to the effective output target.
    """
    effective_target = manifest.default_target if target is None else target
    for spec in manifest.modules:
        if spec.source == effective_target:
            raise ManifestError(
                ManifestProblem.TARGET_ALIASES_SOURCE,
                detail=f"module {spec.id!r} source is {effective_target}",
                manifest=manifest.path,
                lexical=manifest.lexical_path,
            )
    snapshots = read_sources(manifest.modules, limits=limits)
    lock = build_lock(manifest, snapshots)
    lock_bytes = serialize_lock(lock)
    digest = sha256_bytes(lock_bytes)
    rendered = render_bundle(
        bundle_id=manifest.bundle_id,
        manifest_sha256=manifest.sha256,
        lock_sha256=digest,
        snapshots=snapshots,
    )
    validate_rendered(rendered.data, lock, digest)
    return CompiledBundle(
        manifest=manifest,
        lock=lock,
        lock_bytes=lock_bytes,
        lock_sha256=digest,
        rendered=rendered,
    )


def inspect_target(path: Path, *, lexical: str | None = None) -> TargetInspection:
    """Inspect a target without following a link and without mutating it.

    Args:
        path: Resolved target path.
        lexical: Target path as supplied.

    Returns:
        What the filesystem says about the target.

    Raises:
        TargetError: The path is a symbolic link, or exists and is not a regular
            file, or could not be read.
    """
    reference = str(path) if lexical is None else lexical
    if path.is_symlink():
        raise TargetError(TargetProblem.SYMLINK, target=path, lexical=reference)
    try:
        result = path.stat()
    except FileNotFoundError:
        return TargetInspection(
            path=path,
            lexical_path=reference,
            kind=TargetKind.MISSING,
            sha256=None,
            size_bytes=None,
            mode=None,
            is_symlink=False,
            declared_format=None,
        )
    except OSError as error:
        raise TargetError(
            TargetProblem.UNREADABLE, target=path, lexical=reference
        ) from error
    if not stat.S_ISREG(result.st_mode):
        raise TargetError(TargetProblem.NOT_A_FILE, target=path, lexical=reference)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise TargetError(
            TargetProblem.UNREADABLE, target=path, lexical=reference
        ) from error
    format_value = declared_format(data)
    kind = (
        TargetKind.MANAGED
        if format_value == RENDER_FORMAT_VERSION
        else TargetKind.UNMANAGED
    )
    return TargetInspection(
        path=path,
        lexical_path=reference,
        kind=kind,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
        mode=stat.S_IMODE(result.st_mode),
        is_symlink=False,
        declared_format=format_value,
    )


def require_target_parent(path: Path, *, lexical: str | None = None) -> None:
    """Require a target's parent to exist and be a directory.

    This is a preflight check, not a mutation. Destination commands never create a
    target parent implicitly because doing so would broaden the operator-selected
    mutation boundary.

    Args:
        path: Resolved target or output path.
        lexical: Path as supplied.

    Raises:
        TargetError: The parent is missing, is not a directory, or cannot be read.
    """
    reference = str(path) if lexical is None else lexical
    parent = path.parent
    try:
        result = parent.stat()
    except FileNotFoundError as error:
        raise TargetError(
            TargetProblem.PARENT_MISSING, target=path, lexical=reference
        ) from error
    except OSError as error:
        raise TargetError(
            TargetProblem.UNREADABLE, target=path, lexical=reference
        ) from error
    if not stat.S_ISDIR(result.st_mode):
        raise TargetError(
            TargetProblem.PARENT_NOT_A_DIRECTORY,
            target=path,
            lexical=reference,
        )


def inspect_override(target: Path) -> OverrideInspection:
    """Detect a shadowing global override beside the target.

    Shadowing applies only when the target is named ``AGENTS.md``, because that is
    the only case where Codex would prefer a sibling ``AGENTS.override.md``.

    Args:
        target: Resolved target path.

    Returns:
        The override path that would apply and whether it is present and non-empty.
    """
    if target.name != GLOBAL_TARGET_FILENAME:
        return OverrideInspection(path=None, present=False)
    override = target.parent / OVERRIDE_FILENAME
    try:
        size = override.stat().st_size
    except OSError:
        return OverrideInspection(path=override, present=False)
    return OverrideInspection(path=override, present=size > 0)


def evaluate(
    manifest: BundleManifest,
    *,
    lock_path: Path,
    lock_lexical: str | None = None,
    target: Path | None = None,
    target_lexical: str | None = None,
    limits: BundleLimits | None = None,
) -> BundleStatus:
    """Compute the full read-only picture and the first applicable state.

    Precedence is manifest and source invalidity, then lock invalidity, absence, or
    staleness, then shadowing, then the target's own state. The first two raise; the
    rest are returned so a caller can report every detail.

    Propagated failures: :class:`ManifestError` for an invalid manifest or an output
    alias, :class:`SourceError` for an invalid source, :class:`LockError` for a
    malformed existing lock, :class:`RenderError` for a structural rendering failure,
    and :class:`TargetError` for a target path that cannot be used safely.

    Args:
        manifest: The parsed manifest.
        lock_path: Resolved lock path.
        lock_lexical: Lock path as supplied.
        target: Effective target path. Defaults to the manifest's default target.
        target_lexical: Target path as supplied.
        limits: Configured safeguards.

    Returns:
        The evaluated status.
    """
    effective_target = manifest.default_target if target is None else target
    compiled = compile_bundle(manifest, limits=limits, target=effective_target)
    override = inspect_override(effective_target)

    lock_present = lock_path.exists() or lock_path.is_symlink()
    if not lock_present:
        return BundleStatus(
            state=BundleState.LOCK_MISSING,
            compiled=compiled,
            target=None,
            override=override,
            lock_present=False,
            lock_matches=False,
        )
    on_disk_bytes = read_lock_bytes(lock_path, lexical=lock_lexical)
    on_disk = parse_lock(on_disk_bytes, lock=lock_path, lexical=lock_lexical)
    difference = compare_locks(on_disk, compiled.lock)
    # Byte equality, not just structural equality. The rendered header records the
    # digest of the *canonical* lock bytes, so a lock whose content is equivalent but
    # whose serialization differs would make the file's own digest disagree with the
    # header an operator reads. Refusing that keeps one lock file to one digest.
    if difference is not None or on_disk_bytes != compiled.lock_bytes:
        return BundleStatus(
            state=BundleState.LOCK_STALE,
            compiled=compiled,
            target=None,
            override=override,
            lock_present=True,
            lock_matches=False,
        )
    if override.present:
        return BundleStatus(
            state=BundleState.SHADOWED,
            compiled=compiled,
            target=None,
            override=override,
            lock_present=True,
            lock_matches=True,
        )
    inspection = inspect_target(effective_target, lexical=target_lexical)
    if inspection.kind is TargetKind.MISSING:
        state = BundleState.MISSING
    elif inspection.kind is TargetKind.UNMANAGED:
        state = BundleState.UNMANAGED_TARGET
    elif inspection.sha256 == compiled.rendered.sha256:
        state = BundleState.CURRENT
    else:
        state = BundleState.DRIFTED
    return BundleStatus(
        state=state,
        compiled=compiled,
        target=inspection,
        override=override,
        lock_present=True,
        lock_matches=True,
    )


def require_not_shadowed(override: OverrideInspection, target: Path) -> None:
    """Refuse an operation when a non-empty override would shadow the target.

    Args:
        override: Override inspection for this target.
        target: Resolved target path.

    Raises:
        ShadowedError: A non-empty override exists.
    """
    if override.present and override.path is not None:
        raise ShadowedError(override=override.path, target=target)

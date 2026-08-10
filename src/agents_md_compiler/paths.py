"""Deterministic path resolution and platform state-root selection.

This module is the only place that reads environment variables or the user's home
directory, so path policy lives in one reviewable place instead of being spread
through domain code.

Resolution is *lexical*: a joined path is normalized textually and never passed
through ``realpath``. That is deliberate. Following links during resolution would
defeat the symlink refusal that source and target validation depend on, and it
would make a duplicate-path check depend on the filesystem's link graph rather than
on the reviewed manifest.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from agents_md_compiler.hashing import sha256_bytes
from agents_md_compiler.models import DISTRIBUTION_DIRECTORY, LOCK_SUFFIX

WINDOWS_STATE_ENV = "LOCALAPPDATA"
"""Environment variable naming the private per-user data directory on Windows."""

XDG_STATE_ENV = "XDG_STATE_HOME"
"""Environment variable overriding the POSIX user state root."""

POSIX_STATE_FALLBACK = ".local/state"
"""Home-relative POSIX state root used when ``XDG_STATE_HOME`` is unset."""

STATE_V2_DIRNAME = "_v2"
"""Namespace that cannot collide with a valid legacy bundle identifier."""

DEPLOYMENTS_DIRNAME = "deployments"
"""Distribution state subdirectory holding target-qualified evidence."""

SHARED_LOCKS_DIRNAME = "locks"
"""Distribution state subdirectory holding cross-bundle advisory locks."""


def expand_leading_tilde(value: str) -> str:
    """Expand only a leading ``~`` or ``~user`` component.

    Nothing else is expanded: no environment variables, no command substitution, no
    globbing. ``$HOME/x.md`` stays a literal relative path with a ``$HOME``
    directory component.

    Args:
        value: Operator-supplied path text.

    Returns:
        The same text with any leading tilde component expanded.
    """
    if not value.startswith("~"):
        return value
    # PTH111 is suppressed below: Path.expanduser() raises RuntimeError when a
    # "~user" home cannot be resolved, while os.path.expanduser leaves the
    # component literal. Leaving it literal is the documented contract here, so the
    # pathlib replacement would change observable behavior.
    return os.path.expanduser(value)  # noqa: PTH111


def resolve_against(base: Path, value: str) -> Path:
    """Resolve a path value against an explicit base directory.

    Args:
        base: Directory that a relative value is resolved against.
        value: Operator-supplied path text.

    Returns:
        An absolute, textually normalized path. Symbolic links are not followed.
    """
    expanded = expand_leading_tilde(value)
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = base / candidate
    return Path(os.path.normpath(candidate))


def resolve_from_cwd(value: str, *, cwd: Path | None = None) -> Path:
    """Resolve an explicit command-line path against the working directory.

    Args:
        value: Operator-supplied path text.
        cwd: Working directory to use. ``None`` reads the process directory.

    Returns:
        An absolute, textually normalized path.
    """
    base = Path.cwd() if cwd is None else cwd
    return resolve_against(base, value)


def default_lock_path(manifest: Path) -> Path:
    """Derive the default lock path for a manifest.

    Args:
        manifest: Resolved manifest path.

    Returns:
        The manifest path with ``.lock.json`` appended, so
        ``agents-md.toml`` pairs with ``agents-md.toml.lock.json``.
    """
    return Path(str(manifest) + LOCK_SUFFIX)


def user_state_root(*, environ: Mapping[str, str] | None = None) -> Path:
    """Select the platform-appropriate user state root.

    On Windows this is the private per-user local application data directory. On
    POSIX systems it honors ``XDG_STATE_HOME`` and otherwise falls back to
    ``~/.local/state``.

    Args:
        environ: Environment mapping to read. ``None`` reads the process
            environment.

    Returns:
        The state root directory for this distribution, which may not exist yet.
    """
    env = os.environ if environ is None else environ
    if sys.platform == "win32":
        local_app_data = env.get(WINDOWS_STATE_ENV, "")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base / DISTRIBUTION_DIRECTORY
    configured = env.get(XDG_STATE_ENV, "")
    base = Path(configured) if configured else Path.home() / POSIX_STATE_FALLBACK
    return base / DISTRIBUTION_DIRECTORY


def bundle_state_dir(bundle_id: str, *, state_root: Path | None = None) -> Path:
    """Locate the legacy per-bundle state directory.

    New operational evidence uses :func:`deployment_state_dir`. This helper stays
    public so a version-1 receipt can be validated and rolled back in place.

    Args:
        bundle_id: Validated bundle identifier.
        state_root: Override for the distribution state root, used by tests so no
            test ever touches a real user configuration path.

    Returns:
        The legacy per-bundle state directory, which may not exist yet.
    """
    root = user_state_root() if state_root is None else state_root
    return root / bundle_id


def shared_lock_dir(*, state_root: Path | None = None) -> Path:
    """Locate the distribution-wide advisory-lock directory.

    Args:
        state_root: Override for the distribution state root.

    Returns:
        The shared lock directory, which may not exist yet.
    """
    root = user_state_root() if state_root is None else state_root
    return root / STATE_V2_DIRNAME / SHARED_LOCKS_DIRNAME


def deployment_state_dir(
    bundle_id: str, target: Path, *, state_root: Path | None = None
) -> Path:
    """Locate operational evidence for one bundle and resolved target.

    Args:
        bundle_id: Validated bundle identifier.
        target: Absolute normalized target path.
        state_root: Override for the distribution state root.

    Returns:
        The target-qualified deployment directory, which may not exist yet.
    """
    root = user_state_root() if state_root is None else state_root
    target_digest = sha256_bytes(str(target).encode("utf-8"))
    return root / STATE_V2_DIRNAME / DEPLOYMENTS_DIRNAME / bundle_id / target_digest


def is_within(candidate: Path, root: Path) -> bool:
    """Report whether a path is inside a root directory.

    Both paths are compared after textual normalization, so this answers a
    containment question about the reviewed path strings rather than about the
    filesystem's link graph.

    Args:
        candidate: Path to test.
        root: Directory that must contain it.

    Returns:
        ``True`` when ``candidate`` equals ``root`` or lies beneath it.
    """
    normalized_candidate = Path(os.path.normpath(candidate))
    normalized_root = Path(os.path.normpath(root))
    if normalized_candidate == normalized_root:
        return True
    return normalized_root in normalized_candidate.parents

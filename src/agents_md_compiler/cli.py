"""Command-line adapter.

The CLI is a thin shell over the typed library results: it parses arguments,
resolves paths against their documented bases, selects an output format, and maps a
typed outcome onto a stable process exit code. It contains no policy logic.

Stream contract: requested output goes to stdout, diagnostics go to stderr. In JSON
mode stdout carries exactly one JSON object. ``render`` writes raw bytes to stdout
with nothing prepended or appended, so the output can be piped or hashed directly.

``--format`` and ``--quiet`` are accepted both before and after the subcommand. An
option given after the subcommand wins, because that is the position an operator
reaches for when overriding a shell alias or wrapper script.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agents_md_compiler._version import distribution_version
from agents_md_compiler.atomic import atomic_write, create_state_directory
from agents_md_compiler.codex import (
    CODEX_HOME_ENV,
    DEFAULT_TIMEOUT_SECONDS,
    RuntimeVerification,
    VerificationContext,
    active_codex_home,
    content_sentinels,
    required_markers,
    unverified,
    verification_context_for_target,
    verify_rendered_visibility,
)
from agents_md_compiler.errors import (
    CodexVerificationError,
    CompilerError,
    ConcurrentChangeError,
    ConcurrentChangeProblem,
    LockMissingError,
    OutputExistsError,
    UsageError,
)
from agents_md_compiler.installation import install_bundle, rollback_install
from agents_md_compiler.lockfile import read_lock_bytes
from agents_md_compiler.locking import advisory_lock, lock_path_for
from agents_md_compiler.manifest import load_manifest
from agents_md_compiler.models import (
    AGENTS_FILENAME,
    IDENTIFIER_PATTERN,
    JSON_SCHEMA_VERSION,
    NEW_TARGET_MODE,
    SHA256_PATTERN,
    STATE_DIR_MODE,
    BundleManifest,
    BundleState,
    BundleStatus,
    CompiledBundle,
    PathPair,
)
from agents_md_compiler.paths import (
    bundle_state_dir,
    default_lock_path,
    deployment_state_dir,
    is_within,
    resolve_from_cwd,
    shared_lock_dir,
)
from agents_md_compiler.receipts import (
    list_backups,
    list_receipts,
    load_install_receipt,
)
from agents_md_compiler.state import compile_bundle, evaluate, require_target_parent

PROGRAM_NAME = "agents-md-compiler"
"""Console command name, also used in diagnostics."""

DEFAULT_MANIFEST_NAME = "agents-md.toml"
"""Manifest name assumed when ``--manifest`` is omitted."""

LEGACY_DEFAULT_MANIFEST_NAME = "global-agents.toml"
"""Legacy default used only when the neutral default is absent."""

OPTION_BUNDLE_ID = "--bundle-id"
OPTION_EXPECT_DIGEST = "--expect-target-sha256"
OPTION_REPLACE_UNMANAGED = "--replace-unmanaged"
"""Option names are named once so a diagnostic and the parser cannot disagree."""

EXIT_OK = 0
"""Operation succeeded."""

EXIT_ERROR = 1
"""Invalid invocation, invalid input, or an I/O or runtime failure."""

EXIT_DIFFERENCE = 2
"""A read-only command found a difference the operator must resolve."""

EXIT_REFUSAL = 3
"""A safety precondition refused the operation."""

FORMAT_TEXT = "text"
FORMAT_JSON = "json"
_FORMAT_CHOICES = (FORMAT_TEXT, FORMAT_JSON)

STATE_EXIT_CODES: dict[BundleState, int] = {
    BundleState.CURRENT: EXIT_OK,
    BundleState.INVALID_MANIFEST: EXIT_ERROR,
    BundleState.INVALID_LOCK: EXIT_ERROR,
    BundleState.INVALID_SOURCE: EXIT_ERROR,
    BundleState.RUNTIME_UNVERIFIED: EXIT_ERROR,
    BundleState.LOCK_MISSING: EXIT_DIFFERENCE,
    BundleState.LOCK_STALE: EXIT_DIFFERENCE,
    BundleState.DRIFTED: EXIT_DIFFERENCE,
    BundleState.MISSING: EXIT_DIFFERENCE,
    BundleState.SHADOWED: EXIT_REFUSAL,
    BundleState.UNMANAGED_TARGET: EXIT_REFUSAL,
    BundleState.CONCURRENT_CHANGE: EXIT_REFUSAL,
}
"""The exhaustive state-to-exit-code mapping documented in docs/cli-contract.md."""

SCAFFOLD_MANIFEST = """\
# Bundle manifest. Module order is output order and every module is
# mandatory. Sources resolve against this file's directory, never the process
# working directory. Run the exact lock command printed by `init` next.
schema_version = 1
bundle_id = "{bundle_id}"
default_target = {default_target}

[[modules]]
id = "core"
source = "modules/core.md"

[[modules]]
id = "python"
source = "modules/python.md"
"""

SCAFFOLD_CORE = """\
# Core Working Agreement

Apply these rules to every task, before task scope is known.

- Prefer the smallest correct change that satisfies the request.
- State assumptions explicitly rather than guessing silently.
- Report every check that failed, was skipped, or was unavailable.
"""

SCAFFOLD_PYTHON = """\
# Python Rules

Apply these rules when the task edits Python.

- Type every public boundary, including an explicit return type.
- Let the configured formatter own layout; do not hand-align code.
- Validate untrusted input at the boundary, not throughout the call graph.
"""


def _subcommand_options() -> argparse.ArgumentParser:
    """Build the option parser inherited by every subcommand.

    Defaults are ``None`` so an unset subcommand flag can be distinguished from an
    explicit one and resolved against the top-level value.

    Returns:
        A parser suitable for use as an ``argparse`` parent.
    """
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--format",
        dest="subcommand_format",
        choices=_FORMAT_CHOICES,
        default=None,
        help="output format; text is human readable, json emits one object",
    )
    shared.add_argument(
        "--quiet",
        dest="subcommand_quiet",
        action="store_const",
        const=True,
        default=None,
        help="suppress non-error stderr; never suppresses JSON or render output",
    )
    return shared


def _add_bundle_options(parser: argparse.ArgumentParser, *, target: bool) -> None:
    """Add the manifest, lock, and optional target options to a subcommand.

    Args:
        parser: Subcommand parser.
        target: Whether this subcommand resolves a target.
    """
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"manifest path; defaults to ./{DEFAULT_MANIFEST_NAME}",
    )
    parser.add_argument(
        "--lock",
        default=None,
        help="lock path; defaults to the manifest path plus .lock.json",
    )
    if target:
        parser.add_argument(
            "--target",
            default=None,
            help="target path; defaults to the manifest's default_target",
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the complete argument parser.

    Returns:
        The configured top-level parser.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=(
            "Compile ordered Markdown policy modules into one deterministic "
            "AGENTS.md, then check, install, roll back, and verify it."
        ),
    )
    parser.add_argument(
        "--format",
        dest="global_format",
        choices=_FORMAT_CHOICES,
        default=None,
        help="output format; text is human readable, json emits one object",
    )
    parser.add_argument(
        "--quiet",
        dest="global_quiet",
        action="store_const",
        const=True,
        default=None,
        help="suppress non-error stderr; never suppresses JSON or render output",
    )
    parser.add_argument(
        "--version",
        dest="version_flag",
        action="store_true",
        help="print the installed distribution version and exit",
    )
    shared = _subcommand_options()
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")

    initializer = subcommands.add_parser(
        "init",
        parents=[shared],
        help="scaffold an example manifest and modules",
        description="Scaffold an example manifest and modules. Refuses every existing target.",
    )
    initializer.add_argument(
        "--directory", default=".", help="directory to scaffold into; defaults to ."
    )
    initializer.add_argument(
        OPTION_BUNDLE_ID,
        default="example-bundle",
        help="bundle identifier for the scaffolded manifest",
    )
    initializer.add_argument(
        "--target",
        default=None,
        help="target recorded in the manifest; defaults to the active Codex home AGENTS.md",
    )

    locker = subcommands.add_parser(
        "lock",
        parents=[shared],
        help="validate sources and write the deterministic lock",
        description="Validate every source and atomically write the deterministic lock.",
    )
    _add_bundle_options(locker, target=False)
    locker.add_argument(
        "--check",
        dest="check_only",
        action="store_true",
        help="read-only: exit nonzero if a fresh lock would differ",
    )

    validator = subcommands.add_parser(
        "validate",
        parents=[shared],
        help="validate manifest, lock, sources, and rendered structure",
        description="Validate the manifest, the lock, every source, and the rendered structure.",
    )
    _add_bundle_options(validator, target=True)

    renderer = subcommands.add_parser(
        "render",
        parents=[shared],
        help="emit rendered bytes to stdout or an explicit new path",
        description="Emit rendered bytes to stdout, or to an explicit path that must not exist.",
    )
    _add_bundle_options(renderer, target=False)
    renderer.add_argument(
        "--locked",
        action="store_true",
        help="require the on-disk lock to equal a freshly serialized lock",
    )
    renderer.add_argument(
        "--output", default=None, help="write to this path, which must not exist"
    )

    checker = subcommands.add_parser(
        "check",
        parents=[shared],
        help="compare a fresh locked render with the target",
        description="Compare a fresh locked render with the resolved target.",
    )
    _add_bundle_options(checker, target=True)

    reporter = subcommands.add_parser(
        "status",
        parents=[shared],
        help="report source, lock, target, override, and receipt state",
        description="Report source, lock, target, override, and receipt state.",
    )
    _add_bundle_options(reporter, target=True)

    installer = subcommands.add_parser(
        "install",
        parents=[shared],
        help="back up and atomically install, only with --apply",
        description="Back up and atomically install the rendered bundle. Requires --apply.",
    )
    _add_bundle_options(installer, target=True)
    installer.add_argument(
        "--apply", action="store_true", help="perform the mutation; otherwise dry run"
    )
    installer.add_argument(
        OPTION_REPLACE_UNMANAGED,
        action="store_true",
        help="permit replacing a target with no recognized generated header",
    )
    installer.add_argument(
        OPTION_EXPECT_DIGEST,
        default=None,
        help="digest captured immediately before the dry run",
    )

    reverter = subcommands.add_parser(
        "rollback",
        parents=[shared],
        help="restore one receipt, only with --apply",
        description="Restore the bytes one install replaced. Requires --apply.",
    )
    reverter.add_argument("--receipt", required=True, help="install receipt to restore")
    _add_bundle_options(reverter, target=True)
    reverter.add_argument(
        "--apply", action="store_true", help="perform the mutation; otherwise dry run"
    )

    verifier = subcommands.add_parser(
        "verify-codex",
        parents=[shared],
        help="inspect Codex model-visible startup input",
        description=(
            "Confirm the installed bundle is visible in Codex startup input, head "
            "to tail. Sends no model request and requires no API authentication."
        ),
    )
    _add_bundle_options(verifier, target=True)
    verifier.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="prompt-input deadline in seconds; capability checks use 60",
    )
    verifier.add_argument(
        "--cwd",
        default=None,
        help="Codex startup directory; project targets default to target parent",
    )

    subcommands.add_parser(
        "version",
        parents=[shared],
        help="print the installed distribution version",
        description="Print the installed distribution version.",
    )
    return parser


def resolve_format(args: argparse.Namespace) -> str:
    """Resolve the effective output format.

    Args:
        args: Parsed arguments.

    Returns:
        Either ``text`` or ``json``.
    """
    subcommand_value: str | None = getattr(args, "subcommand_format", None)
    if subcommand_value is not None:
        return subcommand_value
    global_value: str | None = args.global_format
    if global_value is not None:
        return global_value
    return FORMAT_TEXT


def resolve_quiet(args: argparse.Namespace) -> bool:
    """Resolve whether non-error stderr is suppressed.

    Args:
        args: Parsed arguments.

    Returns:
        ``True`` when non-error diagnostics must be withheld.
    """
    subcommand_value: bool | None = getattr(args, "subcommand_quiet", None)
    if subcommand_value is not None:
        return subcommand_value
    return args.global_quiet is True


def emit_json(payload: dict[str, Any]) -> None:
    """Write exactly one JSON object to stdout.

    Args:
        payload: Envelope to serialize.
    """
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def _note(args: argparse.Namespace, message: str) -> None:
    """Write a non-error diagnostic to stderr unless suppressed.

    Args:
        args: Parsed arguments.
        message: Diagnostic text.
    """
    if resolve_quiet(args) or resolve_format(args) == FORMAT_JSON:
        return
    sys.stderr.write(message + "\n")


def _envelope(command: str, state: BundleState | None, *, ok: bool) -> dict[str, Any]:
    """Start a JSON envelope with the fields every response carries.

    Args:
        command: Subcommand name.
        state: Reported state, or ``None`` when no lock or target state applies.
        ok: Whether the command achieved its purpose.

    Returns:
        The envelope base.
    """
    return {
        "command": command,
        "ok": ok,
        "schema_version": JSON_SCHEMA_VERSION,
        "state": None if state is None else state.value,
    }


def _manifest_path(args: argparse.Namespace) -> Path:
    """Resolve the manifest path from the working directory.

    Args:
        args: Parsed arguments.

    Returns:
        The resolved manifest path.

    Raises:
        UsageError: Both neutral and legacy default manifests exist.
    """
    supplied: str | None = args.manifest
    if supplied is not None:
        return resolve_from_cwd(supplied)
    current = resolve_from_cwd(DEFAULT_MANIFEST_NAME)
    legacy = resolve_from_cwd(LEGACY_DEFAULT_MANIFEST_NAME)
    current_present = current.exists() or current.is_symlink()
    legacy_present = legacy.exists() or legacy.is_symlink()
    if current_present and legacy_present:
        detail = (
            f"both {DEFAULT_MANIFEST_NAME!r} and "
            f"{LEGACY_DEFAULT_MANIFEST_NAME!r} exist; use --manifest"
        )
        raise UsageError(detail)
    if legacy_present:
        return legacy
    return current


def _lock_paths(args: argparse.Namespace, manifest: Path) -> tuple[Path, str]:
    """Resolve the lock path and the form the operator supplied.

    Args:
        args: Parsed arguments.
        manifest: Resolved manifest path.

    Returns:
        The resolved lock path and its lexical form.
    """
    supplied: str | None = args.lock
    if supplied is None:
        derived = default_lock_path(manifest)
        return derived, str(derived)
    return resolve_from_cwd(supplied), supplied


def _target_paths(
    args: argparse.Namespace, manifest: BundleManifest
) -> tuple[Path, str]:
    """Resolve the target path and the form the operator supplied.

    Args:
        args: Parsed arguments.
        manifest: The parsed manifest, whose default applies when none is supplied.

    Returns:
        The resolved target path and its lexical form.
    """
    supplied: str | None = getattr(args, "target", None)
    if supplied is None:
        return manifest.default_target, manifest.lexical_default_target
    return resolve_from_cwd(supplied), supplied


def _module_payload(compiled: CompiledBundle) -> list[dict[str, Any]]:
    """Render locked module identity for a JSON envelope.

    Args:
        compiled: The compiled bundle.

    Returns:
        One object per module, in manifest order.
    """
    return [
        {"id": module.id, "sha256": module.sha256, "size_bytes": module.size_bytes}
        for module in compiled.lock.modules
    ]


def _bundle_payload(
    compiled: CompiledBundle, lock_path: Path, target: Path | None
) -> dict[str, Any]:
    """Render the identity fields shared by the read-only commands.

    Args:
        compiled: The compiled bundle.
        lock_path: Resolved lock path.
        target: Resolved target path, or ``None`` when a command takes none.

    Returns:
        The shared payload fields.
    """
    return {
        "bundle_id": compiled.manifest.bundle_id,
        "manifest_path": str(compiled.manifest.path),
        "lock_path": str(lock_path),
        "manifest_sha256": compiled.manifest.sha256,
        "lock_sha256": compiled.lock_sha256,
        "output_sha256": compiled.rendered.sha256,
        "output_bytes": compiled.rendered.size_bytes,
        "modules": _module_payload(compiled),
        "target_path": None if target is None else str(target),
    }


def _evaluate(args: argparse.Namespace) -> tuple[BundleStatus, Path, Path]:
    """Run a read-only evaluation for a subcommand.

    Args:
        args: Parsed arguments.

    Returns:
        The status, the resolved lock path, and the resolved target path.
    """
    manifest_path = _manifest_path(args)
    manifest = load_manifest(
        manifest_path, lexical_path=args.manifest or str(manifest_path)
    )
    lock_path, lock_lexical = _lock_paths(args, manifest_path)
    target, target_lexical = _target_paths(args, manifest)
    status = evaluate(
        manifest,
        lock_path=lock_path,
        lock_lexical=lock_lexical,
        target=target,
        target_lexical=target_lexical,
    )
    return status, lock_path, target


def _report_state(
    args: argparse.Namespace,
    command: str,
    status: BundleStatus,
    lock_path: Path,
    target: Path,
    extra: dict[str, Any] | None = None,
) -> int:
    """Emit a read-only result and map its state to an exit code.

    Args:
        args: Parsed arguments.
        command: Subcommand name.
        status: The evaluated status.
        lock_path: Resolved lock path.
        target: Resolved target path.
        extra: Additional command-specific fields.

    Returns:
        The exit code for the reported state.
    """
    code = STATE_EXIT_CODES[status.state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope(command, status.state, ok=code == EXIT_OK)
        payload.update(_bundle_payload(status.compiled, lock_path, target))
        payload["target_sha256"] = (
            None if status.target is None else status.target.sha256
        )
        if extra is not None:
            payload.update(extra)
        emit_json(payload)
    else:
        sys.stdout.write(f"{status.state.value}\n")
        if code != EXIT_OK:
            sys.stderr.write(f"{PROGRAM_NAME}: {command}: {status.state.value}\n")
    return code


def _run_version(args: argparse.Namespace) -> int:
    """Report the installed distribution version.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK``.
    """
    installed = distribution_version()
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("version", None, ok=True)
        payload["version"] = installed
        emit_json(payload)
    else:
        sys.stdout.write(installed + "\n")
    return EXIT_OK


def _scaffold_target(raw_target: str, *, directory: Path) -> tuple[Path, str]:
    """Resolve and serialize a target for a scaffold manifest.

    Args:
        raw_target: Target text supplied to ``init``.
        directory: Resolved scaffold directory.

    Returns:
        The resolved target and portable manifest value.
    """
    target = resolve_from_cwd(raw_target)
    if raw_target.startswith("~"):
        return target, raw_target
    if Path(raw_target).is_absolute():
        return target, str(target)
    return target, os.path.relpath(target, directory)


def _default_scaffold_target() -> str:
    """Select the active global base target for ``init``.

    Preserve the portable tilde form for the ordinary default. When ``CODEX_HOME``
    is explicit, honor it exactly as Codex does and serialize its normalized
    absolute base target.

    Returns:
        Target text to resolve and write into the scaffold manifest.
    """
    if os.environ.get(CODEX_HOME_ENV, ""):
        return str(active_codex_home() / AGENTS_FILENAME)
    return "~/.codex/AGENTS.md"


def _run_init(args: argparse.Namespace) -> int:
    """Scaffold an example manifest and modules.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` when every file was created.

    Raises:
        UsageError: The bundle identifier is not a valid identifier.
        OutputExistsError: A scaffold target already exists.
    """
    bundle_id: str = args.bundle_id
    if IDENTIFIER_PATTERN.match(bundle_id) is None:
        problem = f"{OPTION_BUNDLE_ID} {bundle_id!r} must match [a-z][a-z0-9-]{{0,63}}"
        raise UsageError(problem)
    directory = resolve_from_cwd(args.directory)
    supplied_target: str | None = args.target
    raw_target = (
        _default_scaffold_target() if supplied_target is None else supplied_target
    )
    if not raw_target.strip():
        problem = "--target must not be empty or whitespace only"
        raise UsageError(problem)
    target, manifest_target = _scaffold_target(raw_target, directory=directory)
    manifest_path = directory / DEFAULT_MANIFEST_NAME
    next_command = (PROGRAM_NAME, "lock", "--manifest", str(manifest_path))
    planned = {
        manifest_path: SCAFFOLD_MANIFEST.format(
            bundle_id=bundle_id,
            default_target=json.dumps(manifest_target, ensure_ascii=True),
        ),
        directory / "modules" / "core.md": SCAFFOLD_CORE,
        directory / "modules" / "python.md": SCAFFOLD_PYTHON,
    }
    for path in planned:
        if path.exists() or path.is_symlink():
            raise OutputExistsError(output=path, lexical=str(path))
    create_state_directory(directory / "modules", mode=0o755)
    created: list[str] = []
    for path, text in planned.items():
        atomic_write(path, text.encode("utf-8"), mode=0o644)
        created.append(str(path))
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("init", None, ok=True)
        payload["directory"] = str(directory)
        payload["created"] = created
        payload["target_path"] = str(target)
        payload["next_command"] = list(next_command)
        emit_json(payload)
    else:
        for path_text in created:
            sys.stdout.write(path_text + "\n")
    _note(
        args,
        f"{PROGRAM_NAME}: scaffolded {len(created)} files; run "
        + " ".join(next_command),
    )
    return EXIT_OK


def _run_lock(args: argparse.Namespace) -> int:
    """Validate sources and write or check the deterministic lock.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` after a write, or the difference code under ``--check``.

    Raises:
        ConcurrentChangeError: The lock file changed between the precondition
            capture and the write, so replacing it would discard that change.
    """
    manifest_path = _manifest_path(args)
    manifest = load_manifest(
        manifest_path, lexical_path=args.manifest or str(manifest_path)
    )
    lock_path, lock_lexical = _lock_paths(args, manifest_path)
    compiled = compile_bundle(manifest)
    existing: bytes | None = None
    try:
        existing = read_lock_bytes(lock_path, lexical=lock_lexical)
    except LockMissingError:
        existing = None
    matches = existing == compiled.lock_bytes
    state = (
        BundleState.CURRENT
        if matches
        else BundleState.LOCK_MISSING
        if existing is None
        else BundleState.LOCK_STALE
    )
    if args.check_only:
        code = STATE_EXIT_CODES[state]
        _emit_lock_result(args, compiled, lock_path, state, written=False, code=code)
        return code
    if matches:
        _emit_lock_result(
            args, compiled, lock_path, BundleState.CURRENT, written=False, code=EXIT_OK
        )
        _note(args, f"{PROGRAM_NAME}: lock already current")
        return EXIT_OK
    lock_dir = shared_lock_dir()
    create_state_directory(lock_dir, mode=STATE_DIR_MODE)
    lock_guard = lock_path_for(lock_path, lock_dir=lock_dir)
    with advisory_lock(lock_guard):
        recheck: bytes | None
        try:
            recheck = read_lock_bytes(lock_path, lexical=lock_lexical)
        except LockMissingError:
            recheck = None
        if recheck != existing:
            raise ConcurrentChangeError(
                ConcurrentChangeProblem.LOCK_CHANGED, path=lock_path
            )
        atomic_write(lock_path, compiled.lock_bytes, mode=NEW_TARGET_MODE)
    _emit_lock_result(
        args, compiled, lock_path, BundleState.CURRENT, written=True, code=EXIT_OK
    )
    _note(args, f"{PROGRAM_NAME}: wrote {lock_path}")
    return EXIT_OK


def _emit_lock_result(
    args: argparse.Namespace,
    compiled: CompiledBundle,
    lock_path: Path,
    state: BundleState,
    *,
    written: bool,
    code: int,
) -> None:
    """Emit the result of a lock or lock check.

    Args:
        args: Parsed arguments.
        compiled: The compiled bundle.
        lock_path: Resolved lock path.
        state: Reported state.
        written: Whether the lock file was replaced.
        code: Exit code being returned.
    """
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("lock", state, ok=code == EXIT_OK)
        payload["lock_path"] = str(lock_path)
        payload["lock_sha256"] = compiled.lock_sha256
        payload["manifest_sha256"] = compiled.manifest.sha256
        payload["modules"] = _module_payload(compiled)
        payload["written"] = written
        emit_json(payload)
    else:
        sys.stdout.write(f"{state.value}\n")
        if code != EXIT_OK:
            sys.stderr.write(f"{PROGRAM_NAME}: lock: {state.value}\n")


def _run_validate(args: argparse.Namespace) -> int:
    """Validate the manifest, lock, sources, and rendered structure.

    Deliberately does not report the target's install state. Whether the bundle is
    installed is what ``check`` and ``status`` answer; ``validate`` answers whether
    the inputs and the output structure are sound. A target is still resolved,
    because the manifest must not name a source that aliases the output.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` when the inputs are valid, or the difference code when the lock
        is absent or stale.
    """
    manifest_path = _manifest_path(args)
    manifest = load_manifest(
        manifest_path, lexical_path=args.manifest or str(manifest_path)
    )
    lock_path, lock_lexical = _lock_paths(args, manifest_path)
    target, _target_lexical = _target_paths(args, manifest)
    compiled = compile_bundle(manifest, target=target)
    try:
        existing: bytes | None = read_lock_bytes(lock_path, lexical=lock_lexical)
    except LockMissingError:
        existing = None
    state = (
        BundleState.CURRENT
        if existing == compiled.lock_bytes
        else BundleState.LOCK_MISSING
        if existing is None
        else BundleState.LOCK_STALE
    )
    code = STATE_EXIT_CODES[state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("validate", state, ok=code == EXIT_OK)
        payload.update(_bundle_payload(compiled, lock_path, target))
        payload["target_sha256"] = None
        emit_json(payload)
    else:
        sys.stdout.write(f"{state.value}\n")
        if code != EXIT_OK:
            sys.stderr.write(f"{PROGRAM_NAME}: validate: {state.value}\n")
    return code


def _run_render(args: argparse.Namespace) -> int:
    """Emit rendered bytes to stdout or to an explicit new path.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` on success, or the difference code when ``--locked`` fails.

    Raises:
        OutputExistsError: The requested output path already exists.
    """
    manifest_path = _manifest_path(args)
    manifest = load_manifest(
        manifest_path, lexical_path=args.manifest or str(manifest_path)
    )
    lock_path, lock_lexical = _lock_paths(args, manifest_path)
    compiled = compile_bundle(manifest)
    if args.locked:
        try:
            existing = read_lock_bytes(lock_path, lexical=lock_lexical)
        except LockMissingError:
            return _emit_render_failure(args, BundleState.LOCK_MISSING, lock_path)
        if existing != compiled.lock_bytes:
            return _emit_render_failure(args, BundleState.LOCK_STALE, lock_path)
    supplied_output: str | None = args.output
    if supplied_output is None:
        if resolve_format(args) == FORMAT_JSON:
            payload = _envelope("render", BundleState.CURRENT, ok=True)
            payload.update(_bundle_payload(compiled, lock_path, None))
            payload["output_path"] = None
            emit_json(payload)
        else:
            sys.stdout.buffer.write(compiled.rendered.data)
            sys.stdout.buffer.flush()
        return EXIT_OK
    output = resolve_from_cwd(supplied_output)
    if output.exists() or output.is_symlink():
        raise OutputExistsError(output=output, lexical=supplied_output)
    require_target_parent(output, lexical=supplied_output)
    atomic_write(output, compiled.rendered.data, mode=NEW_TARGET_MODE)
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("render", BundleState.CURRENT, ok=True)
        payload.update(_bundle_payload(compiled, lock_path, None))
        payload["output_path"] = str(output)
        emit_json(payload)
    else:
        sys.stdout.write(str(output) + "\n")
    _note(args, f"{PROGRAM_NAME}: wrote {output}")
    return EXIT_OK


def _emit_render_failure(
    args: argparse.Namespace, state: BundleState, lock_path: Path
) -> int:
    """Report a locked-render refusal without emitting any rendered bytes.

    Args:
        args: Parsed arguments.
        state: Reported state.
        lock_path: Resolved lock path.

    Returns:
        The exit code for the reported state.
    """
    code = STATE_EXIT_CODES[state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("render", state, ok=False)
        payload["lock_path"] = str(lock_path)
        emit_json(payload)
    else:
        sys.stdout.write(f"{state.value}\n")
    sys.stderr.write(f"{PROGRAM_NAME}: render: {state.value} at {lock_path}\n")
    return code


def _run_check(args: argparse.Namespace) -> int:
    """Compare a fresh locked render with the resolved target.

    Args:
        args: Parsed arguments.

    Returns:
        The exit code for the reported state.
    """
    status, lock_path, target = _evaluate(args)
    return _report_state(args, "check", status, lock_path, target)


def _run_status(args: argparse.Namespace) -> int:
    """Report source, lock, target, override, and receipt state.

    Args:
        args: Parsed arguments.

    Returns:
        The exit code for the reported state.
    """
    status, lock_path, target = _evaluate(args)
    state_dir = deployment_state_dir(status.compiled.manifest.bundle_id, target)
    receipts = list_receipts(state_dir)
    extra: dict[str, Any] = {
        "override_path": None
        if status.override.path is None
        else str(status.override.path),
        "override_present": status.override.present,
        "state_root": str(state_dir),
        "receipt_count": len(receipts),
        "latest_receipt": None if not receipts else str(receipts[-1]),
        "backup_count": len(list_backups(state_dir)),
        "lock_present": status.lock_present,
        "lock_matches": status.lock_matches,
        "target_kind": None if status.target is None else status.target.kind.value,
    }
    return _report_state(args, "status", status, lock_path, target, extra)


def _run_install(args: argparse.Namespace) -> int:
    """Back up and atomically install the rendered bundle.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` after a successful apply, or the state code for a dry run.

    Raises:
        UsageError: The digest option was supplied without adoption, or is malformed.
    """
    expected: str | None = args.expect_target_sha256
    if expected is not None:
        if SHA256_PATTERN.match(expected) is None:
            malformed = (
                f"{OPTION_EXPECT_DIGEST} must be a lowercase 64-character "
                "hexadecimal digest"
            )
            raise UsageError(malformed)
        if not args.replace_unmanaged:
            unpaired = (
                f"{OPTION_EXPECT_DIGEST} is only meaningful with "
                f"{OPTION_REPLACE_UNMANAGED}"
            )
            raise UsageError(unpaired)
    manifest_path = _manifest_path(args)
    manifest = load_manifest(
        manifest_path, lexical_path=args.manifest or str(manifest_path)
    )
    lock_path, lock_lexical = _lock_paths(args, manifest_path)
    target, target_lexical = _target_paths(args, manifest)
    status = evaluate(
        manifest,
        lock_path=lock_path,
        lock_lexical=lock_lexical,
        target=target,
        target_lexical=target_lexical,
    )
    if status.state in {BundleState.LOCK_MISSING, BundleState.LOCK_STALE}:
        return _report_state(args, "install", status, lock_path, target)
    compiled = status.compiled
    outcome = install_bundle(
        compiled,
        lock=PathPair(lexical=lock_lexical, resolved=str(lock_path)),
        target=target,
        state_dir=deployment_state_dir(manifest.bundle_id, target),
        lock_dir=shared_lock_dir(),
        target_lexical=target_lexical,
        apply=args.apply,
        replace_unmanaged=args.replace_unmanaged,
        expect_target_sha256=expected,
    )
    code = EXIT_OK if outcome.applied else STATE_EXIT_CODES[outcome.state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope(
            "install", outcome.state, ok=outcome.applied or code == EXIT_OK
        )
        payload.update(_bundle_payload(compiled, lock_path, target))
        payload["applied"] = outcome.applied
        payload["previous_state"] = outcome.plan.previous_target.state.value
        payload["previous_sha256"] = outcome.plan.previous_target.sha256
        payload["backup_path"] = (
            None if outcome.backup is None else str(outcome.backup.path)
        )
        payload["backup_sha256"] = (
            None if outcome.backup is None else outcome.backup.sha256
        )
        payload["receipt_path"] = (
            None if outcome.receipt_path is None else str(outcome.receipt_path)
        )
        payload["target_mode"] = f"0{outcome.plan.target_mode:03o}"
        payload["target_sha256"] = (
            None if status.target is None else status.target.sha256
        )
        emit_json(payload)
    else:
        sys.stdout.write(f"{outcome.state.value}\n")
        if not outcome.applied:
            _note(
                args,
                f"{PROGRAM_NAME}: dry run; nothing written. Re-run with --apply.",
            )
    return code


def _run_rollback(args: argparse.Namespace) -> int:
    """Restore the bytes one install replaced.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` after a successful apply, or the state code for a dry run.
    """
    manifest_path = _manifest_path(args)
    manifest = load_manifest(
        manifest_path, lexical_path=args.manifest or str(manifest_path)
    )
    target, target_lexical = _target_paths(args, manifest)
    state_dir = deployment_state_dir(manifest.bundle_id, target)
    legacy_state_dir = bundle_state_dir(manifest.bundle_id)
    receipt_lexical: str = args.receipt
    receipt_path = resolve_from_cwd(receipt_lexical)
    receipt_state_dir = (
        legacy_state_dir
        if is_within(receipt_path, legacy_state_dir)
        and not is_within(receipt_path, state_dir)
        else state_dir
    )
    receipt = load_install_receipt(
        receipt_path,
        state_root=receipt_state_dir,
        bundle_id=manifest.bundle_id,
        target=target,
        lexical=receipt_lexical,
    )
    outcome = rollback_install(
        receipt,
        target=target,
        state_dir=state_dir,
        lock_dir=shared_lock_dir(),
        target_lexical=target_lexical,
        apply=args.apply,
    )
    code = EXIT_OK if outcome.applied else STATE_EXIT_CODES[outcome.state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("rollback", outcome.state, ok=outcome.applied)
        payload["applied"] = outcome.applied
        payload["receipt_path"] = str(receipt.path)
        payload["restored_sha256"] = (
            None if outcome.restored is None else outcome.restored.sha256
        )
        payload["preserved_path"] = (
            None if outcome.preserved_path is None else str(outcome.preserved_path)
        )
        payload["receipt_written"] = (
            None if outcome.receipt_path is None else str(outcome.receipt_path)
        )
        payload["target_path"] = str(target)
        emit_json(payload)
    else:
        sys.stdout.write(f"{outcome.state.value}\n")
        if not outcome.applied:
            _note(
                args,
                f"{PROGRAM_NAME}: dry run; nothing restored. Re-run with --apply.",
            )
    return code


def _run_verify_codex(args: argparse.Namespace) -> int:
    """Confirm the installed bundle is visible in Codex startup input.

    Runtime verification runs only after the static state is ``CURRENT``. Verifying
    prompt input while the target is drifted, missing, or shadowed would prove
    something about bytes nobody asked about.

    Args:
        args: Parsed arguments.

    Returns:
        ``EXIT_OK`` when every check passed, otherwise the code for the reported
        state. ``RUNTIME_UNVERIFIED`` exits 1 because verification was requested
        and did not succeed.

    Raises:
        UsageError: A working directory was supplied for the active global target,
            or a project working directory falls outside the target's discovery
            chain.
    """
    status, lock_path, target = _evaluate(args)
    context = verification_context_for_target(target)
    supplied_cwd: str | None = args.cwd
    if context is VerificationContext.GLOBAL:
        if supplied_cwd is not None:
            problem = "--cwd cannot be used with the active global target"
            raise UsageError(problem)
        probe_cwd = None
    else:
        probe_cwd = (
            target.parent if supplied_cwd is None else resolve_from_cwd(supplied_cwd)
        )
        if not is_within(probe_cwd, target.parent):
            problem = f"--cwd must be {target.parent} or one of its descendants"
            raise UsageError(problem)
    if status.state is not BundleState.CURRENT:
        # Never report CURRENT for a run whose runtime verification did not happen.
        return _report_state(
            args,
            "verify-codex",
            status,
            lock_path,
            target,
            {
                "capability_present": False,
                "failure": "static state is not CURRENT",
                "probe_cwd": None if probe_cwd is None else str(probe_cwd),
                "verification_context": context.value,
            },
        )
    rendered = status.compiled.rendered
    try:
        result = verify_rendered_visibility(
            rendered, cwd=probe_cwd, timeout_seconds=args.timeout
        )
    except CodexVerificationError as error:
        result = unverified(
            error,
            markers_expected=len(required_markers(rendered.modules)),
            sentinels_expected=len(content_sentinels(rendered)),
            probe_cwd=probe_cwd,
            verification_context=context,
        )
    code = STATE_EXIT_CODES[result.state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope("verify-codex", result.state, ok=code == EXIT_OK)
        payload.update(_bundle_payload(status.compiled, lock_path, target))
        payload["target_sha256"] = (
            None if status.target is None else status.target.sha256
        )
        payload.update(_verification_payload(result))
        emit_json(payload)
    else:
        sys.stdout.write(f"{result.state.value}\n")
        if result.failure is not None:
            sys.stderr.write(f"{PROGRAM_NAME}: verify-codex: {result.failure}\n")
        else:
            _note(
                args,
                f"{PROGRAM_NAME}: {result.codex_version} exposed all "
                f"{result.markers_found} markers and {result.sentinels_found} "
                "content sentinels",
            )
    return code


def _verification_payload(result: RuntimeVerification) -> dict[str, Any]:
    """Render a runtime verification result for a JSON envelope.

    Args:
        result: The verification result.

    Returns:
        The verification fields.
    """
    return {
        "codex_path": None if result.codex_path is None else str(result.codex_path),
        "codex_version": result.codex_version,
        "capability_present": result.capability_present,
        "markers_expected": result.markers_expected,
        "markers_found": result.markers_found,
        "sentinels_expected": result.sentinels_expected,
        "sentinels_found": result.sentinels_found,
        "probe_command": list(result.probe_command),
        "probe_cwd": None if result.probe_cwd is None else str(result.probe_cwd),
        "verification_context": result.verification_context.value,
        "failure": result.failure,
    }


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "check": _run_check,
    "init": _run_init,
    "install": _run_install,
    "lock": _run_lock,
    "render": _run_render,
    "rollback": _run_rollback,
    "status": _run_status,
    "validate": _run_validate,
    "verify-codex": _run_verify_codex,
    "version": _run_version,
}


def _report_error(args: argparse.Namespace, command: str, error: CompilerError) -> int:
    """Emit a failure and map it to an exit code.

    Args:
        args: Parsed arguments.
        command: Subcommand name.
        error: The failure to report.

    Returns:
        The exit code for the error's state.
    """
    state = error.state
    code = EXIT_ERROR if state is None else STATE_EXIT_CODES[state]
    if resolve_format(args) == FORMAT_JSON:
        payload = _envelope(command, state, ok=False)
        detail: dict[str, Any] = {
            "kind": type(error).__name__,
            "message": str(error),
        }
        if error.paths is not None:
            paths: dict[str, Any] = {"lexical": error.paths.lexical}
            if error.paths.resolved != error.paths.lexical:
                paths["resolved"] = error.paths.resolved
            detail["paths"] = paths
        payload["error"] = detail
        emit_json(payload)
    sys.stderr.write(f"{PROGRAM_NAME}: {command}: {error}\n")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Argument vector without the program name. ``None`` reads
            ``sys.argv[1:]``.

    Returns:
        A process exit code from the documented exit-code table.

    Raises:
        SystemExit: Argparse help output was requested successfully.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:
        # Argparse exits 2 for a usage error, but this contract reserves 2 for a
        # read-only difference and assigns 1 to an invalid invocation. Help and
        # version legitimately exit 0 and must keep doing so.
        if exit_request.code == 0:
            raise
        return EXIT_ERROR
    if args.version_flag:
        return _run_version(args)
    if args.command is None:
        parser.print_usage(sys.stderr)
        sys.stderr.write(
            f"{PROGRAM_NAME}: no command given. Run '{PROGRAM_NAME} --help'.\n"
        )
        return EXIT_ERROR
    command: str = args.command
    try:
        return _COMMANDS[command](args)
    except CompilerError as error:
        return _report_error(args, command, error)

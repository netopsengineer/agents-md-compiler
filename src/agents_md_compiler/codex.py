"""Codex model-visible startup verification.

This module answers one question with evidence: are the installed bundle's bytes
actually present in the input the model receives at startup? It answers it by
inspecting Codex's own rendered prompt input, not by asking a model what it read.

There is deliberately no fallback that asks a model to summarize its instructions.
A model's claim about its own instructions is not evidence, and a plausible summary
of rules that were never loaded is worse than a clear failure.

What a pass proves: the bytes reached the model's input. What it does not prove: the
model obeyed them. Marker presence is a visibility check, never a compliance claim.

``codex debug prompt-input`` is a debug interface, not a promised stable API. When
the installed Codex lacks it or changes it, verification reports
``RUNTIME_UNVERIFIED`` with the exact command and the observed failure.
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from agents_md_compiler.errors import CodexProblem, CodexVerificationError
from agents_md_compiler.models import (
    AGENTS_FILENAME,
    OVERRIDE_FILENAME,
    BundleState,
    LockedModule,
    RenderedBundle,
)
from agents_md_compiler.rendering import begin_marker, end_marker, generated_marker

EXECUTABLE_NAME = "codex"
"""Resolved from ``PATH`` without a shell."""

DEBUG_SUBCOMMAND = ("debug", "prompt-input")
"""The capability this verifier depends on."""

PROBE_PROMPT = "agents-md-compiler runtime visibility probe"
"""Neutral probe text. Checked to contain no marker or sentinel before use."""

PROBE_DIRECTORY_PREFIX = "amc-probe-"
"""Prefix for the disposable probe directory, which holds no instruction file."""

DEFAULT_TIMEOUT_SECONDS = 60.0
"""Default deadline for independently timed Codex invocations."""

MAX_OUTPUT_BYTES = 32 * 1024 * 1024
"""Captured output is bounded; a larger response is refused rather than parsed."""

MIN_SENTINEL_LENGTH = 24
"""Shortest accepted content sentinel, long enough to be unlikely by accident."""

CODEX_HOME_ENV = "CODEX_HOME"
"""Environment variable selecting the active Codex home."""

DEFAULT_CODEX_HOME_DIRNAME = ".codex"
"""Home-relative Codex directory used when ``CODEX_HOME`` is unset."""


class VerificationContext(StrEnum):
    """Codex instruction-discovery context used by a runtime probe."""

    GLOBAL = "global"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class CodexCapability:
    """What the installed Codex CLI is and whether it can be inspected.

    Attributes:
        path: Resolved executable path.
        version: Captured ``codex --version`` output, stripped.
        prompt_input_supported: Whether ``debug prompt-input`` is exposed.
    """

    path: Path
    version: str
    prompt_input_supported: bool


@dataclass(frozen=True, slots=True)
class RuntimeVerification:
    """The result of one runtime verification.

    Attributes:
        state: ``CURRENT`` only when every check passed, otherwise
            ``RUNTIME_UNVERIFIED``.
        codex_path: Resolved executable, or ``None`` when it was not found.
        codex_version: Captured version, or ``None`` when it was not captured.
        capability_present: Whether ``debug prompt-input`` is exposed.
        markers_expected: Marker lines the bundle requires.
        markers_found: Marker lines located in the prompt input.
        sentinels_expected: Content sentinels the bundle requires.
        sentinels_found: Content sentinels located in the prompt input.
        probe_command: Exact argument vector used, for reproduction.
        probe_cwd: Startup directory used, or ``None`` when failure occurred
            before an isolated directory was created.
        verification_context: Global or project instruction context.
        failure: Observed failure, or ``None`` on success.
    """

    state: BundleState
    codex_path: Path | None
    codex_version: str | None
    capability_present: bool
    markers_expected: int
    markers_found: int
    sentinels_expected: int
    sentinels_found: int
    probe_command: tuple[str, ...]
    probe_cwd: Path | None
    verification_context: VerificationContext
    failure: str | None


def active_codex_home(*, environ: Mapping[str, str] | None = None) -> Path:
    """Resolve the active Codex home without following symbolic links.

    Args:
        environ: Environment mapping. ``None`` reads the process environment.

    Returns:
        Absolute normalized Codex home path.
    """
    env = os.environ if environ is None else environ
    configured = env.get(CODEX_HOME_ENV, "")
    selected = (
        Path(configured).expanduser()
        if configured
        else Path.home() / DEFAULT_CODEX_HOME_DIRNAME
    )
    if not selected.is_absolute():
        selected = Path.cwd() / selected
    return Path(os.path.normpath(selected))


def verification_context_for_target(
    target: Path, *, codex_home: Path | None = None
) -> VerificationContext:
    """Classify a selected target as active global or project-scoped.

    Args:
        target: Resolved selected target.
        codex_home: Active Codex home override, primarily for tests.

    Returns:
        ``global`` only for the active base or override file.
    """
    home = active_codex_home() if codex_home is None else codex_home
    global_targets = {home / AGENTS_FILENAME, home / OVERRIDE_FILENAME}
    return (
        VerificationContext.GLOBAL
        if target in global_targets
        else VerificationContext.PROJECT
    )


def resolve_executable(name: str = EXECUTABLE_NAME) -> Path:
    """Locate the Codex executable on ``PATH`` without invoking a shell.

    Args:
        name: Executable name to resolve.

    Returns:
        The resolved absolute path.

    Raises:
        CodexVerificationError: The executable is not on ``PATH``.
    """
    found = shutil.which(name)
    if found is None:
        raise CodexVerificationError(
            CodexProblem.EXECUTABLE_MISSING, detail=name, command=(name,)
        )
    return Path(found)


def _run(
    command: Sequence[str], *, timeout_seconds: float, cwd: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run one command as an argument vector, never through a shell.

    The parent environment is inherited on purpose: the probe must use the
    operator's real Codex configuration and instruction discovery context.
    Nothing secret is read from that environment, and no value from it is logged.

    Args:
        command: Argument vector. The first element is an absolute executable path.
        timeout_seconds: Deadline for the invocation.
        cwd: Working directory for the child.

    Returns:
        The completed process with captured output.

    Raises:
        CodexVerificationError: The invocation timed out, or the executable could
            not be started.
    """
    try:
        # nosec B603 - and noqa S603: the argument vector is built from an
        # absolute path resolved by shutil.which plus fixed literals, shell=False.
        return subprocess.run(  # noqa: S603  # nosec B603
            list(command),
            capture_output=True,
            timeout=timeout_seconds,
            cwd=cwd,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise CodexVerificationError(
            CodexProblem.PROBE_TIMEOUT,
            detail=f"exceeded {timeout_seconds}s",
            command=tuple(command),
        ) from error
    except OSError as error:
        raise CodexVerificationError(
            CodexProblem.EXECUTABLE_MISSING,
            detail=error.strerror or type(error).__name__,
            command=tuple(command),
        ) from error


def detect_capability(
    *, name: str = EXECUTABLE_NAME, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> CodexCapability:
    """Resolve Codex, capture its version, and probe for the debug subcommand.

    Args:
        name: Executable name to resolve.
        timeout_seconds: Deadline for each invocation.

    Returns:
        What the installed CLI is and whether it can be inspected.

    Raises:
        CodexVerificationError: The executable is missing, ``--version`` failed, or
            the debug subcommand is not exposed.
    """
    executable = resolve_executable(name)
    version_command = (str(executable), "--version")
    completed = _run(version_command, timeout_seconds=timeout_seconds)
    if completed.returncode != 0:
        raise CodexVerificationError(
            CodexProblem.VERSION_FAILED,
            detail=f"exit {completed.returncode}",
            command=version_command,
        )
    version = completed.stdout.decode("utf-8", errors="replace").strip()
    help_command = (str(executable), *DEBUG_SUBCOMMAND, "--help")
    help_result = _run(help_command, timeout_seconds=timeout_seconds)
    if help_result.returncode != 0:
        raise CodexVerificationError(
            CodexProblem.CAPABILITY_MISSING,
            detail=f"exit {help_result.returncode}",
            command=help_command,
        )
    return CodexCapability(
        path=executable, version=version, prompt_input_supported=True
    )


def extract_strings(document: object) -> Iterator[str]:
    """Yield every string anywhere in a decoded JSON document.

    Traversal is exhaustive and order-independent, so a change in how Codex nests
    or pretty-prints its prompt input cannot hide a marker from this search.

    Args:
        document: Decoded JSON value.

    Yields:
        Each string found, including dictionary keys.
    """
    if isinstance(document, str):
        yield document
    elif isinstance(document, dict):
        mapping = cast("dict[object, object]", document)
        for key, value in mapping.items():
            if isinstance(key, str):
                yield key
            yield from extract_strings(value)
    elif isinstance(document, list):
        for item in cast("list[object]", document):
            yield from extract_strings(item)


def required_markers(modules: Sequence[LockedModule]) -> tuple[str, ...]:
    """List every marker line the prompt input must contain.

    Args:
        modules: Locked modules in manifest order.

    Returns:
        The generated header marker followed by each module's begin and end marker.
    """
    markers = [generated_marker()]
    for module in modules:
        markers.append(begin_marker(module))
        markers.append(end_marker(module))
    return tuple(markers)


def content_sentinels(rendered: RenderedBundle) -> tuple[str, ...]:
    """Choose unique content sentinels from the first and last modules.

    A marker proves the wrapper arrived. A sentinel proves the wrapped bytes did.
    Taking one from the first module and one from the last is what makes this a
    head-to-tail check rather than a "the beginning arrived" check.

    Selection is deterministic: within each module, the first line that is long
    enough, is not a compiler marker, and occurs exactly once in the whole rendered
    bundle. A module offering no such line propagates a
    :class:`CodexVerificationError`, because no head-to-tail claim could be made
    honestly without one.

    Args:
        rendered: The rendered bundle.

    Returns:
        One sentinel per selected module, in module order. Two modules can never
        yield the same sentinel, because a line that occurs in both would occur
        twice in the bundle and the uniqueness requirement would reject it.

    """
    text = rendered.data.decode("utf-8")
    chosen = (
        (rendered.modules[0],)
        if len(rendered.modules) == 1
        else (rendered.modules[0], rendered.modules[-1])
    )
    return tuple(_sentinel_for(text, module) for module in chosen)


def _sentinel_for(text: str, module: LockedModule) -> str:
    """Select one unique sentinel line from a module's rendered content.

    Args:
        text: The decoded rendered bundle.
        module: The module to select from.

    Returns:
        The chosen sentinel line.

    Raises:
        CodexVerificationError: The module offers no long-enough unique line.
    """
    begin = begin_marker(module) + "\n"
    end = "\n" + end_marker(module)
    start = text.index(begin) + len(begin)
    stop = text.index(end, start)
    for line in text[start:stop].split("\n"):
        candidate = line.strip()
        if len(candidate) < MIN_SENTINEL_LENGTH:
            continue
        if candidate.startswith("<!--"):
            # Real policy sources carry HTML comments such as linter directives.
            # A comment is not evidence that policy prose arrived, so skip it.
            continue
        if text.count(candidate) == 1:
            return candidate
    raise CodexVerificationError(
        CodexProblem.SENTINEL_ABSENT,
        detail=(
            f"module {module.id!r} has no unique line of at least "
            f"{MIN_SENTINEL_LENGTH} characters to use as a sentinel"
        ),
    )


def _check_probe_is_clean(
    command: Sequence[str], directory: Path, expected: Sequence[str]
) -> None:
    """Prove the probe cannot be the source of anything it looks for.

    Without this, a marker found in the prompt input could have come from the probe
    text or the directory name rather than from the installed bundle.

    Args:
        command: Argument vector about to be run.
        directory: Disposable probe directory.
        expected: Markers and sentinels that will be searched for.

    Raises:
        CodexVerificationError: The probe itself contains an expected value.
    """
    haystack = " ".join([*command, str(directory), PROBE_PROMPT])
    for value in expected:
        if value in haystack:
            raise CodexVerificationError(
                CodexProblem.PROBE_CONTAMINATED,
                detail=f"probe contains {value[:40]!r}",
                command=tuple(command),
            )


def inspect_prompt_input(
    capability: CodexCapability,
    *,
    expected: Sequence[str],
    cwd: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[object, tuple[str, ...], Path]:
    """Run the prompt-input probe and return its decoded JSON.

    With no ``cwd``, the probe runs from a fresh empty directory so only global
    instructions can contribute. A supplied directory exercises Codex project
    discovery from that startup location.

    Args:
        capability: The detected Codex capability.
        expected: Markers and sentinels the caller will search for, checked against
            the probe itself first.
        cwd: Existing project startup directory. ``None`` requests isolation.
        timeout_seconds: Deadline for the invocation.

    Returns:
        The decoded JSON document, exact argument vector, and startup directory.

    Raises:
        CodexVerificationError: The probe was contaminated, exited nonzero, timed
            out, returned more than the accepted output bound, or returned output
            that is not valid JSON.
    """
    command = (str(capability.path), *DEBUG_SUBCOMMAND, PROBE_PROMPT)
    if cwd is not None:
        if not cwd.is_dir():
            raise CodexVerificationError(
                CodexProblem.PROBE_DIRECTORY_INVALID,
                detail=str(cwd),
                command=command,
            )
        return _run_prompt_input(
            command,
            directory=cwd,
            expected=expected,
            timeout_seconds=timeout_seconds,
        )
    with tempfile.TemporaryDirectory(prefix=PROBE_DIRECTORY_PREFIX) as raw_directory:
        return _run_prompt_input(
            command,
            directory=Path(raw_directory),
            expected=expected,
            timeout_seconds=timeout_seconds,
        )


def _run_prompt_input(
    command: tuple[str, ...],
    *,
    directory: Path,
    expected: Sequence[str],
    timeout_seconds: float,
) -> tuple[object, tuple[str, ...], Path]:
    """Execute and decode one prompt-input probe from a checked directory.

    Args:
        command: Exact Codex argument vector.
        directory: Startup directory.
        expected: Values that must not contaminate the probe itself.
        timeout_seconds: Invocation deadline.

    Returns:
        The decoded JSON document, command, and startup directory.

    Raises:
        CodexVerificationError: The probe fails or returns unusable output.
    """
    _check_probe_is_clean(command, directory, expected)
    completed = _run(command, timeout_seconds=timeout_seconds, cwd=directory)
    if completed.returncode != 0:
        raise CodexVerificationError(
            CodexProblem.PROBE_FAILED,
            detail=f"exit {completed.returncode}",
            command=command,
        )
    if len(completed.stdout) > MAX_OUTPUT_BYTES:
        raise CodexVerificationError(
            CodexProblem.OUTPUT_TOO_LARGE,
            detail=f"{len(completed.stdout)} > {MAX_OUTPUT_BYTES}",
            command=command,
        )
    try:
        return json.loads(completed.stdout.decode("utf-8")), command, directory
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CodexVerificationError(
            CodexProblem.INVALID_JSON, detail=str(error), command=command
        ) from error


def _count_occurrences(strings: Sequence[str], value: str) -> int:
    """Count how many extracted strings contain a value.

    Args:
        strings: Every string extracted from the prompt input.
        value: Marker or sentinel to look for.

    Returns:
        Total occurrences across all extracted strings.
    """
    return sum(item.count(value) for item in strings)


def verify_rendered_visibility(
    rendered: RenderedBundle,
    *,
    name: str = EXECUTABLE_NAME,
    cwd: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    capability_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RuntimeVerification:
    """Prove the rendered bundle is visible in Codex startup input, head to tail.

    Args:
        rendered: The rendered bundle that should be installed and visible.
        name: Executable name to resolve.
        cwd: Project startup directory. ``None`` uses an isolated global probe.
        timeout_seconds: Deadline for the prompt-input invocation.
        capability_timeout_seconds: Independent deadline for each capability
            detection invocation.

    Returns:
        The verification result, ``CURRENT`` only when every check passed.

    Raises:
        CodexVerificationError: Any check failed. The error names the exact command
            and the observed failure.
    """
    markers = required_markers(rendered.modules)
    sentinels = content_sentinels(rendered)
    expected = (*markers, *sentinels)
    capability = detect_capability(
        name=name, timeout_seconds=capability_timeout_seconds
    )
    document, command, probe_cwd = inspect_prompt_input(
        capability, expected=expected, cwd=cwd, timeout_seconds=timeout_seconds
    )
    strings = tuple(extract_strings(document))

    header = markers[0]
    header_count = _count_occurrences(strings, header)
    if header_count == 0:
        raise CodexVerificationError(
            CodexProblem.HEADER_ABSENT, detail=header, command=command
        )
    for marker in markers[1:]:
        count = _count_occurrences(strings, marker)
        if count == 0:
            raise CodexVerificationError(
                CodexProblem.MARKER_ABSENT, detail=marker, command=command
            )
        if count > 1:
            raise CodexVerificationError(
                CodexProblem.MARKER_DUPLICATED,
                detail=f"{marker} appears {count} times",
                command=command,
            )
    for sentinel in sentinels:
        if _count_occurrences(strings, sentinel) == 0:
            raise CodexVerificationError(
                CodexProblem.SENTINEL_ABSENT, detail=sentinel, command=command
            )
    return RuntimeVerification(
        state=BundleState.CURRENT,
        codex_path=capability.path,
        codex_version=capability.version,
        capability_present=capability.prompt_input_supported,
        markers_expected=len(markers),
        markers_found=len(markers),
        sentinels_expected=len(sentinels),
        sentinels_found=len(sentinels),
        probe_command=command,
        probe_cwd=probe_cwd,
        verification_context=(
            VerificationContext.GLOBAL if cwd is None else VerificationContext.PROJECT
        ),
        failure=None,
    )


def unverified(
    error: CodexVerificationError,
    *,
    capability: CodexCapability | None = None,
    markers_expected: int = 0,
    sentinels_expected: int = 0,
    probe_cwd: Path | None = None,
    verification_context: VerificationContext = VerificationContext.GLOBAL,
) -> RuntimeVerification:
    """Summarize a failure as an explicit ``RUNTIME_UNVERIFIED`` result.

    Args:
        error: The failure to summarize.
        capability: What was learned about the CLI before failing, when anything.
        markers_expected: Marker lines the bundle requires.
        sentinels_expected: Content sentinels the bundle requires.
        probe_cwd: Requested startup directory, when known.
        verification_context: Global or project instruction context.

    Returns:
        The verification result, never ``CURRENT``.
    """
    return RuntimeVerification(
        state=BundleState.RUNTIME_UNVERIFIED,
        codex_path=None if capability is None else capability.path,
        codex_version=None if capability is None else capability.version,
        capability_present=capability is not None,
        markers_expected=markers_expected,
        markers_found=0,
        sentinels_expected=sentinels_expected,
        sentinels_found=0,
        probe_command=error.command,
        probe_cwd=probe_cwd,
        verification_context=verification_context,
        failure=str(error),
    )

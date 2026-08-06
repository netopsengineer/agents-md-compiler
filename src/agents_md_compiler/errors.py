"""Public exception taxonomy.

Every error carries the :class:`~agents_md_compiler.models.BundleState` it maps to,
so the CLI can select an exit code without re-deriving the failure. Messages are
composed inside each class from structured arguments, which keeps call sites free of
message strings and keeps the wording of a failure in exactly one place.

No message ever contains canonical policy content. Paths and digests are included
because an operator cannot resolve a path or drift failure without them.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from agents_md_compiler.models import BundleState, ModuleSpec, PathPair


class CompilerError(Exception):
    """Base class for every error this package raises.

    Attributes:
        state: The externally visible state this failure reports.
        paths: The path involved, when the failure concerns a specific path.
    """

    state: BundleState | None = None
    paths: PathPair | None = None

    def __init__(self, message: str, *, paths: PathPair | None = None) -> None:
        """Store the composed message and optional path pair.

        Args:
            message: Fully composed, policy-free diagnostic.
            paths: Lexical and resolved forms of the path involved.
        """
        super().__init__(message)
        self.paths = paths


def _pair(lexical: str | Path | None, resolved: Path | None) -> PathPair | None:
    """Build a path pair when a path is involved.

    Args:
        lexical: Operator-supplied path form.
        resolved: Absolute normalized path form.

    Returns:
        A pair, or ``None`` when no path applies.
    """
    if resolved is None:
        return None
    return PathPair(
        lexical=str(lexical if lexical is not None else resolved),
        resolved=str(resolved),
    )


class UsageError(CompilerError):
    """The invocation itself is invalid."""

    def __init__(self, detail: str) -> None:
        """Compose an invocation diagnostic.

        Args:
            detail: What is wrong with the invocation.
        """
        super().__init__(f"invalid invocation: {detail}")


class ManifestProblem(StrEnum):
    """Why a manifest was rejected."""

    UNREADABLE = "manifest could not be read"
    TOO_LARGE = "manifest exceeds the configured size limit"
    NOT_A_FILE = "manifest is not a regular file"
    SYNTAX = "manifest is not valid TOML"
    UNKNOWN_KEY = "unknown top-level key"
    UNKNOWN_MODULE_KEY = "unknown module key"
    MISSING_KEY = "required key is missing"
    WRONG_TYPE = "key has the wrong type"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported schema_version"
    BAD_IDENTIFIER = "identifier does not match [a-z][a-z0-9-]{0,63}"
    BLANK_VALUE = "value is empty or whitespace only"
    NO_MODULES = "modules must be a non-empty array of tables"
    MODULE_NOT_A_TABLE = "module entry is not a table"
    TOO_MANY_MODULES = "module count exceeds the configured limit"
    DUPLICATE_MODULE_ID = "duplicate module id"
    DUPLICATE_SOURCE_PATH = "duplicate resolved source path"
    TARGET_ALIASES_SOURCE = "a source resolves to the output target"


class ManifestError(CompilerError):
    """The manifest is syntactically or structurally invalid."""

    state: BundleState | None = BundleState.INVALID_MANIFEST

    def __init__(
        self,
        problem: ManifestProblem,
        *,
        detail: str = "",
        manifest: Path | None = None,
        lexical: str | None = None,
    ) -> None:
        """Compose a manifest diagnostic.

        Args:
            problem: Which manifest rule was violated.
            detail: Key name, observed value type, or other short specifics.
            manifest: Resolved manifest path.
            lexical: Manifest path as supplied.
        """
        parts = [f"{problem.value}"]
        if detail:
            parts.append(f"({detail})")
        if manifest is not None:
            parts.append(f"in {manifest}")
        self.problem = problem
        super().__init__(" ".join(parts), paths=_pair(lexical, manifest))


class SourceProblem(StrEnum):
    """Why a canonical source was rejected."""

    MISSING = "source does not exist"
    SYMLINK = "source is a symbolic link"
    NOT_A_FILE = "source is not a regular file"
    UNREADABLE = "source could not be read"
    EMPTY = "source is empty"
    TOO_LARGE = "source exceeds the configured size limit"
    BUNDLE_TOO_LARGE = "total source bytes exceed the configured bundle limit"
    NOT_UTF8 = "source is not valid UTF-8"
    HAS_BOM = "source begins with a UTF-8 byte order mark"
    HAS_NUL = "source contains a NUL byte"
    HAS_CR = "source contains a carriage return"
    NO_FINAL_LF = "source does not end with exactly one LF"
    HAS_MARKER = "source contains the compiler marker prefix"
    CHANGED_WHILE_READING = "source identity or size changed while being read"
    DUPLICATE_CONTENT = "another module has byte-identical content"
    PATH_NOT_UTF8 = "source path is not representable as UTF-8"


class SourceError(CompilerError):
    """A canonical source violates a path, type, encoding, or content invariant."""

    state: BundleState | None = BundleState.INVALID_SOURCE

    def __init__(
        self,
        problem: SourceProblem,
        *,
        module_id: str,
        source: Path,
        lexical: str,
        detail: str = "",
        link_target: Path | None = None,
    ) -> None:
        """Compose a source diagnostic.

        Args:
            problem: Which source rule was violated.
            module_id: Module the source belongs to.
            source: Absolute normalized source path.
            lexical: Source path as the manifest wrote it.
            detail: Offset, size, limit, or other short specifics.
            link_target: Where a rejected symbolic link pointed.
        """
        parts = [f"module {module_id!r}: {problem.value}"]
        if detail:
            parts.append(f"({detail})")
        parts.append(f"at {source}")
        if lexical != str(source):
            parts.append(f"declared as {lexical!r}")
        if link_target is not None:
            parts.append(f"linking to {link_target}")
        self.problem = problem
        self.module_id = module_id
        super().__init__(" ".join(parts), paths=_pair(lexical, source))

    @classmethod
    def from_spec(
        cls,
        problem: SourceProblem,
        spec: ModuleSpec,
        *,
        detail: str = "",
        link_target: Path | None = None,
    ) -> Self:
        """Build a refusal for one module spec.

        Every source rejection carries the same three identity fields, so they are
        read off the spec here rather than repeated at each of the many call sites.

        Args:
            problem: Which source rule was violated.
            spec: The module whose source was rejected.
            detail: Offset, size, limit, or other short specifics.
            link_target: Where a rejected symbolic link pointed.

        Returns:
            The error to raise.
        """
        return cls(
            problem,
            module_id=spec.id,
            source=spec.source,
            lexical=spec.lexical_source,
            detail=detail,
            link_target=link_target,
        )


class LockProblem(StrEnum):
    """Why a lock document was rejected."""

    SYNTAX = "lock is not valid JSON"
    NOT_AN_OBJECT = "lock root is not an object"
    UNKNOWN_KEY = "unknown lock key"
    UNKNOWN_MODULE_KEY = "unknown locked module key"
    MISSING_KEY = "required lock key is missing"
    WRONG_TYPE = "lock key has the wrong type"
    UNSUPPORTED_FORMAT_VERSION = "unsupported lock format_version"
    BAD_IDENTIFIER = "lock identifier does not match [a-z][a-z0-9-]{0,63}"
    BAD_DIGEST = "lock digest is not a lowercase 64-character hexadecimal string"
    BAD_SIZE = "lock size_bytes is not a positive integer"
    NO_MODULES = "lock modules must be a non-empty array of objects"
    MODULE_NOT_AN_OBJECT = "locked module entry is not an object"
    NOT_A_FILE = "lock is not a regular file"
    SYMLINK = "lock is a symbolic link"
    UNREADABLE = "lock could not be read"


class LockError(CompilerError):
    """The lock is syntactically or structurally invalid."""

    state: BundleState | None = BundleState.INVALID_LOCK

    def __init__(
        self,
        problem: LockProblem,
        *,
        detail: str = "",
        lock: Path | None = None,
        lexical: str | None = None,
    ) -> None:
        """Compose a lock diagnostic.

        Args:
            problem: Which lock rule was violated.
            detail: Key name, observed value, or other short specifics.
            lock: Resolved lock path.
            lexical: Lock path as supplied.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        if lock is not None:
            parts.append(f"in {lock}")
        self.problem = problem
        super().__init__(" ".join(parts), paths=_pair(lexical, lock))


class LockMissingError(CompilerError):
    """A command that requires a lock found none."""

    state: BundleState | None = BundleState.LOCK_MISSING

    def __init__(self, *, lock: Path, lexical: str) -> None:
        """Compose a missing-lock diagnostic.

        Args:
            lock: Resolved lock path.
            lexical: Lock path as supplied.
        """
        super().__init__(
            f"no lock at {lock}; run 'lock' to create it", paths=_pair(lexical, lock)
        )


class LockStaleProblem(StrEnum):
    """Why an on-disk lock no longer matches reality."""

    MANIFEST_CHANGED = "manifest bytes differ from the lock"
    SOURCES_CHANGED = "source bytes or paths differ from the lock"
    BUNDLE_ID_CHANGED = "bundle_id differs from the lock"
    MODULE_SET_CHANGED = "module set or order differs from the lock"


class LockStaleError(CompilerError):
    """The on-disk lock disagrees with the manifest or the sources."""

    state: BundleState | None = BundleState.LOCK_STALE

    def __init__(
        self, problem: LockStaleProblem, *, lock: Path, lexical: str, detail: str = ""
    ) -> None:
        """Compose a stale-lock diagnostic.

        Args:
            problem: How the lock disagrees.
            lock: Resolved lock path.
            lexical: Lock path as supplied.
            detail: Module identifier or other short specifics.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        parts.append(f"at {lock}; run 'lock' to refresh it")
        self.problem = problem
        super().__init__(" ".join(parts), paths=_pair(lexical, lock))


class RenderProblem(StrEnum):
    """Why rendered bytes were rejected."""

    HEADER_MISMATCH = "generated header does not match format version 1"
    MISSING_SEPARATOR = "expected one empty line before a module marker"
    MARKER_MISMATCH = "module marker does not match the lock"
    CONTENT_DIGEST_MISMATCH = "module content digest does not match the lock"
    TRUNCATED = "rendered bytes end inside a module block"
    TRAILING_CONTENT = "rendered bytes continue after the final module marker"
    MODULE_COUNT_MISMATCH = "rendered module marker count does not match the lock"
    BAD_IDENTIFIER = "identifier does not match [a-z][a-z0-9-]{0,63}"
    BAD_DIGEST = "digest is not a lowercase 64-character hexadecimal string"
    BAD_SIZE = "declared byte count does not match the content length"


class RenderError(CompilerError):
    """Rendered bytes failed their own structural validation."""

    state: BundleState | None = BundleState.INVALID_SOURCE

    def __init__(self, problem: RenderProblem, *, detail: str = "") -> None:
        """Compose a render diagnostic.

        Args:
            problem: Which structural rule was violated.
            detail: Module identifier, line number, or other short specifics.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        self.problem = problem
        super().__init__(" ".join(parts))


class OutputExistsError(CompilerError):
    """``render --output`` refuses to write to a path that already exists."""

    def __init__(self, *, output: Path, lexical: str) -> None:
        """Compose an output-refusal diagnostic.

        Args:
            output: Resolved output path.
            lexical: Output path as supplied.
        """
        super().__init__(
            f"refusing to write {output}: path exists; use 'install' when "
            "replacement, backup, and rollback semantics are required",
            paths=_pair(lexical, output),
        )


class ShadowedError(CompilerError):
    """A non-empty global override would replace the target."""

    state: BundleState | None = BundleState.SHADOWED

    def __init__(self, *, override: Path, target: Path) -> None:
        """Compose a shadowing diagnostic.

        Args:
            override: The non-empty override path.
            target: The target it would shadow.
        """
        super().__init__(
            f"{override} is non-empty and would be loaded instead of {target}; "
            "resolve the override explicitly",
            paths=_pair(str(override), override),
        )


class UnmanagedTargetProblem(StrEnum):
    """Why an unmanaged target was refused."""

    NO_AUTHORIZATION = "replacing an unmanaged target requires --replace-unmanaged"
    DIGEST_REQUIRED = "--replace-unmanaged requires --expect-target-sha256"
    DIGEST_MISMATCH = "target digest does not match --expect-target-sha256"


class UnmanagedTargetError(CompilerError):
    """The existing target is not owned by this compiler."""

    state: BundleState | None = BundleState.UNMANAGED_TARGET

    def __init__(
        self, problem: UnmanagedTargetProblem, *, target: Path, detail: str = ""
    ) -> None:
        """Compose an unmanaged-target diagnostic.

        Args:
            problem: Why the target was refused.
            target: Resolved target path.
            detail: Observed digest, declared format, or other short specifics.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        parts.append(f"at {target}")
        self.problem = problem
        super().__init__(" ".join(parts), paths=_pair(str(target), target))


class ConcurrentChangeProblem(StrEnum):
    """What changed after a precondition was captured."""

    TARGET_CHANGED = "target changed after its precondition was captured"
    TARGET_APPEARED = "target appeared after it was observed to be missing"
    TARGET_VANISHED = "target disappeared after it was observed to exist"
    TARGET_BECAME_SYMLINK = "target became a symbolic link"
    LOCK_CHANGED = "lock changed after its precondition was captured"
    BACKUP_EXISTS_DIFFERENT = "an existing backup has different content"


class ConcurrentChangeError(CompilerError):
    """A watched path changed under the operation, so it was refused."""

    state: BundleState | None = BundleState.CONCURRENT_CHANGE

    def __init__(
        self, problem: ConcurrentChangeProblem, *, path: Path, detail: str = ""
    ) -> None:
        """Compose a concurrency diagnostic.

        Args:
            problem: What changed.
            path: Resolved path that changed.
            detail: Expected and observed digests, or other short specifics.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        parts.append(f"at {path}")
        self.problem = problem
        super().__init__(" ".join(parts), paths=_pair(str(path), path))


class TargetProblem(StrEnum):
    """Why a target path itself is unusable."""

    SYMLINK = "target is a symbolic link and will not be followed"
    NOT_A_FILE = "target exists and is not a regular file"
    PARENT_MISSING = "target parent directory does not exist"
    PARENT_NOT_A_DIRECTORY = "target parent is not a directory"
    UNREADABLE = "target could not be read"


class TargetError(CompilerError):
    """The target path cannot be used safely."""

    def __init__(self, problem: TargetProblem, *, target: Path, lexical: str) -> None:
        """Compose a target diagnostic.

        Args:
            problem: Why the target is unusable.
            target: Resolved target path.
            lexical: Target path as supplied.
        """
        self.problem = problem
        super().__init__(f"{problem.value}: {target}", paths=_pair(lexical, target))


class ReceiptProblem(StrEnum):
    """Why a receipt was rejected."""

    MISSING = "receipt does not exist"
    SYMLINK = "receipt is a symbolic link"
    NOT_A_FILE = "receipt is not a regular file"
    OUTSIDE_STATE_ROOT = "receipt is not under the expected bundle state root"
    SYNTAX = "receipt is not valid JSON"
    NOT_AN_OBJECT = "receipt root is not an object"
    SCHEMA = "receipt does not match schema version 1"
    UNSUPPORTED_VERSION = "unsupported receipt_schema_version"
    WRONG_OPERATION = "receipt does not record an install"
    TARGET_MISMATCH = "receipt target does not match this invocation"
    BUNDLE_MISMATCH = "receipt bundle_id does not match this invocation"
    BACKUP_OUTSIDE_STATE_ROOT = "receipt backup path escapes the expected state root"
    BACKUP_MISSING = "receipt backup file does not exist"
    BACKUP_DIGEST_MISMATCH = "receipt backup content does not match its recorded digest"
    NO_BACKUP_RECORDED = "receipt records no backup to restore"


class ReceiptError(CompilerError):
    """A receipt is missing, malformed, or not usable for this invocation."""

    def __init__(
        self,
        problem: ReceiptProblem,
        *,
        receipt: Path,
        lexical: str | None = None,
        detail: str = "",
    ) -> None:
        """Compose a receipt diagnostic.

        Args:
            problem: Why the receipt was rejected.
            receipt: Resolved receipt path.
            lexical: Receipt path as supplied.
            detail: Field name, expected value, or other short specifics.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        parts.append(f"at {receipt}")
        self.problem = problem
        super().__init__(" ".join(parts), paths=_pair(lexical, receipt))

    @classmethod
    def at(
        cls,
        problem: ReceiptProblem,
        path: Path,
        lexical: str | None = None,
        detail: str = "",
    ) -> Self:
        """Build a receipt refusal positionally.

        Receipt validation refuses in many places with the same three fields, so a
        factory keeps each call site short while leaving the class name visible.

        Args:
            problem: Why the receipt was rejected.
            path: Resolved receipt path.
            lexical: Receipt path as supplied.
            detail: Field name, expected value, or other short specifics.

        Returns:
            The error to raise.
        """
        return cls(problem, receipt=path, lexical=lexical, detail=detail)


class MutationProblem(StrEnum):
    """Why a filesystem mutation failed or was refused."""

    WRITE_FAILED = "writing the temporary file failed"
    SYNC_FAILED = "flushing or syncing the temporary file failed"
    REPLACE_FAILED = "atomically replacing the target failed"
    PERMISSION_FAILED = "applying the target permission mode failed"
    BACKUP_FAILED = "writing the backup failed"
    STATE_ROOT_FAILED = "creating the bundle state directory failed"
    LOCK_UNAVAILABLE = "acquiring the advisory lock failed"
    POSTCONDITION_FAILED = "the installed bytes do not match what was rendered"
    RECOVERY_FAILED = "recovering the prior target after a failed install failed"


class MutationError(CompilerError):
    """A mutation could not be completed safely."""

    def __init__(
        self, problem: MutationProblem, *, path: Path | None = None, detail: str = ""
    ) -> None:
        """Compose a mutation diagnostic.

        Args:
            problem: Which mutation step failed.
            path: Resolved path involved, when one applies.
            detail: Underlying error text or other short specifics.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        if path is not None:
            parts.append(f"at {path}")
        self.problem = problem
        super().__init__(
            " ".join(parts), paths=_pair(str(path), path) if path is not None else None
        )


class CodexProblem(StrEnum):
    """Why Codex runtime verification did not complete."""

    EXECUTABLE_MISSING = "codex was not found on PATH"
    VERSION_FAILED = "codex --version failed"
    CAPABILITY_MISSING = "the installed codex does not expose 'debug prompt-input'"
    PROBE_FAILED = "codex debug prompt-input exited nonzero"
    PROBE_TIMEOUT = "codex debug prompt-input exceeded the timeout"
    INVALID_JSON = "codex debug prompt-input did not return valid JSON"
    HEADER_ABSENT = "the generated header is not present in the prompt input"
    MARKER_ABSENT = "a module marker is not present in the prompt input"
    MARKER_DUPLICATED = "a module marker appears more than once in the prompt input"
    SENTINEL_ABSENT = "a module content sentinel is not present in the prompt input"
    PROBE_CONTAMINATED = "the probe itself contains an expected marker or sentinel"
    OUTPUT_TOO_LARGE = "codex output exceeded the captured size limit"


class CodexVerificationError(CompilerError):
    """Runtime verification did not pass, so the state is not ``CURRENT``."""

    state: BundleState | None = BundleState.RUNTIME_UNVERIFIED

    def __init__(
        self,
        problem: CodexProblem,
        *,
        detail: str = "",
        command: tuple[str, ...] = (),
    ) -> None:
        """Compose a runtime verification diagnostic.

        Args:
            problem: Which verification step failed.
            detail: Observed exit status, marker identifier, or other specifics.
            command: Exact argument vector used, for reproduction.
        """
        parts = [problem.value]
        if detail:
            parts.append(f"({detail})")
        if command:
            parts.append("running: " + " ".join(command))
        self.problem = problem
        self.command = command
        super().__init__(" ".join(parts))

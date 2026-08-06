# Security model

What this tool defends against, what it deliberately does not, and how to recover
when something goes wrong.

## Trust boundaries

The operator-reviewed manifest is the trust boundary. Everything the compiler
reads, it reads because a human listed it in a file they reviewed.

| Input                   | Trust                                                                              |
|-------------------------|------------------------------------------------------------------------------------|
| Manifest                | Trusted structure, validated strictly, must be reviewed by a human                 |
| Canonical source bytes  | Trusted to be the operator's chosen content; the content itself is untrusted prose |
| Existing lock           | Untrusted input, schema-validated, never a trust signal                            |
| Existing target         | Untrusted input, header-checked and whole-file hashed, never followed as a symlink |
| Install receipt         | Untrusted input, schema-validated, path-constrained before use                     |
| Codex prompt-input JSON | Untrusted input, parsed defensively, only searched for markers                     |

### Compiled policy content is not sanitized

Hashing and compilation preserve provenance. They do not make prose safe. A
canonical source may contain instructions that are wrong or actively hostile
toward whatever agent later reads the compiled bundle. A digest proves the bytes
did not change between locking and rendering; it establishes neither authorship
nor trustworthiness.

The compiler therefore never edits, lints, reflows, normalizes, summarizes, or
rewrites policy prose. Anything that changed those bytes would have to be trusted
to change them correctly, and no such component exists here.

A manifest may intentionally name files outside its own directory. That is
allowed and visible in the reviewed manifest. The review of the manifest, not a
path restriction, is what bounds the source set in schema version 1.

## Path and symlink handling

- A leading `~` or `~user` expands through the standard home-directory lookup.
  Nothing else expands: no environment variables, no command substitution, no
  globbing. `$HOME/x.md` is a literal relative path with a `$HOME` directory
  component.
- Manifest `source` and `default_target` resolve against the manifest directory,
  never the process working directory, so moving the process cannot change which
  files a reviewed manifest selects.
- A source whose final path element is a symlink is rejected, never followed. The
  diagnostic reports both the lexical and resolved path so an operator can see
  what the link pointed at. An ancestor directory may be a symlink because the
  operator-reviewed manifest, rather than real-path containment, bounds the source
  set in schema version 1.
- A symlinked target is rejected, never followed. Following it would let a link
  redirect a privileged write.
- A source that is a directory, device, socket, or FIFO is rejected. Only regular
  files are accepted.
- A source whose resolved path equals the resolved target is rejected, so the
  compiler cannot read its own output.
- A source path that is not representable as UTF-8 is rejected, because the lock
  must record it unambiguously.
- Every path recorded in a diagnostic or receipt keeps both its lexical and its
  resolved form where the distinction affects safety.

## Time-of-check to time-of-use

A file can change between the moment it is checked and the moment it is used.
Every such window in this tool is either closed or detected:

| Window                                    | Mitigation                                                                                                                   |
|-------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| Source validated, then read again         | The source is read once into a validated byte snapshot; hashing and rendering both use that snapshot, never a second read    |
| Source stat'd, then opened                | The opened file descriptor is re-stat'd after opening, and a device, inode, or size change is a failure                      |
| Lock read, then replaced                  | `lock` retains the pre-operation state and digest, acquires an advisory lock, rechecks both, and refuses `CONCURRENT_CHANGE` |
| Target digest captured, then written      | The digest is recaptured after the advisory lock is held, and a mismatch refuses `CONCURRENT_CHANGE`                         |
| Target inspected, then replaced           | Target identity is rechecked under the lock, and a symlink appearing late is refused                                         |
| Receipt digest captured, then rolled back | The current target digest must equal the receipt's installed digest at rollback time                                         |

Advisory locks coordinate cooperating compiler instances. They cannot stop an
uncooperative process and they do not guarantee semantics on a network
filesystem. That is precisely why digest preconditions and post-write verification
remain mandatory rather than optional.

## Output clobbering

- `render --output PATH` refuses every existing path. No backup, no replacement,
  no `--force`.
- `install` is the only command that replaces a file, and only with `--apply`.
- Replacing a target with no recognized generated header additionally requires
  `--replace-unmanaged` and `--expect-target-sha256` carrying the digest captured
  immediately before the dry run. A mismatch refuses rather than overwrites.
- Installation writes a same-directory temporary file, flushes it, `fsync`s it,
  atomically replaces the target, and `fsync`s the containing directory where the
  platform supports it. An interrupted install leaves either the complete old
  bytes or the complete new bytes, never a partial file.
- An existing target's permission bits are preserved. A newly created target gets
  owner read and write only.
- Backups and receipts are recorded before success is reported. Backups are never
  deleted or rotated automatically.

## Marker and identifier injection

Rendered output uses compiler-owned ASCII markers that carry each module's
identity. Two rules make forging them structurally impossible:

1. Every `bundle_id` and module `id` must match `^[a-z][a-z0-9-]{0,63}$` before it
   can reach a marker. The renderer never escapes an identifier; it refuses an
   invalid one.
2. Any source containing the byte prefix `<!-- agents-md-compiler:` anywhere is
   rejected. A source therefore cannot forge, terminate, or nest a marker.

Freshly rendered output is verified by structurally parsing it against the lock,
not by searching for substrings. A short module's bytes can legitimately occur
inside a longer module's text, so counting occurrences would prove nothing. An
existing target is recognized by its exact generated header and compared with the
fresh render by a whole-file digest.

## Unicode and control characters

Source validation rejects a UTF-8 BOM, any NUL byte, any CR byte, invalid UTF-8,
an empty file, and a missing final LF. Those are the byte-level failures that
silently corrupt a concatenated document or smuggle content past a reviewer.

Beyond that, the compiler does not police the Unicode content of policy prose. It
does not strip bidirectional controls, normalize homoglyphs, or reject zero-width
characters, because doing so would mean editing policy bytes, and this tool's
entire guarantee is that it does not. A source repository that cares about those
properties should enforce them on the canonical sources, which is exactly where
the operator's own policy gates run.

Rendered markers, the generated header, and every identifier are pure printable
ASCII, so the compiler-owned portion of the output cannot itself carry a
bidirectional or invisible control.

## No shell, no network

- The runtime performs no network access. There is nothing to configure, proxy, or
  intercept.
- The compiler never invokes a shell. The only subprocess is `codex`, resolved from
  `PATH` and invoked with an explicit argument vector, no shell, a finite timeout,
  and bounded captured output.
- The Codex probe runs from a disposable directory that contains no project
  instruction file. No policy file is copied into it, and the probe text, directory
  name, and argument vector are checked to contain none of the expected markers or
  sentinels, so a marker found in the prompt input cannot have come from the probe
  itself.
- `verify-codex` sends no model request and requires no API authentication.

## Information disclosure

- Canonical policy content never appears in an error message, a status report, a
  lock, a receipt, or a JSON envelope. Only `render` to stdout emits policy bytes.
- Diagnostics do reveal paths and digests, because an operator cannot resolve a
  path or drift failure without them.
- A backup contains the exact prior target bytes. That is the point of a backup,
  and it is why state directories and state files are created with owner-only
  permissions on POSIX systems.
- No secret is read, stored, or logged. The tool has no credential of any kind.

## File permissions

- POSIX: state directories are created with owner-only access and state files with
  owner-only read and write, subject to a more restrictive existing mode. An
  existing state root, backup, receipt, manifest, lock, or target never has its
  permissions broadened.
- Windows: state lives in the current user's private local application data
  directory, and existing restrictive ACL behavior is preserved.
- The standard library cannot prove ACL equivalence on Windows. Mode behavior is
  tested on POSIX; on Windows the tool relies on the private per-user location and
  does not claim an equivalent ACL guarantee.

## Receipt handling

A receipt path is untrusted input, so before either of its recorded paths is read
or written:

1. the receipt must be a regular file and not a symbolic link;
2. it must live under the expected per-bundle state root for the current
   invocation;
3. it must validate against the receipt schema;
4. its recorded target must match the target resolved for this invocation;
5. its recorded backup path must live under the same expected state root.

A forged receipt pointing at an arbitrary target, a receipt symlink, and a receipt
whose backup path escapes the state root are all refused before any I/O on the
referenced paths.

## Recovery

| Situation                                   | Action                                                                                                    |
|---------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Target drifted from the lock                | Inspect with `check --format json`, then reinstall or roll back deliberately                              |
| Lock stale after editing a canonical source | Rerun `lock`; never hand-edit a lock                                                                      |
| Installed bundle is wrong                   | `rollback --receipt PATH --apply`, which refuses unless the target still matches the receipt              |
| Target changed under you                    | The refusal is `CONCURRENT_CHANGE`; recapture state before retrying, and do not force                     |
| Installation created a target you want gone | Rollback moves the generated file into the state directory instead of deleting it                         |
| Backup appears corrupted                    | Rollback verifies the backup digest before restoring and refuses on mismatch                              |
| Codex debug interface changed               | `verify-codex` reports `RUNTIME_UNVERIFIED` with the exact command and failure; static gates remain valid |
| A non-empty global override exists          | The refusal is `SHADOWED`; resolve the override explicitly, since Codex would load it instead             |

## Out of scope

- The content, correctness, or safety of the policy modules an operator compiles.
- Vulnerabilities in the Codex CLI itself.
- Protecting a target from a process that ignores advisory locks.
- Guaranteeing atomic replacement semantics on a network filesystem that does not
  provide them.
- Windows ACL equivalence claims beyond using the private per-user data directory.

Report a vulnerability privately through the path in `SECURITY.md`.

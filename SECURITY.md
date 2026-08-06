# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

- Preferred: open a private report through GitHub Security Advisories, using the
  **"Report a vulnerability"** button on this repository's **Security** tab.
- Alternatively: email <enterprise.code.developer@gmail.com>.

Include a description of the issue plus steps to reproduce or a proof of concept.
Reports are acknowledged within a few business days, with status updates through
remediation.

## Supported versions

Only the latest released version is supported. Fixes ship as new releases rather
than as patches to older versions.

## Scope

In scope:

- the source in this repository and the published sdist and wheel;
- path handling, symlink refusal, and containment checks;
- the atomic installation, advisory locking, backup, receipt, and rollback paths;
- marker and identifier validation in rendered output;
- the `codex` subprocess invocation used by `verify-codex`;
- the GitHub Actions workflows under `.github/` that build and publish releases,
  including token scope, action pinning, and OIDC publishing.

Out of scope:

- the content of the Markdown policy modules an operator chooses to compile;
- vulnerabilities in the Codex CLI itself;
- the operator's own manifest, target path, and file permission decisions beyond
  what this tool documents and enforces.

## Compiled policy content is operator-trusted input, not sanitized content

This compiler is a provenance and determinism tool. It reads exactly the files an
operator listed in a reviewed manifest, records their SHA-256 digests, and copies
their accepted bytes verbatim into one output file.

It does not, and cannot, make that prose safe. A canonical source may contain
instructions that are wrong, hostile, or adversarial toward whatever agent later
reads the compiled bundle. Hashing proves that the bytes did not change between
locking and rendering. It establishes neither authorship nor trustworthiness.

The operator's review of the manifest is the trust boundary. The compiler refuses
symlinked sources, rejects sources that contain a compiler marker line, validates
every module identifier before it reaches a generated marker, and never executes
a shell, but it deliberately never edits, lints, reflows, or summarizes policy
prose.

## What the compiler never does

- No network access at runtime.
- No shell invocation. The Codex verifier uses an argument vector.
- No policy content in any error message, status report, lock file, or receipt.
- No implicit installation during `render`, `check`, `status`, or `verify-codex`.
- No automatic deletion or rotation of backups.
- No writing to a path that already exists, except through `install --apply`
  with its documented preconditions.

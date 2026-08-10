# CHANGELOG

<!--
After v0.1.0 this file is maintained automatically by python-semantic-release,
driven by Conventional-Commit history on protected main. See
.github/workflows/release.yml and the [tool.semantic_release] section of
pyproject.toml. Manual edits below the first release entry may be overwritten on
the next release.

Version 0.1.0 itself is published through the explicit workflow_dispatch bootstrap
path, because python-semantic-release needs a prior release tag to compare
against.
-->

## Unreleased

### Added

- Deterministic compiler for one explicit global, repository, nested, or custom
  `AGENTS.md` target per manifest, with strict parsing, explicit lock refresh,
  exact source-byte preservation, and read-only state comparison.
- `init`, `lock`, `validate`, `render`, `check`, `status`, `install`, `rollback`,
  `verify-codex`, and `version` subcommands, with text and single-object JSON
  output and a stable exit-code contract.
- Concurrency-safe, atomic installation with per-target advisory locking,
  precondition hashes, immutable backups, install receipts, and receipt-based
  rollback.
- Codex model-visible prompt-input verification that inspects the real startup
  input rather than trusting a model summary.
- Frozen public format documentation for the manifest, lock, rendered bundle,
  CLI contract, and security model, plus JSON schemas shipped as package data.

### Changed

- Lock format 2 records lexical source paths so canonical locks are portable
  across checkout roots. Rendered format 2 uses a scope-neutral title. Strict
  format-1 artifacts remain migration input, and exact format-1 targets remain
  managed for explicit upgrade and receipt-based rollback.
- `init` now defaults to `agents-md.toml`, accepts an explicit target, and emits
  portable manifest targets. The legacy `global-agents.toml` name remains an
  unambiguous fallback when no neutral default exists.
- Operational evidence is qualified by bundle and target. Advisory locks use a
  distribution-wide target identity so different bundles cannot concurrently
  mutate the same path. Typed install and rollback callers now provide that
  shared lock directory explicitly.
- `verify-codex` isolates active global targets and verifies project or nested
  targets from an explicit valid startup directory.
- This repository now dogfoods a compiled project-local `AGENTS.md` from
  canonical policy under `.agents/`.

### Fixed

- `init` scaffolds an explicit persistent target, and the quickstart explains
  global and repository targets, one-invocation overrides, standalone output,
  and required parent directories.
- Target and render-output dry runs now reject a missing or non-directory parent
  before reporting the destination as merely missing.
- A failure after target replacement now runs verified recovery under the same
  advisory lock. Exact prior bytes are restored from the immutable backup; a
  target created by the failed operation is preserved under private state while
  the destination returns to its prior missing state. Recovery never overwrites
  bytes that no longer match the failed operation.
- `status` could report a receipt that was not the most recent operation. Receipt
  names led with the operation, so every `install` name sorted before every
  `rollback` name and the newest-last ordering broke as soon as the two
  alternated. Rolling back "the latest receipt" could therefore restore the bytes
  from before an earlier install. Names now lead with a microsecond-precision UTC
  stamp, which also removes the tie between two mutations recorded in the same
  second, where the winner had been decided by a random operation identifier.

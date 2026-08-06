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

- Deterministic compiler for a single global `AGENTS.md` bundle: strict manifest
  parsing, explicit lockfile refresh, format-version-1 rendering with exact
  source-byte preservation, and read-only state comparison.
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

### Fixed

- `init` now scaffolds the actual global `~/.codex/AGENTS.md` target instead of a
  project-local `AGENTS.md`, and the quickstart explains persistent targets,
  one-invocation overrides, standalone output, and required parent directories.
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

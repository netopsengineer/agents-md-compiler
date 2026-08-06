# CLAUDE.md

@AGENTS.md

## Claude-specific operating mechanics

`AGENTS.md` above is the canonical project contract. This file adds only the
mechanics that are specific to running Claude Code in this repository.

- `.claude/settings.json` registers two hooks. `UserPromptSubmit` runs
  `.claude/hooks/inject-discipline.sh`, which re-asserts the operating discipline
  each turn. `Stop` runs `.claude/hooks/enforce-done.sh`, which blocks completion
  while `prek run --files` is red for the changed files.
- Both hooks fail open when `prek` is not on `PATH`. A missing gate runner
  produces a warning, never a wedged session. A failing gate is never a reason to
  edit source outside the requested scope.
- `@AGENTS.md` above is a Claude Code import. Codex does not expand `@path`
  imports; it treats them as ordinary Markdown. Do not rely on that line for any
  other agent runtime, and do not add `@` imports to files this project compiles.
- This repository is the compiler, not the operator's policy set. Never edit
  `~/.codex/AGENTS.md`, `~/.codex/config.toml`, or any canonical Vault policy
  source while working on this package.

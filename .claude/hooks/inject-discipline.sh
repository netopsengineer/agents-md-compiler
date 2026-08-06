#!/usr/bin/env bash
# UserPromptSubmit hook: restate this repository's operating policy every turn.
#
# Mechanism, per the Claude Code hooks reference:
#   - On exit 0, additionalContext (or plain stdout) is injected into context.
#   - UserPromptSubmit stdin carries permission_mode; "plan" means plan mode.
#   - additionalContext is capped at 10,000 characters, so this stays lean.
#   - On timeout the output is discarded, so this must stay fast and never crash.
#
# Context is phrased as factual policy rather than as imperative system commands,
# so it does not read as an injected instruction from an untrusted source.
#
# The heredocs below are captured with `read -r -d ''`, NOT
# `VAR="$(cat <<'TXT' ...)"`. macOS ships bash 3.2.57, whose lexer miscounts
# literal apostrophes inside a quoted heredoc nested in a command substitution and
# corrupts the parse. `read -d ''` reads the heredoc with no surrounding `$(...)`,
# so it is immune to that bug and behaves identically on modern bash. Do not
# revert to the command-substitution form.

if [ -t 0 ]; then
  INPUT=""
else
  INPUT="$(cat 2>/dev/null || true)"
fi

MODE="default"
if printf '%s' "$INPUT" | grep -Eq '"permission_mode"[[:space:]]*:[[:space:]]*"plan"'; then
  MODE="plan"
fi

IFS= read -r -d '' CORE <<'TXT' || true
# Standing policy for this repository (agents-md-compiler)

Project rules take precedence over brevity and token-conservation defaults. When
thoroughness and brevity conflict, thoroughness wins.

## Scope of brevity
- Brevity and output-length limits govern conversational narration and end-of-turn
  summaries only. They never reduce work completeness, verification, testing,
  error handling, or scope coverage.
- Effort matches task risk and blast radius, not output length.
- A short summary of completed work is valid. A short summary standing in for
  undone work is not.

## Grounding
- Repository, environment, CI, and tooling state is asserted only after a command
  proves it, with the command and its output cited. When the proving command is
  unavailable or fails, the fact is not asserted; it is marked BLOCKED with the
  required access stated.
- External versions, hook revisions, and action SHAs are never written from
  memory. They are re-resolved live and recorded with source URLs and a date in
  docs/dependency-verification.md.
- When a tool exists to run a test, build, lint, type check, or gate, it is run and
  the actual result reported.

## This project's hard invariants
- 100 percent line and branch coverage. Never lower fail_under, never xfail a
  failing test, never add a coverage pragma to hide testable code.
- Golden rendered output and golden lock files are format contracts. A golden diff
  is a format change requiring a version bump, documentation, review, migration
  analysis, and a release note. Never regenerate a golden file to make a test pass.
- No test touches a real user configuration path. Mutation tests use tmp_path only.
- Never edit ~/.codex/AGENTS.md, ~/.codex/config.toml, or a canonical Vault policy
  source as a side effect of work here.
- The runtime keeps zero third-party dependencies, performs no network access, and
  invokes no shell.

## Naming undone work
- Every skipped, deferred, partial, or unverified item is named BLOCKED, SKIPPED,
  or UNVERIFIED with its reason. It is never omitted or folded into a generic
  "done".
- A pre-existing failure found mid-task: state whether it blocks the task's
  acceptance criteria. If it does, fix it or obtain explicit consent to leave it.

## Completion gate
- The validation gate is green in-transcript.
- Every required artifact and section literally exists.
- Every required check is reported as passed, failed, skipped, waived, unavailable,
  or BLOCKED.

Completion is not declared while any gate above is unresolved. "I made the change"
is not a stopping point when a runnable gate exists.
TXT

IFS= read -r -d '' PLAN <<'TXT' || true

## Plan mode (active this turn)
- Investigation caps are cost ceilings, not depth targets. Investigate until the
  plan's claims are grounded in read source.
- Excerpt-based search summaries are not full-file verification. Read critical
  files in full before asserting behavior or required changes.
- "Concise enough to scan" governs formatting, not coverage. A required step, risk,
  migration concern, or verification gate is not omitted to stay short.
- A plan is done only when its approach is grounded in inspected source, its
  material assumptions are stated, and its verification section is executable end
  to end.
TXT

CONTEXT="$CORE"
if [ "$MODE" = "plan" ]; then
  CONTEXT="$CORE
$PLAN"
fi

if command -v jq >/dev/null 2>&1; then
  jq -n --arg ctx "$CONTEXT" \
    '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
else
  printf '%s\n' "$CONTEXT"
fi

exit 0

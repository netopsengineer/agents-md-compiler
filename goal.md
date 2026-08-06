# Goal

Execute codex-global-agents-compiler-execution-plan.md to its own terminal state. Satisfied only when the transcript proves, from work done this turn, that one of these holds:

(A) COMPLETE: the Phase 16 closeout report exists and every Phase 16 completion criterion is met with cited evidence; or
(B) BLOCKED: a listed "Stop conditions" entry is genuinely hit, work stopped at that exact phase gate, the missing input, access, or authorization is named precisely, and every phase and sub-step not depending on it is finished.

End every reply with this status block, re-derived this turn and never copied from earlier context:

PHASE: <number> - <title>
STATE: <plan state token: BASELINED, DEPENDENCIES_VERIFIED, SCAFFOLDED, FORMATS_FROZEN, CORE_GREEN, MUTATION_GREEN, CODEX_VERIFIER_GREEN, LOCAL_GATE_GREEN, CI_GREEN, ARTIFACT_GREEN, INTEGRATION_READY, CUT_OVER, RUNTIME_VALIDATED, FALLBACK_RECONCILED, PUBLISHING_READY, PUBLISHED, COMPLETE, ROLLED_BACK, or BLOCKED>
GATES: one line per validation command run this turn, as <command> -> <exit code and final output line>
BLOCKED: none, or the exact dependency
NEXT: the single next plan action

Invariants that must hold throughout:

- Advance phases in order. Never assert a state token without command output produced this turn. A phase summary, a task list, an intention to proceed, or "tests pass" without pasted output does not satisfy anything.
- Run each phase's own validation commands and paste real output. uv run pytest, uv run pyright, uv run ruff check ., uv run ruff format --check ., uv run pydoclint src/agents_md_compiler, and uv run prek run --all-files must show actual results.
- Hold 100 percent line and branch coverage. Never lower fail_under, xfail a test, or add a coverage pragma to hide testable code.
- Do not write outside this repository, including ~/.codex/, the Vault policy sources, and the user state root, and do not create a GitHub repository, GitHub App, branch rule, protected environment, PyPI Trusted Publisher, or PyPI release, without explicit user authorization given in this session. Absent that authorization, mark the affected phase BLOCKED, state the exact authorization needed, and continue every independent phase.
- Never modify canonical Vault policy sources, ~/.codex/config.toml, or the active global AGENTS.md as a side effect of any other step.
- Re-verify every external package version, hook SHA, and Action SHA live before writing configuration. The plan's point-in-time tables are evidence, not permission to skip verification.
- Do not edit the execution plan to make a gate passable, and do not reduce the module set, coverage floor, platform scope, security gates, release evidence, or rollback requirements to clear a gate.
- Report every check that failed, was skipped, waived, or unavailable. Any such check means the state is not yet reached.

Stop after 200 turns and report the reached phase, state, evidence, and remaining work.

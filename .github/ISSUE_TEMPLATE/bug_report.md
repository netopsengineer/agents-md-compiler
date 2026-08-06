---
name: Bug report
about: Report incorrect behavior in the compiler or the CLI
title: ""
labels: bug
assignees: ""
---

## What happened

<!-- The observed behavior. -->

## What you expected

<!-- Cite the documented contract where you can: docs/cli-contract.md for states and
exit codes, docs/rendered-format-v1.md for output bytes, docs/manifest-v1.md for
manifest rules. -->

## Reproduction

The most useful report is a command sequence someone else can run. `init` scaffolds a
throwaway bundle, so a reproduction usually needs no private content:

```bash
agents-md-compiler init --directory bundle --bundle-id example
agents-md-compiler lock --manifest bundle/global-agents.toml
# the command that misbehaved
```

## Diagnostics

`status --format json` reports the full state without printing any policy content.
Please include it, plus the exit code of the failing command:

```bash
agents-md-compiler status --manifest path/to/global-agents.toml --format json
echo "exit code: $?"
```

## Environment

- `agents-md-compiler --version`:
- `python --version`:
- Operating system and version:
- Installed with (`uv tool`, `uvx`, `pip`, other):

## Do not paste policy content

Your modules are your own instructions. Digests, sizes, state tokens, and exit codes
are enough to diagnose almost every defect. If a specific byte sequence is genuinely
the trigger, a minimal synthetic module that reproduces it is better than the real
file.

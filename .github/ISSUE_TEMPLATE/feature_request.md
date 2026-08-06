---
name: Feature request
about: Propose a capability or a change to an existing contract
title: ""
labels: enhancement
assignees: ""
---

## The problem

<!-- What you are trying to accomplish, and what currently prevents it. Please
describe the problem before the proposed solution: there may be a way to do this
already. -->

## Proposed behavior

<!-- What the tool would do. If this is a new subcommand or option, state its exact
name, its inputs, and its output. -->

## Effect on the frozen contracts

This tool's value depends on its output being byte-stable and its states being
predictable, so a change to any of the following is a versioning decision:

- rendered output bytes (`docs/rendered-format-v1.md`)
- the manifest schema (`docs/manifest-v1.md`)
- the lock format, the receipt schema, or the published JSON schemas
- a CLI state token, exit code, or JSON field (`docs/cli-contract.md`)

Does your proposal change any of these? If so, can it be expressed as a new optional
field or a new command instead?

## Scope this project deliberately excludes

Please check these before filing, so the discussion starts in the right place:

- Nested or per-project `AGENTS.md` generation. This tool compiles one global file.
- Rewriting, summarizing, reformatting, or linting the policy content itself. Module
  bytes are preserved exactly, on purpose.
- Runtime dependencies. The package ships none, and a proposal that needs one has to
  justify the supply-chain cost.

If your request falls in one of those areas, it may still be worth discussing, but it
needs a case for changing the boundary rather than an assumption that it should move.

## Alternatives you considered

<!-- Including doing nothing. -->

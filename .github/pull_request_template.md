# Pull request

## What changed and why

<!-- State the behavior change, not the file list. If this fixes a defect, state the
mechanism that was wrong. -->

## Type of change

- [ ] Defect fix (behavior was wrong)
- [ ] New capability
- [ ] Refactor (no observable behavior change)
- [ ] Documentation
- [ ] Dependency, tooling, or CI change
- [ ] Public format or CLI contract change

## Gates

Run `uv run prek run --all-files` and paste the result rather than asserting it
passed.

- [ ] `uv run prek run --all-files` passes with no edits on a second run
- [ ] Coverage is still 100 percent line and branch, with no new `pragma: no cover`
- [ ] A defect fix has a regression test that fails before the fix
- [ ] A behavior change has a contract test

## Compatibility

Answer both. "Not applicable" is a valid answer; silence is not.

- Does this change rendered output bytes, the manifest schema, the lock format, the
  receipt schema, a CLI state token, an exit code, or a JSON field?
- If yes, which format version was incremented, and what happens to an artifact
  written by the previous version?

## Security

- Does this change how untrusted input is validated, how paths are contained, how
  locks are taken, or how files are replaced?
- Does this add a runtime dependency? The package ships zero, so any addition needs
  an explicit reason.

## Dependency and pin changes

- [ ] Every version was verified against a live source, not from memory
- [ ] Every new or changed pin is an immutable commit SHA with its tag recorded
- [ ] `docs/dependency-verification.md` records the evidence

## Notes for the reviewer

<!-- Anything you deliberately did not do, and why. A stated omission is easier to
review than a discovered one. -->

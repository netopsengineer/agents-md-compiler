# Contributing

Thanks for helping. This project keeps a narrow scope on purpose: read
[`AGENTS.md`](AGENTS.md) for what is in scope and what will be declined.

## Local setup

Requires Python 3.14 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/netopsengineer/agents-md-compiler
cd agents-md-compiler
uv sync --locked
uv run prek install -f   # optional: run the gate on every commit
```

`uv sync --locked` fails if `uv.lock` is out of date with `pyproject.toml`. That
is intentional: a dependency change that skipped `uv lock` cannot merge.

## Verifying locally

One command runs the whole gate, and it is exactly what CI runs:

```bash
uv run prek run --all-files --show-diff-on-failure --color always
```

The individual commands, if you want to run one at a time:

```bash
uv run pytest
uv run pyright
uv run ruff check .
uv run ruff format --check .
uv run pydoclint src/agents_md_compiler
uv run bandit -c pyproject.toml -r src
```

Coverage is enforced at 100 percent line and branch on every `pytest` run. Do not
lower `fail_under`, mark a failing test `xfail`, or add a coverage pragma to hide
testable code. If a branch genuinely cannot be reached under test, say why in the
pull request and name the platform or runtime condition.

For a Markdown change, run the table formatter, the fixing linter, the non-fixing
linter, and a Unicode scan on each edited path.

## Commit and PR conventions

Commits and pull request titles follow
[Conventional Commits](https://www.conventionalcommits.org/). The squash merge
uses the pull request title as the release-relevant commit message, and CI lints
it.

Allowed types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`,
`revert`, `style`, `test`. Use `!` or a `BREAKING CHANGE:` footer for a breaking
change.

Which type triggers what, once automated releases are enabled: `fix` selects a
patch release, `feat` a minor release, and a breaking marker a major release.
That automatic release runs only when the exact push also changes a published
package input: `LICENSE`, `README.md`, `pyproject.toml`, or a file under
`src/agents_md_compiler/`. Repository-only changes do not publish a package even
when the commit is named `fix` or `feat`. `chore`, `ci`, `docs`, `style`, `test`,
`build`, and `refactor` produce no automatic release. An operator can dispatch
the explicit `release` path with a selected semantic level when a repository-only
publication is intentional.

## Dependency updates

Never write a version, hook revision, or action SHA from memory.

- Re-resolve every version live, checking both `/releases/latest` and `/tags` for
  each upstream repository.
- Pin GitHub Actions and hook repositories to immutable commit SHAs with a comment
  naming the verified tag. Dereference an annotated tag to its commit.
- Run an advisory scan for each exact selected version.
- Record the evidence, with source URLs and the date, in
  [`docs/dependency-verification.md`](docs/dependency-verification.md).

Dependabot opens one grouped pull request per ecosystem (`uv`, `pre-commit`,
`github-actions`). Refresh the lock with `uv lock`, never by editing `uv.lock`.

## Changing a public format

The manifest schema, the rendered bundle format, the CLI contract, and the JSON
envelopes are public contracts. A change to rendered output is not a test update.
It requires all of:

1. a new format version, or a proven backward-compatible correction;
2. updated documentation in `docs/`;
3. explicit review of the golden-file diff;
4. migration and rollback analysis;
5. a release note;
6. full integration and active prompt verification.

Never regenerate a golden file to make a test pass.

## Tests

- Unit tests for pure logic; behavioral tests for the CLI's streams, exit codes,
  and JSON envelopes.
- Every documented rejection branch needs a test that triggers it.
- Fixtures that must contain CRLF, a BOM, a NUL byte, or invalid UTF-8 are written
  as bytes by test code, never committed as text files, so repository hygiene
  hooks cannot silently repair them.
- No test may touch a real user configuration path. Mutation tests operate only on
  `tmp_path` fixtures.
- Codex adapter tests use a mock executable and recorded fixtures. They spend no
  tokens and send no model request.

## Security reporting

Do not open a public issue for a security problem. See
[`SECURITY.md`](SECURITY.md) for the private reporting path.

## Release boundaries

Releases are automated and gated; contributors do not publish.

- Do not bump `project.version` by hand. After `v0.1.0`, python-semantic-release
  owns the version and changelog commit. It must not create the tag or GitHub
  release.
- Do not add a PyPI token anywhere. Publishing uses OIDC Trusted Publishing from a
  minimal job that performs no checkout, no dependency install, and no rebuild.
- Create the tag and GitHub release only after PyPI accepts the exact gated
  artifact. If publication succeeded but finalization failed, dispatch `recover`
  with the exact published version and prepared commit; recovery verifies public
  PyPI state and never republishes.
- Do not move or replace an existing tag.

Full gate ordering is in the release-gates section of [`AGENTS.md`](AGENTS.md).

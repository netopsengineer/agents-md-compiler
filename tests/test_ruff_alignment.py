"""Keep both Ruff entry points on the same reviewed version."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_ruff_hook_matches_locked_development_tool() -> None:
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    match = re.search(
        r"repo: https://github\.com/astral-sh/ruff-pre-commit\s+"
        r"rev: [0-9a-f]{40} # frozen: v?(\d+\.\d+\.\d+)",
        hooks,
    )
    assert match is not None
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [
        entry["version"] for entry in lock["package"] if entry["name"] == "ruff"
    ]
    assert versions == [match.group(1)]


def test_uv_group_updates_requirements_not_only_lockfile() -> None:
    config = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    uv_block = config.split("  - package-ecosystem: uv\n", 1)[1].split(
        "  - package-ecosystem:", 1
    )[0]
    assert "    versioning-strategy: increase\n" in uv_block

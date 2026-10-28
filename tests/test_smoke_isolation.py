"""Prove smoke setup confines inherited operator state to its disposable root."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "smoke.sh"


def test_smoke_setup_replaces_inherited_operator_paths(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("the shell smoke gate requires bash")
    workdir = tmp_path / "smoke with spaces"
    operator = tmp_path / "operator"
    operator.mkdir()
    sentinel = operator / "AGENTS.md"
    sentinel.write_text("operator policy\n", encoding="utf-8")
    environment = os.environ.copy()
    for name in ("HOME", "USERPROFILE", "CODEX_HOME", "XDG_STATE_HOME"):
        environment[name] = str(operator)
    setup = SCRIPT.read_text(encoding="utf-8").split("manifest=", 1)[0]
    result = subprocess.run(
        [
            bash,
            "-c",
            setup
            + 'printf "%s\\n" "$HOME" "$USERPROFILE" "${CODEX_HOME-unset}" "$XDG_STATE_HOME"',
            "smoke",
            str(workdir),
            "1.0.1",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    home, profile, codex_home, state = result.stdout.splitlines()
    assert home.endswith("/smoke with spaces/home")
    assert profile == home
    assert codex_home == "unset"
    assert state == f"{home.removesuffix('/home')}/state"
    assert sentinel.read_text(encoding="utf-8") == "operator policy\n"
    assert list(operator.iterdir()) == [sentinel]

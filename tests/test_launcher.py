"""Tests for scripts/py, the launcher that runs a script with the venv's
Python on macOS, Linux and Windows.

The tests need .venv, so run scripts/quickstart.py (or scripts/py --setup)
first, as CI does. Without .venv they stop with a message instead of making
one, because a first setup downloads OR-Tools.

    python3 scripts/py --dev -m pytest tests/
    python3 scripts/py tests/test_launcher.py   (stdlib only)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "scripts" / "py"
VENV_PYTHON = (
    REPO / ".venv" / "Scripts" / "python.exe" if os.name == "nt" else REPO / ".venv" / "bin" / "python"
)


def launch(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    assert VENV_PYTHON.exists(), f"no {VENV_PYTHON}; run scripts/quickstart.py first"
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args], cwd=cwd, capture_output=True, text=True
    )


def test_where_names_the_venv_python_for_this_system():
    result = launch("--where")
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == VENV_PYTHON


def test_runs_in_the_venv_from_the_repo_root_and_passes_the_exit_code():
    code = "import os, sys; print(sys.prefix); print(os.getcwd()); raise SystemExit(7)"
    result = launch("-c", code, cwd=REPO / "tests")
    assert result.returncode == 7, result.stderr
    prefix, cwd = result.stdout.strip().splitlines()
    assert Path(prefix).resolve() == (REPO / ".venv").resolve()
    assert Path(cwd).resolve() == REPO.resolve()


def test_children_run_in_utf8_mode():
    result = launch("-c", "import sys; print(sys.flags.utf8_mode)")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_asks_for_a_command():
    result = launch()
    assert result.returncode == 2
    assert "name a command to run" in result.stderr


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok - {name}")

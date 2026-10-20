#!/usr/bin/env python3
"""One command from a fresh clone to a printed sample schedule, on any OS.

    python3 scripts/quickstart.py          macOS and Linux
    py -3 scripts/quickstart.py            Windows (or: python scripts/quickstart.py)

It makes .venv and installs OR-Tools when they are missing (scripts/py does
this), solves the built-in sample, and opens output/sample_schedule.html.
A second run skips the install. Pass --no-open to skip the browser, for
example in CI. scripts/quickstart.sh runs this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "scripts" / "py"
SAMPLE_HTML = REPO / "output" / "sample_schedule.html"


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0

    setup = subprocess.call([sys.executable, str(LAUNCHER), "--setup"])
    if setup != 0:
        return setup

    solve = subprocess.call([sys.executable, str(LAUNCHER), "solver.py"])
    if solve != 0:
        return solve

    if "--no-open" in argv:
        print(f"Done. The schedule is {SAMPLE_HTML.relative_to(REPO).as_posix()}.")
        return 0
    sys.path.insert(0, str(REPO / "scripts"))
    from run_my_schedule import open_html

    try:
        open_html(SAMPLE_HTML)
    except OSError:
        print(f"Done. Open {SAMPLE_HTML.relative_to(REPO).as_posix()} in your browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

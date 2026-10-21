"""The b2b_weekend penalty, its practice switch and its per-clinician exemption.

One clinician must work two Saturdays in a row, so the schedule is the same
in every run and only the objective moves:

* rules.avoid_b2b_weekends true: the objective holds one b2b_weekend penalty.
* rules.avoid_b2b_weekends false: no penalty.
* allow_b2b_weekends = 1 in clinicians.csv: no penalty for that clinician.
* clinicians.csv without the column (an old file): the penalty, as before.

    python3 scripts/py --dev -m pytest tests/test_b2b_weekends.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEIGHT = 40

HEAD = "clinician_id,name,active,can_or,target_shifts,max_shifts,target_weekend_shifts,max_weekend_shifts"


def objective(clinicians_csv: str, avoid_b2b: bool = True) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data"
        data.mkdir()
        (data / "clinicians.csv").write_text(clinicians_csv)
        (data / "coverage.csv").write_text(
            "date,shift_type,required_count\n2026-06-06,OR,1\n2026-06-13,OR,1\n"
        )
        (data / "requests.csv").write_text(
            "request_id,clinician_id,start_date,end_date,request_type,hard,shift_type,note\n"
        )
        (data / "history.csv").write_text("date,clinician_id,shift_type,status\n")
        config = root / "config" / "rules.json"
        config.parent.mkdir()
        config.write_text(json.dumps({
            "input_dir": "data",
            "output_csv": "output/schedule.csv",
            "rules": {
                "min_days_between_assignments_default": 1,
                "preferred_days_between_assignments": 0,
                "avoid_b2b_weekends": avoid_b2b,
                "weights": {"b2b_weekend": WEIGHT},
            },
        }))
        run = subprocess.run(
            [sys.executable, str(REPO / "solver.py"), "--config", str(config), "--verbose"],
            cwd=REPO, capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        match = re.search(r"^Objective: (-?\d+)$", run.stdout, re.M)
        assert match, run.stdout
        return int(match.group(1))


def roster(allow: str | None) -> str:
    if allow is None:
        return f"{HEAD}\nann,Ann,1,1,2,2,2,2\n"
    return f"{HEAD},allow_b2b_weekends\nann,Ann,1,1,2,2,2,2,{allow}\n"


def test_two_weekends_in_a_row_cost_one_penalty():
    assert objective(roster("0")) - objective(roster("0"), avoid_b2b=False) == WEIGHT


def test_an_old_file_without_the_column_keeps_the_penalty():
    assert objective(roster(None)) == objective(roster("0"))
    assert objective(roster("")) == objective(roster("0"))


def test_a_consented_clinician_gets_no_penalty():
    assert objective(roster("1")) == objective(roster("0"), avoid_b2b=False)
    assert objective(roster("1")) == objective(roster("0")) - WEIGHT


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")

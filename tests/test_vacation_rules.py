"""The two strong soft vacation rules.

avoid_call_after_vacation: no call on the day after a hard vacation ends.
avoid_call_before_vacation: no call on the day before a hard vacation starts.

Each case is a small roster: Ann wants both calls (target 2), Ben wants none
(target 0). With no rule, Ann takes both. A protected day moves to Ben, as
the rule outweighs the target terms. When only Ann can work, she works the
day, and the solver and scripts/check_schedule.py report the break.

    python3 scripts/py --dev -m pytest tests/test_vacation_rules.py
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HEAD = "clinician_id,name,active,can_or,target_shifts,max_shifts,target_weekend_shifts,max_weekend_shifts"
REQ_HEAD = "request_id,clinician_id,start_date,end_date,request_type,hard,shift_type,note"


def run(script: Path, config: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--config", str(config)], cwd=REPO, capture_output=True, text=True
    )


def solve(days: list[str], requests: list[str], ben_eligible: bool = True,
          rules: dict | None = None, shift_types: str | None = None):
    """Solve one OR call on each day. Returns (holder of each day, solver
    output, schedule check output, data check output)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data"
        data.mkdir()
        (data / "clinicians.csv").write_text(
            f"{HEAD}\nann,Ann,1,1,2,2,2,2\nben,Ben,1,{1 if ben_eligible else 0},0,2,0,2\n"
        )
        (data / "coverage.csv").write_text(
            "date,shift_type,required_count\n" + "".join(f"{d},OR,1\n" for d in days)
        )
        (data / "requests.csv").write_text(REQ_HEAD + "\n" + "".join(r + "\n" for r in requests))
        (data / "history.csv").write_text("date,clinician_id,shift_type,status\n")
        if shift_types is not None:
            (data / "shift_types.csv").write_text(shift_types)
        config = root / "config" / "rules.json"
        config.parent.mkdir()
        config.write_text(json.dumps({
            "input_dir": "data",
            "output_csv": "output/schedule.csv",
            "rules": {
                "min_days_between_assignments_default": 0,
                "preferred_days_between_assignments": 0,
                **(rules or {}),
            },
        }))
        checked_data = run(REPO / "scripts" / "check_my_data.py", config)
        solved = run(REPO / "solver.py", config)
        assert solved.returncode == 0, solved.stdout + solved.stderr
        with (root / "output" / "schedule.csv").open(newline="") as f:
            holders = {r["date"]: r["clinician_id"] for r in csv.DictReader(f)}
        checked = run(REPO / "scripts" / "check_schedule.py", config)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        return holders, solved.stdout, checked.stdout, checked_data


VAC_JUN_1_2 = "v1,ann,2026-06-01,2026-06-02,vacation,1,,Trip"
VAC_JUN_11_12 = "v1,ann,2026-06-11,2026-06-12,vacation,1,,Trip"


def test_no_call_on_the_day_after_a_vacation():
    holders, out, checked, _ = solve(["2026-06-03", "2026-06-17"], [VAC_JUN_1_2])
    assert holders == {"2026-06-03": "ben", "2026-06-17": "ann"}
    assert "Vacation rules broken" not in out
    assert "WARNING" not in checked


def test_no_call_on_the_day_before_a_vacation():
    holders, out, _, _ = solve(["2026-06-03", "2026-06-10"], [VAC_JUN_11_12])
    assert holders == {"2026-06-03": "ann", "2026-06-10": "ben"}
    assert "Vacation rules broken" not in out


def test_each_rule_turns_off_by_its_switch():
    holders, _, _, _ = solve(
        ["2026-06-03", "2026-06-17"], [VAC_JUN_1_2], rules={"avoid_call_after_vacation": False}
    )
    assert holders == {"2026-06-03": "ann", "2026-06-17": "ann"}
    holders, _, _, _ = solve(
        ["2026-06-03", "2026-06-10"], [VAC_JUN_11_12], rules={"avoid_call_before_vacation": False}
    )
    assert holders == {"2026-06-03": "ann", "2026-06-10": "ann"}


def test_the_rule_yields_to_coverage_and_the_break_is_reported():
    holders, out, checked, _ = solve(["2026-06-03", "2026-06-10"], [VAC_JUN_1_2, "v2,ann,2026-06-11,2026-06-11,vacation,1,,Trip"],
                                     ben_eligible=False)
    assert holders == {"2026-06-03": "ann", "2026-06-10": "ann"}
    after = "avoid_call_after_vacation: Ann works OR on 2026-06-03, the day after a vacation."
    before = "avoid_call_before_vacation: Ann works OR on 2026-06-10, the day before a vacation."
    assert "Vacation rules broken (2)" in out
    assert f"  {after}" in out and f"  {before}" in out
    # A warning, not an error: the check passes.
    assert f"  - WARNING {after}" in checked and f"  - WARNING {before}" in checked
    assert "Schedule check passed" in checked


def test_a_lock_on_a_protected_day_holds_and_is_reported_as_locked():
    holders, out, checked, _ = solve(
        ["2026-06-03", "2026-06-17"], [VAC_JUN_1_2, "l1,ann,2026-06-03,2026-06-03,lock,1,OR,Locked"]
    )
    assert holders["2026-06-03"] == "ann"
    line = "avoid_call_after_vacation: Ann works OR on 2026-06-03, the day after a vacation. The day is locked."
    assert line in out and line in checked


def test_only_hard_whole_day_vacation_counts():
    for request in (
        "v1,ann,2026-06-01,2026-06-02,vacation,0,,Soft",
        "v1,ann,2026-06-01,2026-06-02,no_call,1,,No call",
        "v1,ann,2026-06-01,2026-06-02,prefer_off,0,,Prefer",
    ):
        holders, out, _, _ = solve(["2026-06-03", "2026-06-17"], [request])
        assert holders == {"2026-06-03": "ann", "2026-06-17": "ann"}, request
        assert "Vacation rules broken" not in out


def test_a_daily_shift_type_is_not_call():
    holders, _, _, _ = solve(
        ["2026-06-03", "2026-06-17"], [VAC_JUN_1_2],
        shift_types="code,name,category,post_call_ok,next_day_off,weekend_block,color\nOR,OR,daily,1,0,0,\n",
    )
    assert holders == {"2026-06-03": "ann", "2026-06-17": "ann"}


def test_vacation_outside_the_month_protects_the_first_and_last_day():
    # The PostCallMD export carries the day before and the day after the month.
    holders, out, _, data = solve(
        ["2026-06-01", "2026-06-15", "2026-06-30"],
        ["v1,ann,2026-05-31,2026-05-31,vacation,1,,Before the month",
         "v2,ann,2026-07-01,2026-07-01,vacation,1,,After the month"],
    )
    assert data.returncode == 0, data.stdout + data.stderr
    assert holders["2026-06-01"] == "ben" and holders["2026-06-30"] == "ben"
    assert "Vacation rules broken" not in out


def test_vacation_outside_the_month_reports_a_forced_break():
    _, out, checked, _ = solve(
        ["2026-06-01", "2026-06-30"],
        ["v1,ann,2026-05-31,2026-05-31,vacation,1,,Before the month",
         "v2,ann,2026-07-01,2026-07-01,vacation,1,,After the month"],
        ben_eligible=False,
    )
    assert "avoid_call_after_vacation: Ann works OR on 2026-06-01, the day after a vacation." in out
    assert "avoid_call_before_vacation: Ann works OR on 2026-06-30, the day before a vacation." in checked


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")

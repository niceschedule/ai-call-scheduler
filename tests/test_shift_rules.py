"""Tests for the three shift-type rules that solver.py reads from
shift_types.csv (weekend_block, next_day_off, post_call_ok), and for
scripts/check_schedule.py, which checks every hard rule on the output.

Two groups of tests:

* The sample (data/sample) with a shift_types.csv beside it. Locks in
  requests.csv force the exact case a rule must allow or forbid.
* tests/fixtures/onec_twoc_export.json, a normal 1C/2C group in the shape
  of a PostCallMD export (tests/make_1c2c_fixture.py wrote it). It goes
  through the app route with no code change: import, data check, solve,
  schedule check, draft.

Every solve here runs the stock solver.py. No test patches it.

    python3 scripts/py --dev -m pytest tests/test_shift_rules.py
    python3 scripts/py tests/test_shift_rules.py   (stdlib only)
"""

from __future__ import annotations

import atexit
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "data" / "sample"
CHECK = REPO / "scripts" / "check_schedule.py"
EXPORT_1C2C = REPO / "tests" / "fixtures" / "onec_twoc_export.json"
OVERVIEW_1C2C = REPO / "tests" / "fixtures" / "onec_twoc_overview.json"

HEADER = "code,name,category,post_call_ok,next_day_off,weekend_block,color\n"
# The PostCallMD fixture's flags, on the sample's codes.
ALL_FLAGS = HEADER + "OR,OR,call,0,1,0,#1d4ed8\nOB,OB,call,0,1,1,#be185d\n"


def run(*args: str | Path, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def solve(flags: str | None, rest_gap: int = 1, locks: list[tuple[str, str, str]] = (),
          drop: list[tuple[str, str]] = (), history: list[tuple[str, str, str]] = ()
          ) -> tuple[subprocess.CompletedProcess, list[dict[str, str]], subprocess.CompletedProcess | None]:
    """Solve the sample with the stock solver.py. flags is the text of
    shift_types.csv, or None for no file. locks are (clinician, date,
    shift); drop removes (date, shift) coverage rows; history replaces
    history.csv with (clinician, date, shift) rows. Returns the solve, the
    schedule rows and the schedule check (None when no schedule)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data"
        shutil.copytree(SAMPLE, data)
        if flags is not None:
            (data / "shift_types.csv").write_text(flags)

        people = read_rows(data / "clinicians.csv")
        for row in people:
            row["min_days_between_assignments"] = str(rest_gap)
        write_rows(data / "clinicians.csv", people)

        coverage = (data / "coverage.csv").read_text().splitlines(keepends=True)
        (data / "coverage.csv").write_text(
            "".join(line for line in coverage if tuple(line.split(",")[:2]) not in set(drop))
        )
        with (data / "requests.csv").open("a", newline="") as f:
            for n, (who, day, shift) in enumerate(locks):
                f.write(f"lock{n},{who},{day},{day},lock,1,{shift},test lock\n")
        if history:
            (data / "history.csv").write_text(
                "date,clinician_id,shift_type,status\n"
                + "".join(f"{day},{who},{shift},final\n" for who, day, shift in history)
            )

        config = root / "config" / "rules.json"
        config.parent.mkdir()
        rules = json.loads((REPO / "config" / "sample_rules.json").read_text())
        rules.update(input_dir="data", output_csv="output/schedule.csv")
        rules["rules"]["min_days_between_assignments_default"] = rest_gap
        if rest_gap == 0:
            # No soft spacing either, so the rules under test decide who may
            # work back to back, and the solve stays fast.
            rules["rules"]["preferred_days_between_assignments"] = 0
        config.write_text(json.dumps(rules))

        result = run(REPO / "solver.py", "--config", config)
        out = root / "output" / "schedule.csv"
        rows: list[dict[str, str]] = []
        checked = None
        if result.returncode == 0 and out.exists():
            rows = read_rows(out)
            checked = run(CHECK, "--config", config)
        return result, rows, checked


def optimal(result: subprocess.CompletedProcess, checked: subprocess.CompletedProcess | None = None) -> None:
    """A schedule was found, and the schedule check passed. FEASIBLE counts
    too: a slow CI machine can hit the time limit before it proves the
    schedule is the best one."""
    solved = "Status: OPTIMAL" in result.stdout or "Status: FEASIBLE" in result.stdout
    assert result.returncode == 0 and solved, (
        f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    if checked is not None:
        assert checked.returncode == 0, f"schedule check failed:\n{checked.stdout}{checked.stderr}"


def no_schedule(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 2 and "No feasible schedule" in result.stdout, (
        f"expected no schedule, got exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    )


def by_person_day(rows: list[dict[str, str]]) -> dict[tuple[str, date], str]:
    return {(r["clinician_id"], date.fromisoformat(r["date"])): r["shift_type"] for r in rows}


def holder(rows: list[dict[str, str]], day: str, shift: str) -> str:
    return next(r["clinician_id"] for r in rows if r["date"] == day and r["shift_type"] == shift)


# The sample without shift_types.csv solves as before.

def test_no_shift_types_file_changes_nothing():
    result, rows, checked = solve(None)
    optimal(result, checked)
    # The sample has more than one optimal schedule, and the parallel search
    # may pick another one on another platform. Compare what the objective
    # rewards: each clinician's calls and weekend calls.
    def load(rs: list[dict[str, str]]) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for r in rs:
            n = out.setdefault(r["clinician_id"], [0, 0])
            n[0] += 1
            n[1] += date.fromisoformat(r["date"]).weekday() >= 5
        return {k: (a, b) for k, (a, b) in out.items()}

    assert load(rows) == load(read_rows(REPO / "output" / "sample_schedule.csv"))


def test_a_coverage_shift_type_missing_from_shift_types_stops_the_solve():
    result, _, _ = solve(HEADER + "OR,OR,call,1,0,0,\n")
    assert result.returncode != 0 and "shift_types.csv has no row for OB" in result.stderr


# weekend_block. The sample keeps its rest gap of 1, so a block needs the rest exception.

def test_weekend_block_pairs_saturday_and_sunday_on_the_sample():
    flags = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,1,0,1,\n"
    result, rows, checked = solve(flags, locks=[("fox", "2026-06-06", "OB")])
    optimal(result, checked)
    assert holder(rows, "2026-06-07", "OB") == "fox"
    assert holder(rows, "2026-06-13", "OB") == holder(rows, "2026-06-14", "OB")
    # Nobody else works two days in a row.
    worked = by_person_day(rows)
    for (who, day), shift in worked.items():
        if (who, day + timedelta(days=1)) in worked:
            assert day.weekday() == 5 and shift == "OB", f"{who} works {day} and the day after"


def test_weekend_block_notes_a_saturday_without_its_sunday():
    flags = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,1,0,1,\n"
    result, _, checked = solve(flags, drop=[("2026-06-07", "OB")])
    optimal(result, checked)
    assert "OB on 2026-06-06 is a weekend block, but 2026-06-07 has no OB row" in result.stdout


# next_day_off. Rest gap 0, so only the flag keeps the next day off.

def test_next_day_off_on_the_sample():
    flags = HEADER + "OR,OR,call,1,1,0,\nOB,OB,call,1,0,0,\n"
    locks = [("fox", "2026-06-02", "OR"), ("fox", "2026-06-03", "OB")]
    no_flag = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,1,0,0,\n"
    optimal(solve(no_flag, rest_gap=0, locks=locks)[0])  # without the flag, back to back is allowed
    no_schedule(solve(flags, rest_gap=0, locks=locks)[0])

    result, rows, checked = solve(flags, rest_gap=0)
    optimal(result, checked)
    worked = by_person_day(rows)
    for (who, day), shift in worked.items():
        if shift == "OR":
            assert (who, day + timedelta(days=1)) not in worked, f"{who} works the day after OR on {day}"


# post_call_ok. OB may not follow call. OR may.

def test_post_call_ok_on_the_sample():
    flags = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,0,0,0,\n"
    no_schedule(solve(flags, rest_gap=0, locks=[("fox", "2026-06-02", "OR"), ("fox", "2026-06-03", "OB")])[0])
    optimal(solve(flags, rest_gap=0, locks=[("fox", "2026-06-02", "OB"), ("fox", "2026-06-03", "OR")])[0])

    result, rows, checked = solve(flags, rest_gap=0)
    optimal(result, checked)
    worked = by_person_day(rows)
    for (who, day), shift in worked.items():
        if shift == "OB":
            assert (who, day - timedelta(days=1)) not in worked, f"{who} works OB on {day} after call"


# All three, with the PostCallMD fixture's flags.

def test_all_three_rules_together_on_the_sample():
    result, rows, checked = solve(ALL_FLAGS, rest_gap=0)
    optimal(result, checked)
    worked = by_person_day(rows)
    blocks_held = {(holder(rows, s, "OB"), date.fromisoformat(s)) for s in ("2026-06-06", "2026-06-13")}
    for sat in ("2026-06-06", "2026-06-13"):
        sun = (date.fromisoformat(sat) + timedelta(days=1)).isoformat()
        assert holder(rows, sat, "OB") == holder(rows, sun, "OB"), f"the {sat} block is split"
    for (who, day), shift in worked.items():
        nxt = (who, day + timedelta(days=1))
        if (who, day) in blocks_held:
            assert worked.get(nxt) == "OB"
        else:
            # next_day_off on both shifts, and post_call_ok = 0 on both.
            assert nxt not in worked, f"{who} works {day} and the day after"

    # The weekend block holds with the other two rules in place.
    optimal(solve(ALL_FLAGS, rest_gap=0, locks=[("fox", "2026-06-13", "OB")])[0])


def test_a_saturday_without_its_sunday_keeps_its_day_off():
    """next_day_off and post_call_ok exempt only a confirmed block, never a
    weekend_block Saturday from the flag alone. With no Sunday OB row there
    is no block, so the Saturday OB holder must get Sunday off."""
    # OR may follow call, so only OB's next_day_off can block fox on Sunday.
    flags = HEADER + "OR,OR,call,1,1,0,\nOB,OB,call,0,1,1,\n"
    locks = [("fox", "2026-06-06", "OB"), ("fox", "2026-06-07", "OR")]
    result, _, _ = solve(flags, rest_gap=0, locks=locks, drop=[("2026-06-07", "OB")])
    no_schedule(result)
    assert "2026-06-07 has no OB row" in result.stdout
    # With the Sunday OB row back, the Saturday is a block, and fox holds both days.
    result, rows, checked = solve(flags, rest_gap=0, locks=[("fox", "2026-06-06", "OB")])
    optimal(result, checked)
    assert holder(rows, "2026-06-07", "OB") == "fox"


# The day before the month, from history.csv. The sample starts Monday 2026-06-01.

def test_history_on_the_day_before_the_month_counts():
    flags = HEADER + "OR,OR,call,1,1,0,\nOB,OB,call,0,0,0,\n"
    # next_day_off: OR on May 31 gives fox June 1 off.
    no_schedule(solve(flags, rest_gap=0, history=[("fox", "2026-05-31", "OR")],
                      locks=[("fox", "2026-06-01", "OR")])[0])
    # post_call_ok: OB on June 1 may not follow call (OB) on May 31.
    no_schedule(solve(flags, rest_gap=0, history=[("fox", "2026-05-31", "OB")],
                      locks=[("fox", "2026-06-01", "OB")])[0])
    # OR may follow call, and OB has no next_day_off.
    result, _, checked = solve(flags, rest_gap=0, history=[("fox", "2026-05-31", "OB")],
                               locks=[("fox", "2026-06-01", "OR")])
    optimal(result, checked)
    # The minimum rest of 1 day holds across the month line too.
    no_schedule(solve(HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,1,0,0,\n", rest_gap=1,
                      history=[("fox", "2026-05-31", "OR")], locks=[("fox", "2026-06-01", "OR")])[0])


# The 1C/2C group, through the app route, with no code change.

@lru_cache(maxsize=None)
def solved_1c2c() -> tuple[Path, Path, subprocess.CompletedProcess, subprocess.CompletedProcess]:
    """Import, data check, solve and schedule check, once. The temp folder
    stays for the run of the tests."""
    root = Path(tempfile.mkdtemp(prefix="onec-twoc-"))
    atexit.register(shutil.rmtree, root, True)
    config = root / "config" / "my_rules.json"
    imported = run(REPO / "scripts" / "from_postcallmd.py", EXPORT_1C2C,
                   "--data-dir", root / "data" / "my_data", "--config", config)
    assert imported.returncode == 0, imported.stdout + imported.stderr
    data_check = run(REPO / "scripts" / "check_my_data.py", "--config", config)
    assert data_check.returncode == 0, data_check.stdout + data_check.stderr
    result = run(REPO / "solver.py", "--config", config)
    optimal(result)
    checked = run(CHECK, "--config", config)
    return root, config, imported, checked


def schedule_1c2c() -> list[dict[str, str]]:
    root, _, _, _ = solved_1c2c()
    return read_rows(root / "output" / "schedule_2026-11.csv")


def names_1c2c() -> dict[str, str]:
    root, _, _, _ = solved_1c2c()
    return {r["name"]: r["clinician_id"] for r in read_rows(root / "data" / "my_data" / "clinicians.csv")}


def test_1c2c_import_clears_the_four_blocking_lines_with_the_template():
    _, config, imported, _ = solved_1c2c()
    assert "STOP" not in imported.stdout
    assert imported.stdout.count("  BLOCKING: ") == 4
    assert json.loads(config.read_text())["enforces"] == ["weekend_block", "next_day_off", "post_call_ok"]


def test_1c2c_schedule_passes_the_schedule_check():
    _, _, _, checked = solved_1c2c()
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "Schedule check passed" in checked.stdout


def test_1c2c_schedule_keeps_the_rules():
    rows = schedule_1c2c()
    ids = names_1c2c()
    worked = by_person_day(rows)
    # Every Saturday 1C block is one person, and the day after the block is off.
    for day in range(7, 29, 7):
        sat, sun = date(2026, 11, day), date(2026, 11, day + 1)
        who = holder(rows, sat.isoformat(), "1C")
        assert holder(rows, sun.isoformat(), "1C") == who
        assert (who, sun + timedelta(days=1)) not in worked
    # Sunday the 1st belongs to the October 31 block (G. Gupta in history).
    assert holder(rows, "2026-11-01", "1C") == ids["G. Gupta"]
    # The lock on Saturday the 14th pins the whole block.
    assert holder(rows, "2026-11-15", "1C") == ids["D. Dorsey"]
    assert holder(rows, "2026-11-19", "2C") == ids["F. Flores"]
    # Vacation and must_off days stay empty.
    for d in range(9, 16):
        assert (ids["A. Adams"], date(2026, 11, d)) not in worked
    assert (ids["B. Brooks"], date(2026, 11, 21)) not in worked
    # No second call for E. Ellis.
    assert not any(r["clinician_id"] == ids["E. Ellis"] and r["shift_type"] == "2C" for r in rows)
    # 2C only on weekdays, and never the day after call.
    for (who, day), shift in worked.items():
        if shift == "2C":
            assert day.weekday() < 5
            assert (who, day - timedelta(days=1)) not in worked


def test_1c2c_half_time_doctor_stays_near_target():
    rows = schedule_1c2c()
    ids = names_1c2c()
    count = sum(1 for r in rows if r["clinician_id"] == ids["H. Hale"])
    assert count <= 4, f"H. Hale (target 3) has {count} shifts"


def test_1c2c_draft_is_written_with_no_code_change():
    root, config, _, _ = solved_1c2c()
    out = root / "output" / "draft.json"
    draft = run(REPO / "scripts" / "to_postcallmd_draft.py", "--overview", OVERVIEW_1C2C,
                "--config", config, "--out", out)
    assert draft.returncode == 0, draft.stdout + draft.stderr
    assert "BLOCKING lines cleared" in draft.stdout
    assert len(json.loads(out.read_text())["assignments"]) == len(schedule_1c2c())


# The schedule check catches each broken rule.

def broken(edit) -> str:
    """Copy the 1C/2C schedule, apply edit(rows, ids), and return the
    check's output. The check must fail."""
    root, config, _, _ = solved_1c2c()
    rows = [dict(r) for r in schedule_1c2c()]
    edit(rows, names_1c2c())
    bad = root / "output" / "broken.csv"
    write_rows(bad, rows)
    result = run(CHECK, "--config", config, "--schedule", bad)
    assert result.returncode == 1, result.stdout + result.stderr
    return result.stdout


def set_holder(rows, day, shift, who):
    for r in rows:
        if r["date"] == day and r["shift_type"] == shift:
            r["clinician_id"] = who


def test_check_catches_a_split_weekend_block():
    def edit(rows, ids):
        sat = holder(rows, "2026-11-21", "1C")
        set_holder(rows, "2026-11-22", "1C", next(i for i in ids.values() if i != sat))
    assert "weekend_block: 1C 2026-11-21" in broken(edit)


def test_check_catches_work_the_day_after_1c():
    def edit(rows, ids):
        set_holder(rows, "2026-11-04", "2C", holder(rows, "2026-11-03", "1C"))
    out = broken(edit)
    assert "next_day_off:" in out and "post_call_ok:" in out


def test_check_catches_a_missing_row_and_a_vacation_day():
    def edit(rows, ids):
        rows[:] = [r for r in rows if not (r["date"] == "2026-11-05" and r["shift_type"] == "2C")]
        set_holder(rows, "2026-11-10", "2C", ids["A. Adams"])
    out = broken(edit)
    assert "coverage: 2026-11-05 2C needs 1, has 0" in out
    assert "requests:" in out and "vacation" in out


def test_check_catches_eligibility_a_moved_lock_and_the_history_block():
    def edit(rows, ids):
        set_holder(rows, "2026-11-02", "2C", ids["E. Ellis"])
        set_holder(rows, "2026-11-19", "2C", ids["J. Ives"])
        set_holder(rows, "2026-11-01", "1C", ids["K. Kerr"])
    out = broken(edit)
    assert "eligibility:" in out
    assert "requests: lock lock-1c2c0002" in out
    assert "worked 1C on 2026-10-31 (history) but not on 2026-11-01" in out


def test_check_catches_a_cap():
    def edit(rows, ids):
        for r in rows[:12]:
            r["clinician_id"] = ids["H. Hale"]
    assert "caps: " in broken(edit)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok - {name}")

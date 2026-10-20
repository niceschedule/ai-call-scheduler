"""Tests for the three app rules in docs/adaptation-cookbook.md, section
"App rules from shift_types.csv".

Each test reads the marked code blocks from the cookbook itself, applies
them to a copy of solver.py in the install order, and solves the sample
(data/sample) with a shift_types.csv beside it. So a change to the cookbook
text is what the tests check. Locks in requests.csv force the exact case a
rule must allow or forbid.

    python3 scripts/py --dev -m pytest tests/test_cookbook.py
    python3 scripts/py tests/test_cookbook.py   (stdlib only)
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COOKBOOK = REPO / "docs" / "adaptation-cookbook.md"
SAMPLE = REPO / "data" / "sample"

HEADER = "code,name,category,post_call_ok,next_day_off,weekend_block,color\n"
# The PostCallMD fixture's flags, on the sample's codes.
ALL_FLAGS = HEADER + "OR,OR,call,0,1,0,#1d4ed8\nOB,OB,call,0,1,1,#be185d\n"


def blocks() -> dict[str, str]:
    """name -> code of every block marked <!-- cookbook:name --> in the cookbook."""
    text = COOKBOOK.read_text(encoding="utf-8")
    found = dict(re.findall(r"<!-- cookbook:([\w-]+) -->\n```python\n(.*?)```", text, re.S))
    for name in ("helper", "helper-call", "weekend-block", "weekend-block-rest-old",
                 "weekend-block-rest", "next-day-off", "post-call-ok"):
        assert name in found, f"the cookbook has no block marked cookbook:{name}"
    return found


def insert_after(source: str, anchor: str, code: str) -> str:
    """Insert code after the one line that holds anchor."""
    assert source.count(anchor) == 1, f"solver.py should hold {anchor!r} once"
    index = source.index(anchor)
    line_end = source.index("\n", index) + 1
    return source[:line_end] + code + source[line_end:]


def patched_solver(entries: list[str]) -> str:
    """solver.py with the helper and the named entries, in that order."""
    b = blocks()
    source = (REPO / "solver.py").read_text(encoding="utf-8")
    source = source.replace("\ndef request_matches(", "\n" + b["helper"] + "\n\ndef request_matches(", 1)
    source = insert_after(source, "= load_history(input_dir / \"history.csv\", clinicians)", b["helper-call"])
    code = "".join("\n" + b[name] for name in entries)
    marker = "    # HARD RULE 3 and SOFT PREFERENCE 1:"
    assert source.count(marker) == 1
    source = source.replace(marker, code.lstrip("\n") + "\n" + marker, 1)
    if "weekend-block" in entries:
        old, new = b["weekend-block-rest-old"], b["weekend-block-rest"]
        assert source.count(old) == 1, "the rest rule in solver.py no longer matches the cookbook"
        source = source.replace(old, new, 1)
    return source


def solve(entries: list[str], flags: str, rest_gap: int = 1, locks: list[tuple[str, str, str]] = (),
          drop: list[tuple[str, str]] = ()) -> tuple[subprocess.CompletedProcess, list[dict[str, str]]]:
    """Solve the sample with a patched solver.py. locks are (clinician, date,
    shift); drop removes (date, shift) coverage rows."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "solver.py").write_text(patched_solver(entries), encoding="utf-8")
        shutil.copy(REPO / "schedule_html.py", root / "schedule_html.py")
        data = root / "data"
        shutil.copytree(SAMPLE, data)
        (data / "shift_types.csv").write_text(flags)

        with (data / "clinicians.csv").open(newline="") as f:
            reader = csv.DictReader(f)
            fields, people = reader.fieldnames, list(reader)
        for row in people:
            row["min_days_between_assignments"] = str(rest_gap)
        with (data / "clinicians.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(people)

        coverage = (data / "coverage.csv").read_text().splitlines(keepends=True)
        (data / "coverage.csv").write_text(
            "".join(line for line in coverage if tuple(line.split(",")[:2]) not in set(drop))
        )
        with (data / "requests.csv").open("a", newline="") as f:
            for n, (who, day, shift) in enumerate(locks):
                f.write(f"lock{n},{who},{day},{day},lock,1,{shift},test lock\n")

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

        result = subprocess.run(
            [sys.executable, str(root / "solver.py"), "--config", str(config)],
            cwd=root, capture_output=True, text=True,
        )
        out = root / "output" / "schedule.csv"
        rows = []
        if result.returncode == 0 and out.exists():
            with out.open(newline="") as f:
                rows = list(csv.DictReader(f))
        return result, rows


def optimal(result: subprocess.CompletedProcess) -> None:
    """A schedule was found. FEASIBLE counts too: these tests check the
    rules in the schedule, and a slow CI machine can hit the time limit
    before it proves the schedule is the best one."""
    solved = "Status: OPTIMAL" in result.stdout or "Status: FEASIBLE" in result.stdout
    assert result.returncode == 0 and solved, (
        f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def no_schedule(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 2 and "No feasible schedule" in result.stdout, (
        f"expected no schedule, got exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    )


def by_person_day(rows: list[dict[str, str]]) -> dict[tuple[str, date], str]:
    return {(r["clinician_id"], date.fromisoformat(r["date"])): r["shift_type"] for r in rows}


def holder(rows: list[dict[str, str]], day: str, shift: str) -> str:
    return next(r["clinician_id"] for r in rows if r["date"] == day and r["shift_type"] == shift)


# Entry 5. The sample keeps its rest gap of 1, so a block needs the rest exception.

def test_weekend_block_pairs_saturday_and_sunday_on_the_sample():
    flags = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,1,0,1,\n"
    result, rows = solve(["weekend-block"], flags, locks=[("fox", "2026-06-06", "OB")])
    optimal(result)
    assert holder(rows, "2026-06-07", "OB") == "fox"
    assert holder(rows, "2026-06-13", "OB") == holder(rows, "2026-06-14", "OB")
    # Nobody else works two days in a row.
    worked = by_person_day(rows)
    for (who, day), shift in worked.items():
        if (who, day + timedelta(days=1)) in worked:
            assert day.weekday() == 5 and shift == "OB", f"{who} works {day} and the day after"


def test_weekend_block_notes_a_saturday_without_its_sunday():
    flags = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,1,0,1,\n"
    result, _ = solve(["weekend-block"], flags, drop=[("2026-06-07", "OB")])
    optimal(result)
    assert "OB on 2026-06-06 is a weekend block, but 2026-06-07 has no OB row" in result.stdout


# Entry 6, after entry 5. Rest gap 0, so only the flag keeps the next day off.

def test_next_day_off_on_the_sample():
    flags = HEADER + "OR,OR,call,1,1,0,\nOB,OB,call,1,0,0,\n"
    locks = [("fox", "2026-06-02", "OR"), ("fox", "2026-06-03", "OB")]
    baseline, _ = solve(["weekend-block"], flags, rest_gap=0, locks=locks)
    optimal(baseline)  # without the rule, back to back is allowed
    no_schedule(solve(["weekend-block", "next-day-off"], flags, rest_gap=0, locks=locks)[0])

    result, rows = solve(["weekend-block", "next-day-off"], flags, rest_gap=0)
    optimal(result)
    worked = by_person_day(rows)
    for (who, day), shift in worked.items():
        if shift == "OR":
            assert (who, day + timedelta(days=1)) not in worked, f"{who} works the day after OR on {day}"


# Entry 7, after entry 5. OB may not follow call. OR may.

def test_post_call_ok_on_the_sample():
    flags = HEADER + "OR,OR,call,1,0,0,\nOB,OB,call,0,0,0,\n"
    entries = ["weekend-block", "post-call-ok"]
    no_schedule(solve(entries, flags, rest_gap=0, locks=[("fox", "2026-06-02", "OR"), ("fox", "2026-06-03", "OB")])[0])
    optimal(solve(entries, flags, rest_gap=0, locks=[("fox", "2026-06-02", "OB"), ("fox", "2026-06-03", "OR")])[0])

    result, rows = solve(entries, flags, rest_gap=0)
    optimal(result)
    worked = by_person_day(rows)
    for (who, day), shift in worked.items():
        if shift == "OB":
            assert (who, day - timedelta(days=1)) not in worked, f"{who} works OB on {day} after call"


# All three, in the install order, with the PostCallMD fixture's flags.

def test_all_three_rules_together_on_the_sample():
    entries = ["weekend-block", "next-day-off", "post-call-ok"]
    result, rows = solve(entries, ALL_FLAGS, rest_gap=0)
    optimal(result)
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
    optimal(solve(entries, ALL_FLAGS, rest_gap=0, locks=[("fox", "2026-06-13", "OB")])[0])


def test_a_saturday_without_its_sunday_keeps_its_day_off():
    """The composition bug: next_day_off and post_call_ok used to exempt any
    weekend_block Saturday from the flag alone. With no Sunday OB row there
    is no block, so the Saturday OB holder must get Sunday off."""
    entries = ["weekend-block", "next-day-off", "post-call-ok"]
    # OR may follow call, so only OB's next_day_off can block fox on Sunday.
    flags = HEADER + "OR,OR,call,1,1,0,\nOB,OB,call,0,1,1,\n"
    locks = [("fox", "2026-06-06", "OB"), ("fox", "2026-06-07", "OR")]
    result, _ = solve(entries, flags, rest_gap=0, locks=locks, drop=[("2026-06-07", "OB")])
    no_schedule(result)
    assert "2026-06-07 has no OB row" in result.stdout
    # With the Sunday OB row back, the Saturday is a block, and fox holds both days.
    result, rows = solve(entries, flags, rest_gap=0, locks=[("fox", "2026-06-06", "OB")])
    optimal(result)
    assert holder(rows, "2026-06-07", "OB") == "fox"


def test_entries_6_and_7_need_entry_5():
    result, _ = solve(["next-day-off"], ALL_FLAGS, rest_gap=0)
    assert result.returncode != 0 and "NameError" in result.stderr and "block_saturdays" in result.stderr


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok - {name}")

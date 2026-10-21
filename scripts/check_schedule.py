#!/usr/bin/env python3
"""Check a solved schedule against every hard rule.

It reads the same inputs as solver.py (the input_dir of the config) and the
schedule the solver wrote (output_csv of the config, or --schedule). It does
not trust the solver: each rule is checked again on the output rows.

The hard rules:

1. coverage: each coverage row is filled exactly (date, shift_type,
   required_count), and no assignment is outside coverage.csv.
2. eligibility: a clinician works only the shift types with can_<code> = 1.
3. one_per_day: a clinician works at most one assignment per day.
4. requests: a hard request (vacation, no_call, or hard=1) keeps its days
   empty. A lock row (locked and imported entries) is in the schedule.
5. caps: max_shifts and max_weekend_shifts per clinician.
6. min_rest: min_days_between_assignments (the column, else the config
   default), counted against history too.
7. weekend_block: for a shift type with weekend_block = 1 in
   shift_types.csv, Saturday and the Sunday after it have the same people.
8. next_day_off: for a shift type with next_day_off = 1, the clinician has
   no assignment on the next day.
9. post_call_ok: for a shift type with post_call_ok = 0, the clinician
   worked no call shift (category call) on the day before.

The Saturday and Sunday of a weekend block are one assignment, so rules 6,
8 and 9 allow that one pair. Rules 6, 8 and 9 also read history.csv for the
day before the month, and rule 7 for a Sunday on the first day.

It also lists the breaks of the two strong soft rules, as warnings:
avoid_call_after_vacation (call on the day after a vacation) and
avoid_call_before_vacation (call on the day before a vacation). A warning
does not fail the check. The admin should see each one.

Exit 0 when every rule holds, 1 when one or more fail, 2 for a bad input.
Warnings do not change the exit code.

    python3 scripts/py scripts/check_schedule.py --config config/my_rules.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver import (  # noqa: E402  (the solver's own readers, so both read the files alike)
    eligibility_column,
    is_weekend,
    load_clinicians,
    load_coverage,
    load_history_rows,
    load_requests,
    load_shift_flags,
    parse_bool,
    parse_date,
    parse_int,
    request_matches,
    vacation_break_line,
    vacation_breaks,
)


def resolve_path(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def check(config_path: Path, schedule_path: Path | None = None) -> list[str]:
    """Every broken rule, one line each. Empty when the schedule is valid."""
    return check_with_warnings(config_path, schedule_path)[0]


def check_with_warnings(
    config_path: Path, schedule_path: Path | None = None
) -> tuple[list[str], list[str]]:
    """The broken hard rules and the broken soft vacation rules, one line each."""
    config = json.loads(config_path.read_text())
    base = config_path.parent.parent
    input_dir = resolve_path(base, config["input_dir"])
    if schedule_path is None:
        schedule_path = resolve_path(base, config.get("output_csv", "output/schedule.csv"))
    rules = config.get("rules", {})
    default_gap = parse_int(str(rules.get("min_days_between_assignments_default", 1)))

    clinicians = load_clinicians(input_dir / "clinicians.csv")
    coverage = load_coverage(input_dir / "coverage.csv")
    requests = load_requests(input_dir / "requests.csv", clinicians)
    flags = load_shift_flags(input_dir / "shift_types.csv")
    history = [
        (cid, day, code)
        for cid, day, code in load_history_rows(input_dir / "history.csv", clinicians)
        if cid in clinicians
    ]

    with schedule_path.open(newline="", encoding="utf-8-sig") as f:
        rows = [
            (r["clinician_id"].strip(), parse_date(r["date"]), r["shift_type"].strip().upper())
            for r in csv.DictReader(f)
        ]

    problems: list[str] = []
    month_start = min(c.date for c in coverage)
    eve = month_start - timedelta(days=1)
    required = {(c.date, c.shift_type): c.required_count for c in coverage}

    # 1. coverage
    filled = Counter((day, code) for _, day, code in rows)
    for key, need in sorted(required.items()):
        if filled[key] != need:
            problems.append(f"coverage: {key[0]} {key[1]} needs {need}, has {filled[key]}")
    for key in sorted(set(filled) - set(required)):
        problems.append(f"coverage: {key[0]} {key[1]} is not in coverage.csv")

    # 2. eligibility
    for cid, day, code in rows:
        if cid not in clinicians:
            problems.append(f"eligibility: {cid} on {day} is not an active clinician")
        elif not parse_bool(clinicians[cid].get(eligibility_column(code), "0")):
            problems.append(f"eligibility: {cid} works {code} on {day} but is not eligible")

    # 3. one_per_day
    worked: dict[tuple[str, date], list[str]] = defaultdict(list)
    for cid, day, code in rows:
        worked[(cid, day)].append(code)
    for (cid, day), codes in sorted(worked.items()):
        if len(codes) > 1:
            problems.append(f"one_per_day: {cid} works {', '.join(codes)} on {day}")

    # 4. requests
    for r in requests:
        if r.request_type == "lock":
            days = [c for c in coverage if request_matches(r, c.date, c.shift_type)]
            for c in days:
                if c.shift_type not in worked.get((r.clinician_id, c.date), []):
                    problems.append(
                        f"requests: lock {r.request_id} puts {r.clinician_id} on {c.shift_type} "
                        f"{c.date}, but the schedule does not"
                    )
        elif r.hard:
            for (cid, day), codes in worked.items():
                if cid != r.clinician_id:
                    continue
                for code in codes:
                    if request_matches(r, day, code):
                        problems.append(
                            f"requests: {cid} works {code} on {day} against hard "
                            f"{r.request_type} {r.request_id}"
                        )

    # 5. caps
    total = Counter(cid for cid, _, _ in rows)
    weekend = Counter(cid for cid, day, _ in rows if is_weekend(day))
    for cid, row in clinicians.items():
        cap = parse_int(row.get("max_shifts", ""), 999)
        wcap = parse_int(row.get("max_weekend_shifts", ""), 999)
        if total[cid] > cap:
            problems.append(f"caps: {cid} has {total[cid]} shifts, max_shifts is {cap}")
        if weekend[cid] > wcap:
            problems.append(f"caps: {cid} has {weekend[cid]} weekend shifts, max_weekend_shifts is {wcap}")

    # 7. weekend_block, and the pairs rules 6, 8 and 9 allow.
    holders: dict[tuple[date, str], set[str]] = defaultdict(set)
    for cid, day, code in rows:
        holders[(day, code)].add(cid)
    history_holders: dict[tuple[date, str], set[str]] = defaultdict(set)
    for cid, day, code in history:
        history_holders[(day, code)].add(cid)
    block_pairs: set[tuple[str, date, str]] = set()  # (clinician, Saturday, code)
    for (day, code) in required:
        if not flags.get(code, {}).get("weekend_block") or day.weekday() != 6:
            continue
        saturday = day - timedelta(days=1)
        if (saturday, code) in required:
            sat, sun = holders[(saturday, code)], holders[(day, code)]
            if sat != sun:
                problems.append(
                    f"weekend_block: {code} {saturday} has {sorted(sat)}, "
                    f"{day} has {sorted(sun)}"
                )
            for cid in sat & sun:
                block_pairs.add((cid, saturday, code))
        elif saturday == eve:
            for cid in history_holders.get((saturday, code), set()):
                if cid in holders[(day, code)]:
                    block_pairs.add((cid, saturday, code))
                    continue
                blocked = any(
                    r.clinician_id == cid and r.hard and r.request_type != "lock"
                    and request_matches(r, day, code)
                    for r in requests
                )
                eligible = parse_bool(clinicians[cid].get(eligibility_column(code), "0"))
                if eligible and not blocked:
                    problems.append(
                        f"weekend_block: {cid} worked {code} on {saturday} (history) "
                        f"but not on {day}"
                    )

    def is_block(cid: str, day_a: date, code_a: str, day_b: date, code_b: str) -> bool:
        return code_a == code_b and day_b == day_a + timedelta(days=1) and (cid, day_a, code_a) in block_pairs

    # All assignments of a clinician, history before the month included.
    timeline: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for cid, day, code in history:
        if day < month_start:
            timeline[cid].append((day, code))
    for cid, day, code in rows:
        timeline[cid].append((day, code))

    for cid, items in timeline.items():
        if cid not in clinicians:
            continue
        items.sort()
        gap_raw = clinicians[cid].get("min_days_between_assignments", "")
        min_gap = parse_int(gap_raw, default_gap)
        for i, (day_a, code_a) in enumerate(items):
            for day_b, code_b in items[i + 1:]:
                gap = (day_b - day_a).days
                if gap > max(min_gap, 1):
                    break
                if day_b < month_start or gap == 0:
                    continue  # history against history, or rule 3
                if is_block(cid, day_a, code_a, day_b, code_b):
                    continue
                # 6. min_rest
                if gap <= min_gap:
                    problems.append(
                        f"min_rest: {cid} works {day_a} {code_a} and {day_b} {code_b}, "
                        f"minimum gap is {min_gap}"
                    )
                if gap == 1:
                    # 8. next_day_off
                    if flags.get(code_a, {}).get("next_day_off"):
                        problems.append(
                            f"next_day_off: {cid} works {code_b} on {day_b}, the day after {code_a}"
                        )
                    # 9. post_call_ok
                    if (
                        flags.get(code_a, {}).get("category") == "call"
                        and not flags.get(code_b, {}).get("post_call_ok", True)
                    ):
                        problems.append(
                            f"post_call_ok: {cid} works {code_b} on {day_b} after call ({code_a}) on {day_a}"
                        )
    warnings = [
        vacation_break_line(rule, clinicians[cid]["name"] if cid in clinicians else cid, day, code, locked)
        for rule, cid, day, code, locked in vacation_breaks(rows, requests, flags, rules)
    ]
    return problems, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/my_rules.json", help="Defaults to config/my_rules.json.")
    parser.add_argument("--schedule", help="The schedule CSV. Defaults to output_csv in the config.")
    args = parser.parse_args()
    config_path = resolve_path(ROOT, args.config)
    schedule = resolve_path(ROOT, args.schedule) if args.schedule else None
    try:
        problems, warnings = check_with_warnings(config_path, schedule)
    except (OSError, ValueError, KeyError) as exc:
        print(f"check_schedule: {exc}", file=sys.stderr)
        return 2
    if warnings:
        print(f"Warnings: {len(warnings)} soft vacation rule(s) broken. Tell the admin about each one:")
        for line in warnings:
            print(f"  - WARNING {line}")
    if problems:
        print(f"Schedule check FAILED: {len(problems)} broken rule(s):")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("Schedule check passed: coverage, eligibility, one per day, requests and locks, "
          "caps, minimum rest, weekend_block, next_day_off, post_call_ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

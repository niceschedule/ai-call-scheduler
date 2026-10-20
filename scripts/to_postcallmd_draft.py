#!/usr/bin/env python3
"""Turn the solver's output CSV into a draft for PostCallMD.

Input:

* The solver output CSV (date, shift_type, clinician_id, name). By default
  the output_csv named in config/my_rules.json.
* The shift-type list with ids: the JSON that the PostCallMD MCP tool
  get_practice_overview returns, saved to a file. Its shift_types array
  carries id and code. A bare array of shift types works too.
* The data folder that scripts/from_postcallmd.py wrote (the input_dir in
  the config, data/my_data by default): clinicians.csv, coverage.csv and
  notes.txt.

Output: a JSON file with the arguments for submit_schedule_draft:

    {
      "start_date": "2026-06-01",
      "end_date": "2026-06-30",
      "mode": "fill_empty",
      "assignments": [
        {"date": "2026-06-01", "clinician_id": "<uuid>", "shift_type_id": "<uuid>"},
        ...
      ]
    }

start_date and end_date are the first and last day of the month, not the
first and last assignment. The month is --month YYYY-MM, or the month of
coverage.csv when --month is not given.

clinician_id is already the app UUID, because the export wrote it into
clinicians.csv. shift_type_id comes from the code, matched without regard
to case or spacing (solver.py writes "OR CALL" for the app's "OR Call").

The script writes nothing and stops when:

* notes.txt has a line that starts with "BLOCKING:" that is not cleared.
  The export writes such a line for a rule the solver may not keep:
  "BLOCKING: <token> <subject>: <sentence>". The script prints each one.
* a clinician_id is not a UUID, or is not an active row in clinicians.csv.
* a shift code has no active shift type in the overview, or its id is not
  a UUID.
* an assignment date is outside the month.

A line for a shift-type rule (token next_day_off, weekend_block or
post_call_ok) is cleared when both hold:

* config/my_rules.json lists the token in "enforces", for example
  "enforces": ["weekend_block", "next_day_off"].
* solver.py has the rule from docs/adaptation-cookbook.md. The script looks
  for the names the cookbook code uses, so a config entry alone clears
  nothing.

A must_on or must_off line is never cleared. The export does not carry the
request, so no solver can keep it.

    python3 scripts/py scripts/to_postcallmd_draft.py --overview overview.json --month 2026-07
    (Windows: py -3 scripts/py ...)
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

WRAPPER_KEYS = ("structuredContent", "structured_content", "result", "data", "output")
OUTPUT_COLUMNS = ("date", "shift_type", "clinician_id")
BLOCKING_PREFIX = "BLOCKING:"

# Shift-type rules a solver can keep, and the names from the cookbook code
# that must be in solver.py before "enforces" may clear the rule. Entries 6
# and 7 depend on entry 5, so they need its names too.
SOLVER_RULES = {
    "weekend_block": ("load_shift_flags", "weekend_block_pairs", "block_saturdays"),
    "next_day_off": ("load_shift_flags", 'get("next_day_off")', "block_saturdays"),
    "post_call_ok": ("load_shift_flags", 'get("post_call_ok"', "weekend_block_pairs"),
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def fail(message: str) -> int:
    print(f"to_postcallmd_draft: {message}", file=sys.stderr)
    return 2


def normalize_code(code: str) -> str:
    """Case-insensitive key for a shift code. Spaces, hyphens and underscores
    count as one separator, so "OR Call", "OR CALL" and "or_call" match."""
    return re.sub(r"[\s_\-]+", " ", code.strip().lower())


def load_shift_types(path: Path) -> list[dict]:
    """Read the shift-type list from a get_practice_overview result, a bare
    list, or either of those inside a one-level client wrapper."""
    with path.open(encoding="utf-8-sig") as f:
        data = json.load(f)
    for _ in range(3):
        if isinstance(data, str):
            data = json.loads(data)
            continue
        if isinstance(data, dict):
            if isinstance(data.get("shift_types"), list):
                data = data["shift_types"]
                break
            inner = next(
                (data[k] for k in WRAPPER_KEYS if isinstance(data.get(k), (dict, list, str))),
                None,
            )
            if inner is not None:
                data = inner
                continue
        break
    if not isinstance(data, list):
        raise ValueError(
            "no shift_types array found. Save the result of get_practice_overview."
        )
    rows = [r for r in data if isinstance(r, dict)]
    if not rows:
        raise ValueError("the shift_types array is empty")
    return rows


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def build_code_map(shift_types: list[dict]) -> dict[str, dict]:
    """normalized code -> shift type row. Inactive rows are skipped. Two
    active rows with the same normalized code are an error, because the
    solver output cannot tell them apart. An id that is not a UUID is an
    error, because the app would reject it."""
    by_code: dict[str, dict] = {}
    for row in shift_types:
        code = row.get("code")
        st_id = row.get("id") or row.get("shift_type_id")
        if not code or not st_id:
            raise ValueError(f"a shift type row lacks code or id: {row}")
        if row.get("active") is False:
            continue
        if not is_uuid(str(st_id)):
            raise ValueError(f"the shift type {code!r} has the id {st_id!r}, which is not a UUID")
        key = normalize_code(str(code))
        if key in by_code and by_code[key]["id"] != st_id:
            raise ValueError(
                f"two active shift types share the code {code!r} "
                f"({by_code[key]['id']} and {st_id}); the solver output cannot tell them apart"
            )
        by_code[key] = {"id": str(st_id), "code": str(code), "name": row.get("name", "")}
    return by_code


def inactive_codes(shift_types: list[dict]) -> set[str]:
    return {
        normalize_code(str(row.get("code")))
        for row in shift_types
        if row.get("active") is False and row.get("code")
    }


def blocking_lines(notes_path: Path) -> list[str]:
    """Every line of notes.txt that starts with BLOCKING:. Leading spaces
    are ignored, so an indented line still counts."""
    text = notes_path.read_text(encoding="utf-8-sig")
    return [line.strip() for line in text.splitlines() if line.lstrip().startswith(BLOCKING_PREFIX)]


def blocking_token(line: str) -> str:
    """The rule token: the first word after BLOCKING:."""
    words = line.strip()[len(BLOCKING_PREFIX):].split()
    return words[0] if words else ""


def split_blocking(
    lines: list[str], enforces: list[str], solver_text: str
) -> tuple[list[str], list[str], list[str]]:
    """Split BLOCKING lines into (still blocking, cleared, reasons). A line
    is cleared only when its token is a solver rule, the config lists it in
    enforces, and solver.py has the cookbook code for it."""
    kept: list[str] = []
    cleared: list[str] = []
    reasons: list[str] = []
    for line in lines:
        token = blocking_token(line)
        if token not in enforces:
            kept.append(line)
            continue
        if token not in SOLVER_RULES:
            kept.append(line)
            reasons.append(
                f"{token} is in enforces, but a {token} line cannot be cleared: "
                "the export does not carry the request."
            )
            continue
        missing = [name for name in SOLVER_RULES[token] if name not in solver_text]
        if missing:
            kept.append(line)
            reasons.append(
                f"{token} is in enforces, but solver.py lacks {', '.join(missing)}. "
                "Add the rule from docs/adaptation-cookbook.md first."
            )
            continue
        cleared.append(line)
    return kept, cleared, sorted(set(reasons))


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def active_roster(path: Path) -> dict[str, str]:
    """clinician_id -> name for every active row of clinicians.csv."""
    _, rows = read_rows(path)
    roster: dict[str, str] = {}
    for row in rows:
        clinician_id = (row.get("clinician_id") or "").strip()
        active = (row.get("active") or "1").strip().lower()
        if clinician_id and active in {"1", "true", "yes", "y"}:
            roster[clinician_id] = (row.get("name") or clinician_id).strip()
    return roster


def parse_month(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ValueError(f"--month must be YYYY-MM, for example 2026-07, not {value!r}")
    return int(match.group(1)), int(match.group(2))


def coverage_month(path: Path) -> str:
    """The one YYYY-MM that every coverage row falls in."""
    _, rows = read_rows(path)
    months = sorted({(r.get("date") or "").strip()[:7] for r in rows if (r.get("date") or "").strip()})
    if not months:
        raise ValueError(f"{path} has no dated rows, so the month is unknown. Pass --month YYYY-MM.")
    if len(months) > 1:
        raise ValueError(
            f"{path} spans more than one month ({', '.join(months)}). Pass --month YYYY-MM."
        )
    return months[0]


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--overview",
        required=True,
        help="The saved result of get_practice_overview (or its shift_types array).",
    )
    parser.add_argument(
        "--schedule",
        default=None,
        help="Solver output CSV. Defaults to output_csv in the config file.",
    )
    parser.add_argument(
        "--config",
        default="config/my_rules.json",
        help="Config that names output_csv. Defaults to config/my_rules.json.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Folder with clinicians.csv, coverage.csv and notes.txt. "
        "Defaults to input_dir in the config, else data/my_data.",
    )
    parser.add_argument(
        "--month",
        default=None,
        help="The draft month, YYYY-MM. Defaults to the month of coverage.csv.",
    )
    parser.add_argument(
        "--solver",
        default="solver.py",
        help="The solver that made the schedule, checked for the rules in enforces. "
        "Defaults to solver.py.",
    )
    parser.add_argument(
        "--out",
        default="output/postcallmd_draft.json",
        help="Where to write the draft JSON. Defaults to output/postcallmd_draft.json.",
    )
    args = parser.parse_args()

    config_path = resolve_path(root, args.config)
    config: dict = {}
    if config_path.exists():
        with config_path.open() as f:
            config = json.load(f)
    elif not args.schedule:
        return fail(f"no config at {config_path}; pass --schedule or --config")

    if args.data_dir:
        data_dir = resolve_path(root, args.data_dir)
    elif config.get("input_dir"):
        data_dir = resolve_path(config_path.parent.parent, config["input_dir"])
    else:
        data_dir = root / "data" / "my_data"

    # The gate comes first. A BLOCKING line means the app has a rule that
    # this solver did not keep, so no draft may go back to the app.
    notes_path = data_dir / "notes.txt"
    if not notes_path.exists():
        return fail(
            f"no notes.txt in {display_path(root, data_dir)}. Run scripts/from_postcallmd.py "
            "on the export first, or pass --data-dir."
        )
    enforces = config.get("enforces") or []
    if not isinstance(enforces, list) or not all(isinstance(t, str) for t in enforces):
        return fail(f'"enforces" in {display_path(root, config_path)} must be a list of tokens')
    solver_path = resolve_path(root, args.solver)
    solver_text = solver_path.read_text(encoding="utf-8") if solver_path.exists() else ""
    blocking, cleared, reasons = split_blocking(blocking_lines(notes_path), enforces, solver_text)
    if blocking:
        print(
            f"to_postcallmd_draft: {display_path(root, notes_path)} has "
            f"{len(blocking)} BLOCKING line(s). The solver may not keep these rules:",
            file=sys.stderr,
        )
        for line in blocking:
            print(f"  {line}", file=sys.stderr)
        for reason in reasons:
            print(f"to_postcallmd_draft: {reason}", file=sys.stderr)
        print(
            "to_postcallmd_draft: nothing was written. Tell the admin about each line. "
            "Fix the rule in the app, or add it to the solver from "
            "docs/adaptation-cookbook.md and list its token in enforces in the config.",
            file=sys.stderr,
        )
        return 2

    if args.schedule:
        schedule_path = resolve_path(root, args.schedule)
    else:
        schedule_path = resolve_path(
            config_path.parent.parent, config.get("output_csv", "output/schedule.csv")
        )
    if not schedule_path.exists():
        return fail(f"no solver output at {schedule_path}; run the solver first")

    clinicians_path = data_dir / "clinicians.csv"
    if not clinicians_path.exists():
        return fail(f"no clinicians.csv in {display_path(root, data_dir)}; pass --data-dir")
    roster = active_roster(clinicians_path)

    try:
        if args.month:
            year, month = parse_month(args.month)
        else:
            coverage_path = data_dir / "coverage.csv"
            if not coverage_path.exists():
                return fail(
                    f"no coverage.csv in {display_path(root, data_dir)}, so the month is "
                    "unknown. Pass --month YYYY-MM."
                )
            year, month = parse_month(coverage_month(coverage_path))
    except ValueError as exc:
        return fail(str(exc))
    month_start = f"{year:04d}-{month:02d}-01"
    month_end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"

    overview_path = resolve_path(root, args.overview)
    if not overview_path.exists():
        return fail(f"no such file: {overview_path}")
    try:
        shift_types = load_shift_types(overview_path)
        code_map = build_code_map(shift_types)
    except (ValueError, json.JSONDecodeError) as exc:
        return fail(f"could not read shift types from {overview_path}: {exc}")
    inactive = inactive_codes(shift_types)

    fields, rows = read_rows(schedule_path)
    lacking = [c for c in OUTPUT_COLUMNS if c not in fields]
    if lacking:
        return fail(f"{schedule_path} lacks columns: {', '.join(lacking)}")
    if not rows:
        return fail(f"{schedule_path} has no assignment rows")

    assignments: list[dict[str, str]] = []
    unknown_codes: dict[str, int] = {}
    bad_ids: list[str] = []
    not_on_roster: list[str] = []
    bad_dates: list[str] = []
    outside_month: list[str] = []
    per_person: Counter = Counter()
    names: dict[str, str] = {}
    for index, row in enumerate(rows, start=2):
        code = (row.get("shift_type") or "").strip()
        clinician_id = (row.get("clinician_id") or "").strip()
        day = (row.get("date") or "").strip()
        shift = code_map.get(normalize_code(code))
        if shift is None:
            unknown_codes[code] = unknown_codes.get(code, 0) + 1
            continue
        if not is_uuid(clinician_id):
            bad_ids.append(f"row {index}: {clinician_id!r} ({row.get('name', '')})")
            continue
        if clinician_id not in roster:
            not_on_roster.append(f"row {index}: {clinician_id} ({row.get('name', '')})")
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            bad_dates.append(f"row {index}: {day!r}")
            continue
        if not month_start <= day <= month_end:
            outside_month.append(f"row {index}: {day}")
            continue
        assignments.append(
            {"date": day, "clinician_id": clinician_id, "shift_type_id": shift["id"]}
        )
        per_person[clinician_id] += 1
        names[clinician_id] = (row.get("name") or clinician_id).strip()

    problems: list[str] = []
    for code, count in sorted(unknown_codes.items()):
        reason = (
            "is inactive in the app"
            if normalize_code(code) in inactive
            else "has no active shift type"
        )
        problems.append(
            f"the code {code!r} {reason} in {display_path(root, overview_path)} "
            f"({count} rows). Known codes: "
            + ", ".join(sorted(v["code"] for v in code_map.values()))
        )
    if bad_ids:
        problems.append(
            "clinician_id is not a UUID, so the schedule did not come from a PostCallMD "
            "export: " + "; ".join(bad_ids[:5])
        )
    if not_on_roster:
        problems.append(
            f"clinician_id is not an active clinician in {display_path(root, clinicians_path)}, "
            "so the schedule is older than the last import. Run the solver again: "
            + "; ".join(not_on_roster[:5])
        )
    if bad_dates:
        problems.append("bad dates: " + "; ".join(bad_dates[:5]))
    if outside_month:
        problems.append(
            f"dates outside the month {month_start[:7]}: "
            + "; ".join(outside_month[:5])
            + ". Pass the right --month, or run the solver for this month."
        )
    if problems:
        for p in problems:
            print(f"to_postcallmd_draft: {p}", file=sys.stderr)
        print("to_postcallmd_draft: nothing was written.", file=sys.stderr)
        return 2

    draft = {
        "start_date": month_start,
        "end_date": month_end,
        "mode": "fill_empty",
        "assignments": assignments,
    }
    out_path = resolve_path(root, args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(draft, f, indent=2)
        f.write("\n")

    if cleared:
        print(f"BLOCKING lines cleared, because {display_path(root, solver_path)} keeps the rule (enforces):")
        for line in cleared:
            print(f"  {line}")
    print(
        f"Wrote {len(assignments)} assignments for {draft['start_date']} to "
        f"{draft['end_date']} to {display_path(root, out_path)}"
    )
    print("Shift types used:")
    used = Counter(normalize_code(r["shift_type"]) for r in rows)
    for key, count in sorted(used.items()):
        shift = code_map[key]
        print(f"  {shift['code']:<12} {count:>3} rows -> {shift['id']}")
    print("Assignments per clinician:")
    for clinician_id, count in sorted(per_person.items(), key=lambda kv: names[kv[0]]):
        print(f"  {names[clinician_id]:<24} {count:>3}")
    print()
    print(
        "Next: call submit_schedule_draft with start_date, end_date, mode and "
        "assignments from that file. Do not publish."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

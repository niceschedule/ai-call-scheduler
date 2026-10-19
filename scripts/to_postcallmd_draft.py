#!/usr/bin/env python3
"""Turn the solver's output CSV into a draft for PostCallMD.

Input:

* The solver output CSV (date, shift_type, clinician_id, name). By default
  the output_csv named in config/my_rules.json.
* The shift-type list with ids: the JSON that the PostCallMD MCP tool
  get_practice_overview returns, saved to a file. Its shift_types array
  carries id and code. A bare array of shift types works too.

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

clinician_id is already the app UUID, because the export wrote it into
clinicians.csv. shift_type_id comes from the code, matched without regard
to case or spacing (solver.py writes "OR CALL" for the app's "OR Call").
A code with no id, or a clinician_id that is not a UUID, stops the script.

    .venv/bin/python scripts/to_postcallmd_draft.py --overview overview.json --out draft.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path

WRAPPER_KEYS = ("structuredContent", "structured_content", "result", "data", "output")
OUTPUT_COLUMNS = ("date", "shift_type", "clinician_id")


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


def build_code_map(shift_types: list[dict]) -> dict[str, dict]:
    """normalized code -> shift type row. Inactive rows are skipped. Two
    active rows with the same normalized code are an error, because the
    solver output cannot tell them apart."""
    by_code: dict[str, dict] = {}
    for row in shift_types:
        code = row.get("code")
        st_id = row.get("id") or row.get("shift_type_id")
        if not code or not st_id:
            raise ValueError(f"a shift type row lacks code or id: {row}")
        if row.get("active") is False:
            continue
        key = normalize_code(str(code))
        if key in by_code and by_code[key]["id"] != st_id:
            raise ValueError(
                f"two active shift types share the code {code!r} "
                f"({by_code[key]['id']} and {st_id}); the solver output cannot tell them apart"
            )
        by_code[key] = {"id": str(st_id), "code": str(code), "name": row.get("name", "")}
    return by_code


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
        "--out",
        default="output/postcallmd_draft.json",
        help="Where to write the draft JSON. Defaults to output/postcallmd_draft.json.",
    )
    args = parser.parse_args()

    if args.schedule:
        schedule_path = resolve_path(root, args.schedule)
    else:
        config_path = resolve_path(root, args.config)
        if not config_path.exists():
            return fail(f"no config at {config_path}; pass --schedule or --config")
        with config_path.open() as f:
            config = json.load(f)
        schedule_path = resolve_path(
            config_path.parent.parent, config.get("output_csv", "output/schedule.csv")
        )
    if not schedule_path.exists():
        return fail(f"no solver output at {schedule_path}; run the solver first")

    overview_path = resolve_path(root, args.overview)
    if not overview_path.exists():
        return fail(f"no such file: {overview_path}")
    try:
        code_map = build_code_map(load_shift_types(overview_path))
    except (ValueError, json.JSONDecodeError) as exc:
        return fail(f"could not read shift types from {overview_path}: {exc}")

    with schedule_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    lacking = [c for c in OUTPUT_COLUMNS if c not in fields]
    if lacking:
        return fail(f"{schedule_path} lacks columns: {', '.join(lacking)}")
    if not rows:
        return fail(f"{schedule_path} has no assignment rows")

    assignments: list[dict[str, str]] = []
    unknown_codes: dict[str, int] = {}
    bad_ids: list[str] = []
    bad_dates: list[str] = []
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
        try:
            uuid.UUID(clinician_id)
        except ValueError:
            bad_ids.append(f"row {index}: {clinician_id!r} ({row.get('name', '')})")
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            bad_dates.append(f"row {index}: {day!r}")
            continue
        assignments.append(
            {"date": day, "clinician_id": clinician_id, "shift_type_id": shift["id"]}
        )
        per_person[clinician_id] += 1
        names[clinician_id] = (row.get("name") or clinician_id).strip()

    problems: list[str] = []
    for code, count in sorted(unknown_codes.items()):
        problems.append(
            f"no shift type in {display_path(root, overview_path)} has the code {code!r} "
            f"({count} rows). Known codes: "
            + ", ".join(sorted(v["code"] for v in code_map.values()))
        )
    if bad_ids:
        problems.append(
            "clinician_id is not a UUID, so the schedule did not come from a PostCallMD "
            "export: " + "; ".join(bad_ids[:5])
        )
    if bad_dates:
        problems.append("bad dates: " + "; ".join(bad_dates[:5]))
    if problems:
        for p in problems:
            print(f"to_postcallmd_draft: {p}", file=sys.stderr)
        print("to_postcallmd_draft: nothing was written.", file=sys.stderr)
        return 2

    dates = sorted(a["date"] for a in assignments)
    draft = {
        "start_date": dates[0],
        "end_date": dates[-1],
        "mode": "fill_empty",
        "assignments": assignments,
    }
    out_path = resolve_path(root, args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(draft, f, indent=2)
        f.write("\n")

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

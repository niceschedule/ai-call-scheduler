#!/usr/bin/env python3
"""Prepare the configured data folder for the next schedule month."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_coverage import find_pattern, generate_coverage  # noqa: E402


HISTORY_FIELDS = ["date", "clinician_id", "shift_type", "status"]
REQUEST_FIELDS = [
    "request_id",
    "clinician_id",
    "start_date",
    "end_date",
    "request_type",
    "hard",
    "shift_type",
    "note",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def read_existing_history(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {
            (
                row.get("date", "").strip(),
                row.get("clinician_id", "").strip(),
                row.get("shift_type", "").strip(),
            )
            for row in csv.DictReader(f)
        }


def append_output_to_history(prior_output: Path, history_path: Path) -> int:
    if not prior_output.exists():
        print(f"No prior output found at {prior_output}; skipped history update.")
        return 0

    existing = read_existing_history(history_path)
    rows_to_append: list[dict[str, str]] = []
    with prior_output.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = (
                row["date"].strip(),
                row["clinician_id"].strip(),
                row["shift_type"].strip(),
            )
            if key in existing:
                continue
            existing.add(key)
            rows_to_append.append(
                {
                    "date": key[0],
                    "clinician_id": key[1],
                    "shift_type": key[2],
                    "status": "final",
                }
            )

    if not rows_to_append:
        print("History already contains the prior output rows.")
        return 0

    history_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not history_path.exists() or history_path.stat().st_size == 0
    with history_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerows(rows_to_append)
    return len(rows_to_append)


def reset_requests(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REQUEST_FIELDS)
        writer.writeheader()


def advance_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def infer_next_month(output_csv: str) -> tuple[int, int] | None:
    """Pull a YYYY-MM prefix off the configured output filename and return
    the month after it. Returns None if the filename has no date prefix
    (e.g. the template default `output/my_schedule.csv`)."""
    stem = Path(output_csv).stem
    match = re.match(r"(\d{4})-(\d{2})", stem)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return advance_month(year, month)


def next_calendar_month(today: date) -> tuple[int, int]:
    return advance_month(today.year, today.month)


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Four-digit year, e.g. 2026. If omitted, inferred from the last "
        "configured output filename (or next calendar month if no prior run).",
    )
    parser.add_argument(
        "--month",
        type=int,
        default=None,
        help="Month number 1-12. If omitted, inferred along with --year.",
    )
    parser.add_argument(
        "--config",
        default="config/my_rules.json",
        help="Configured rules file. Defaults to config/my_rules.json.",
    )
    parser.add_argument(
        "--prior-output",
        default=None,
        help="Prior schedule CSV to append to history. Defaults to current output_csv.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Next output CSV. Defaults to output/YYYY-MM_schedule.csv.",
    )
    parser.add_argument(
        "--reset-requests",
        action="store_true",
        help="Replace requests.csv with just the header row for the new month.",
    )
    args = parser.parse_args()

    if (args.year is None) != (args.month is None):
        parser.error("Pass both --year and --month, or neither (to infer).")

    config_path = resolve_path(root, args.config)
    if not config_path.exists():
        print(f"Missing {display_path(root, config_path)}.")
        print("Create it first:")
        print("  cp -R data/template data/my_data")
        print("  cp config/my_rules.template.json config/my_rules.json")
        return 2

    with config_path.open() as f:
        config = json.load(f)

    base_dir = config_path.parent.parent
    input_dir = resolve_path(base_dir, config["input_dir"])
    if not input_dir.exists():
        print(f"Missing input directory: {input_dir}")
        print("Create it first: cp -R data/template data/my_data")
        return 2

    prior_output_value = args.prior_output or config.get("output_csv", "output/schedule.csv")
    prior_output = resolve_path(base_dir, prior_output_value)

    # Resolve which month we're advancing to.
    if args.year is not None:
        year, month = args.year, args.month
        chosen_via = "explicit"
    else:
        inferred = infer_next_month(prior_output_value)
        if inferred is not None:
            year, month = inferred
            chosen_via = f"inferred from {prior_output_value}"
        else:
            year, month = next_calendar_month(date.today())
            chosen_via = "next calendar month from today"

    if not 1 <= month <= 12:
        parser.error(f"Resolved month {month} is out of range 1-12.")

    print(f"Starting {year}-{month:02d} ({chosen_via}).")
    history_path = input_dir / "history.csv"
    coverage_path = input_dir / "coverage.csv"
    requests_path = input_dir / "requests.csv"

    history_count = append_output_to_history(prior_output, history_path)
    pattern_path = find_pattern(coverage_path)
    coverage_count = generate_coverage(
        year, month, coverage_path, pattern_path
    )

    if args.reset_requests:
        reset_requests(requests_path)
        requests_message = "Reset requests.csv to a blank header."
    else:
        requests_message = "Left requests.csv in place; update it for the new month."

    next_output = args.output_csv or f"output/{year}-{month:02d}_schedule.csv"
    config["output_csv"] = next_output
    with config_path.open("w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print(f"Added {history_count} prior assignments to {history_path}.")
    if pattern_path is None:
        print(
            f"Wrote {coverage_count} coverage rows to {coverage_path} "
            "(default pattern: OR + OB every day; add shift_pattern.csv to customize)."
        )
    else:
        print(
            f"Wrote {coverage_count} coverage rows to {coverage_path} "
            f"using pattern from {pattern_path}."
        )
    print(requests_message)
    print(f"Updated {config_path} to write {next_output}.")
    print()
    print("Next run:")
    prefix = "py -3 scripts/py" if os.name == "nt" else "python3 scripts/py"
    print(f"  {prefix} scripts/run_my_schedule.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

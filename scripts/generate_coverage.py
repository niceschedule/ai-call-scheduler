#!/usr/bin/env python3
"""Generate a coverage.csv for a calendar month from a shift_pattern.csv.

Helper for the PostCallMD AI tutorial. The solver needs a coverage row for
every shift it has to staff; typing them by hand is tedious and error-prone.
This script writes a full month of rows by reading a small shift_pattern.csv
that lives next to the coverage file.

`shift_pattern.csv` columns:

    shift_type      OR, OB, NIGHT, ...
    weekday_mask    Seven characters, Mon..Sun. '1' = include, '0' = skip.
                    Examples:
                        1111111  every day
                        1111100  weekdays only
                        0000011  weekends only
                        0000100  Fridays only
    required_count  How many clinicians needed for that day/shift.

To run different counts on weekdays vs weekends for the same shift type,
add two rows:

    OR,1111100,2
    OR,0000011,1

If no shift_pattern.csv is found next to the output, a default of OR + OB
every day is used so a fresh repo can still run the script. Pass `--pattern`
to point at a file in a different location.

Examples:

    # Default: read data/my_data/shift_pattern.csv if present.
    python scripts/generate_coverage.py --year 2026 --month 7

    # Custom output path:
    python scripts/generate_coverage.py --year 2026 --month 7 \\
        --out data/my_data/coverage.csv

    # Explicit pattern file:
    python scripts/generate_coverage.py --year 2026 --month 7 \\
        --pattern data/my_data/shift_pattern.csv
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path


# Used when no shift_pattern.csv is present. Matches the sample data:
# one OR + one OB every day.
DEFAULT_PATTERN: list[tuple[str, str, int]] = [
    ("OR", "1111111", 1),
    ("OB", "1111111", 1),
]


def parse_mask(mask: str, source: str) -> list[bool]:
    mask = mask.strip()
    if len(mask) != 7 or any(c not in "01" for c in mask):
        raise ValueError(
            f"{source}: weekday_mask must be 7 characters of '0' or '1' "
            f"(Mon..Sun). Got {mask!r}."
        )
    return [c == "1" for c in mask]


def load_pattern(pattern_path: Path | None) -> list[tuple[str, list[bool], int]]:
    if pattern_path is None:
        return [
            (shift_type, parse_mask(mask, "default pattern"), count)
            for shift_type, mask, count in DEFAULT_PATTERN
        ]

    with pattern_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"shift_type", "weekday_mask", "required_count"}
        fields = set(reader.fieldnames or [])
        missing = required - fields
        if missing:
            raise ValueError(
                f"{pattern_path}: missing columns {sorted(missing)}. "
                "Expected shift_type, weekday_mask, required_count."
            )
        rows: list[tuple[str, list[bool], int]] = []
        for index, row in enumerate(reader, start=2):
            shift_type = row["shift_type"].strip().upper()
            if not shift_type:
                continue
            count_raw = row["required_count"].strip()
            try:
                count = int(count_raw) if count_raw else 1
            except ValueError as exc:
                raise ValueError(
                    f"{pattern_path} row {index}: required_count must be a "
                    f"number, got {count_raw!r}."
                ) from exc
            if count < 1:
                raise ValueError(
                    f"{pattern_path} row {index}: required_count must be at "
                    "least 1."
                )
            mask = parse_mask(row["weekday_mask"], f"{pattern_path} row {index}")
            rows.append((shift_type, mask, count))

    if not rows:
        raise ValueError(f"{pattern_path}: no shift rows found.")
    return rows


def find_pattern(output_path: Path) -> Path | None:
    """Look for shift_pattern.csv alongside the coverage output."""
    candidate = output_path.parent / "shift_pattern.csv"
    return candidate if candidate.exists() else None


def generate_coverage(
    year: int,
    month: int,
    output_path: Path,
    pattern_path: Path | None = None,
) -> int:
    if pattern_path is None:
        pattern_path = find_pattern(output_path)
    pattern = load_pattern(pattern_path)

    start_date = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    end_date = next_month - timedelta(days=1)

    rows: list[dict[str, object]] = []
    current = start_date
    while current <= end_date:
        weekday = current.weekday()  # 0 = Mon
        for shift_type, mask, count in pattern:
            if not mask[weekday]:
                continue
            rows.append(
                {
                    "date": current.isoformat(),
                    "shift_type": shift_type,
                    "required_count": count,
                }
            )
        current += timedelta(days=1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date", "shift_type", "required_count"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--year", type=int, required=True, help="Four-digit year, e.g. 2026.")
    parser.add_argument("--month", type=int, required=True, help="Month number 1-12.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path. Defaults to data/my_data/coverage.csv.",
    )
    parser.add_argument(
        "--pattern",
        type=Path,
        default=None,
        help="Path to shift_pattern.csv. Defaults to alongside the output file.",
    )
    args = parser.parse_args()

    if not 1 <= args.month <= 12:
        parser.error("--month must be between 1 and 12.")

    repo_root = Path(__file__).resolve().parent.parent
    output_path = args.out
    if output_path is None:
        working_dir = repo_root / "data" / "my_data"
        if not working_dir.exists():
            parser.error(
                "data/my_data does not exist. Create it first with: "
                "cp -R data/template data/my_data"
            )
        output_path = working_dir / "coverage.csv"
    elif not output_path.is_absolute():
        output_path = repo_root / output_path

    if args.pattern is not None:
        pattern_path = (
            args.pattern if args.pattern.is_absolute() else repo_root / args.pattern
        )
        if not pattern_path.exists():
            parser.error(f"--pattern file not found: {pattern_path}")
    else:
        pattern_path = find_pattern(output_path)

    try:
        count = generate_coverage(args.year, args.month, output_path, pattern_path)
    except ValueError as exc:
        print(f"Could not generate coverage: {exc}")
        return 2
    print(f"Wrote {count} coverage rows to {output_path}")
    if pattern_path is None:
        print(
            f"(No shift_pattern.csv found in {output_path.parent}; "
            "used default: OR + OB every day.)"
        )
    else:
        print(f"Used pattern from {pattern_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check configured schedule data before running the solver."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def resolve_clinician_key(value: str, known_ids: set[str]) -> str:
    value = value.strip()
    if not value or value in known_ids:
        return value
    slug = slugify(value)
    if slug in known_ids:
        return slug
    return value


REQUIRED_FIELDS = {
    "clinicians.csv": [
        "clinician_id",
        "name",
        "active",
        "target_shifts",
        "max_shifts",
        "target_weekend_shifts",
        "max_weekend_shifts",
    ],
    "coverage.csv": ["date", "shift_type", "required_count"],
    "requests.csv": [
        "request_id",
        "clinician_id",
        "start_date",
        "end_date",
        "request_type",
        "hard",
        "shift_type",
        "note",
    ],
    "history.csv": ["date", "clinician_id", "shift_type", "status"],
}

VALID_REQUEST_TYPES = {"vacation", "no_call", "prefer_off", "lock"}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def parse_date_value(value: str) -> date | None:
    value = (value or "").strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def eligibility_column(shift_type: str) -> str:
    return "can_" + shift_type.lower().replace(" ", "_").replace("-", "_")


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="config/my_rules.json",
        help="Configured rules file. Defaults to config/my_rules.json.",
    )
    args = parser.parse_args()

    config_path = resolve_path(root, args.config)
    errors: list[str] = []
    warnings: list[str] = []

    if not config_path.exists():
        errors.append(f"Missing config file: {config_path}")
        errors.append("Create it with: cp config/my_rules.template.json config/my_rules.json")
        return report(errors, warnings)

    with config_path.open() as f:
        config = json.load(f)

    base_dir = config_path.parent.parent
    input_dir = resolve_path(base_dir, config["input_dir"])
    if not input_dir.exists():
        errors.append(f"Missing input directory: {input_dir}")
        errors.append("Create it with: cp -R data/template data/my_data")
        return report(errors, warnings)

    csv_data: dict[str, tuple[list[str], list[dict[str, str]]]] = {}
    for filename, required in REQUIRED_FIELDS.items():
        path = input_dir / filename
        fields, rows = read_csv(path)
        csv_data[filename] = (fields, rows)
        if not path.exists():
            errors.append(f"Missing {path}")
            continue
        missing = [field for field in required if field not in fields]
        if missing:
            errors.append(f"{filename} is missing columns: {', '.join(missing)}")

    clinician_fields, clinician_rows = csv_data.get("clinicians.csv", ([], []))
    coverage_fields, coverage_rows = csv_data.get("coverage.csv", ([], []))
    _request_fields, request_rows = csv_data.get("requests.csv", ([], []))
    _history_fields, history_rows = csv_data.get("history.csv", ([], []))

    active_ids: set[str] = set()
    seen_ids: set[str] = set()
    clinician_max: dict[str, int] = {}
    clinician_weekend_max: dict[str, int] = {}
    total_capacity = 0
    weekend_capacity = 0
    for index, row in enumerate(clinician_rows, start=2):
        clinician_id = row.get("clinician_id", "").strip()
        name = row.get("name", "").strip()
        if not clinician_id:
            if not name:
                errors.append(
                    f"clinicians.csv row {index} has no clinician_id and no name"
                )
                continue
            clinician_id = slugify(name)
            if not clinician_id:
                errors.append(
                    f"clinicians.csv row {index}: could not derive clinician_id "
                    f"from name {name!r}; fill in clinician_id explicitly"
                )
                continue
            row["clinician_id"] = clinician_id
        if clinician_id in seen_ids:
            errors.append(f"clinicians.csv has duplicate clinician_id {clinician_id!r}")
        seen_ids.add(clinician_id)
        b2b = (row.get("allow_b2b_weekends") or "").strip().lower()
        if b2b not in {"", "0", "1", "true", "false", "yes", "no", "y", "n"}:
            errors.append(
                f"clinicians.csv row {index} allow_b2b_weekends is {b2b!r}; use 1, 0 or blank"
            )
        if parse_bool(row.get("active", "1")):
            active_ids.add(clinician_id)
            m = parse_nonnegative_int(
                row.get("max_shifts", ""), f"clinicians.csv row {index} max_shifts", errors
            )
            wm = parse_nonnegative_int(
                row.get("max_weekend_shifts", ""),
                f"clinicians.csv row {index} max_weekend_shifts",
                errors,
            )
            clinician_max[clinician_id] = m
            clinician_weekend_max[clinician_id] = wm
            total_capacity += m
            weekend_capacity += wm

    if clinician_rows and not active_ids:
        errors.append("No active clinicians found in clinicians.csv")

    shift_types: set[str] = set()
    shift_demand: Counter = Counter()
    shift_weekend_demand: Counter = Counter()
    coverage_slots: set[tuple[str, str]] = set()
    coverage_rows_by_slot: dict[tuple[str, str], list[int]] = {}
    total_demand = 0
    weekend_demand = 0
    for index, row in enumerate(coverage_rows, start=2):
        raw_date = row.get("date", "")
        parsed_date = parse_date_value(raw_date)
        if parsed_date is None:
            errors.append(f"coverage.csv row {index} has an invalid date {raw_date!r}")
        shift_type = row.get("shift_type", "").strip().upper()
        if not shift_type:
            errors.append(f"coverage.csv row {index} has a blank shift_type")
            continue
        shift_types.add(shift_type)
        count = parse_positive_int(
            row.get("required_count", ""), f"coverage.csv row {index} required_count", errors
        )
        shift_demand[shift_type] += count
        total_demand += count
        if parsed_date is not None:
            slot = (parsed_date.isoformat(), shift_type)
            coverage_slots.add(slot)
            coverage_rows_by_slot.setdefault(slot, []).append(index)
        if parsed_date is not None and parsed_date.weekday() >= 5:
            shift_weekend_demand[shift_type] += count
            weekend_demand += count

    if not coverage_rows:
        errors.append("coverage.csv has no rows")
    for (slot_date, shift_type), row_numbers in sorted(coverage_rows_by_slot.items()):
        if len(row_numbers) > 1:
            rows_text = ", ".join(str(n) for n in row_numbers)
            warnings.append(
                f"coverage.csv repeats {slot_date} {shift_type} on rows {rows_text}; "
                "combine those into one required_count unless this is intentional."
            )

    shift_eligible: dict[str, list[str]] = {}
    for shift_type in sorted(shift_types):
        column = eligibility_column(shift_type)
        if column not in clinician_fields:
            errors.append(f"Missing eligibility column {column!r} for shift type {shift_type!r}")
            continue
        eligible = [
            row.get("clinician_id", "").strip()
            for row in clinician_rows
            if row.get("clinician_id", "").strip() in active_ids
            and parse_bool(row.get(column, "0"))
        ]
        if not eligible:
            errors.append(f"No active clinicians are eligible for {shift_type}")
            continue
        shift_eligible[shift_type] = eligible

    check_reference_rows(
        "requests.csv",
        request_rows,
        active_ids,
        errors,
        shift_types=shift_types,
        coverage_slots=coverage_slots,
    )
    check_reference_rows("history.csv", history_rows, active_ids, errors)

    # Per-shift-type capacity summary. Catches infeasibilities the global
    # total-demand check would miss (e.g. plenty of total capacity, but only
    # two doctors are eligible for OR and they're maxed).
    summary_lines = build_capacity_summary(
        shift_demand,
        shift_weekend_demand,
        shift_eligible,
        clinician_max,
        clinician_weekend_max,
        errors,
        warnings,
    )

    if total_demand > total_capacity:
        warnings.append(
            f"Total demand ({total_demand}) exceeds total max_shifts ({total_capacity})."
        )
    if weekend_demand > weekend_capacity:
        warnings.append(
            f"Weekend demand ({weekend_demand}) exceeds total max_weekend_shifts ({weekend_capacity})."
        )

    return report(errors, warnings, summary_lines)


def check_reference_rows(
    filename: str,
    rows: list[dict[str, str]],
    active_ids: set[str],
    errors: list[str],
    shift_types: set[str] | None = None,
    coverage_slots: set[tuple[str, str]] | None = None,
) -> None:
    date_fields = ["date"] if filename == "history.csv" else ["start_date", "end_date"]
    for index, row in enumerate(rows, start=2):
        raw_id = row.get("clinician_id", "").strip()
        clinician_id = resolve_clinician_key(raw_id, active_ids)
        if raw_id and clinician_id not in active_ids:
            errors.append(f"{filename} row {index} references unknown clinician {raw_id!r}")
        parsed_dates: dict[str, date] = {}
        for field in date_fields:
            value = row.get(field, "")
            parsed = parse_date_value(value)
            if parsed is None:
                errors.append(f"{filename} row {index} has an invalid {field} {value!r}")
            else:
                parsed_dates[field] = parsed
        if filename == "requests.csv":
            request_type = row.get("request_type", "").strip().lower()
            request_id = row.get("request_id", "").strip() or f"row {index}"
            shift_type = row.get("shift_type", "").strip().upper()
            start = parsed_dates.get("start_date")
            end = parsed_dates.get("end_date")

            if request_type not in VALID_REQUEST_TYPES:
                errors.append(
                    f"{filename} row {index} has unknown request_type "
                    f"{request_type!r}; use one of {sorted(VALID_REQUEST_TYPES)}"
                )
            if start is not None and end is not None and start > end:
                errors.append(
                    f"{filename} row {index} starts after it ends "
                    f"({start.isoformat()} > {end.isoformat()})"
                )
            if shift_type and shift_types is not None and shift_type not in shift_types:
                errors.append(
                    f"{filename} row {index} names shift_type {shift_type!r}, "
                    "but coverage.csv has no rows for that shift."
                )
            if request_type == "lock" and not shift_type:
                errors.append(
                    f"{filename} row {index} is a lock but has no shift_type "
                    "(locks must name the shift to pin)"
                )
            elif (
                request_type == "lock"
                and start is not None
                and end is not None
                and coverage_slots is not None
                and (shift_types is None or shift_type in shift_types)
                and not any(
                    (day.isoformat(), shift_type) in coverage_slots
                    for day in iter_dates(start, end)
                )
            ):
                errors.append(
                    f"Lock request {request_id!r} did not match any coverage row "
                    f"for {shift_type} from {start.isoformat()} to {end.isoformat()}."
                )


def parse_nonnegative_int(value: str, label: str, errors: list[str]) -> int:
    value = value.strip()
    if not value:
        return 0
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"{label} must be a number")
        return 0
    if parsed < 0:
        errors.append(f"{label} cannot be negative")
        return 0
    return parsed


def parse_positive_int(value: str, label: str, errors: list[str]) -> int:
    parsed = parse_nonnegative_int(value, label, errors)
    if parsed == 0:
        errors.append(f"{label} must be at least 1")
    return parsed


def build_capacity_summary(
    shift_demand: Counter,
    shift_weekend_demand: Counter,
    shift_eligible: dict[str, list[str]],
    clinician_max: dict[str, int],
    clinician_weekend_max: dict[str, int],
    errors: list[str],
    warnings: list[str],
) -> list[str]:
    """Build the per-shift-type Capacity summary lines, and append any
    infeasibility errors or tightness warnings while we're at it.

    "Infeasible" here means demand for a single shift type exceeds the
    combined max_shifts of every doctor eligible for it — i.e. even if those
    doctors covered nothing else they couldn't fill the shift. "Tight" means
    the headroom is small enough that a single vacation week could tip the
    solve over: floor of 2, or ~1/6 of demand for larger months.
    """
    if not shift_demand:
        return []

    label_width = max(len(s) for s in shift_demand)

    def tight_threshold(demand: int) -> int:
        return max(2, demand // 6)

    def line(shift_type: str, demand: int, cap: int, eligible_n: int) -> str:
        headroom = cap - demand
        base = (
            f"{shift_type:<{label_width}}  "
            f"{demand:>3} shifts, {eligible_n} eligible (combined max {cap})"
        )
        if headroom < 0:
            return f"{base}. Short by {-headroom}."
        if headroom <= tight_threshold(demand):
            return f"{base}. Headroom {headroom} — tight."
        return f"{base}. Headroom {headroom}."

    lines: list[str] = ["Capacity summary:"]
    for shift_type in sorted(shift_demand):
        demand = shift_demand[shift_type]
        eligible = shift_eligible.get(shift_type, [])
        if not eligible:
            # already errored above (no eligible clinicians); skip this row
            continue
        cap = sum(clinician_max.get(cid, 0) for cid in eligible)
        lines.append("  " + line(shift_type, demand, cap, len(eligible)))
        headroom = cap - demand
        if headroom < 0:
            errors.append(
                f"{shift_type} demand ({demand}) exceeds combined max_shifts "
                f"of eligible doctors ({cap}). No solver can cover this."
            )
        elif headroom <= tight_threshold(demand):
            warnings.append(
                f"{shift_type} is tight: {demand} shifts needed, {cap} capacity "
                f"(headroom {headroom}). One lock or vacation may make it infeasible."
            )

    if shift_weekend_demand:
        lines.append("Weekend coverage:")
        for shift_type in sorted(shift_weekend_demand):
            demand = shift_weekend_demand[shift_type]
            eligible = shift_eligible.get(shift_type, [])
            if not eligible:
                continue
            cap = sum(clinician_weekend_max.get(cid, 0) for cid in eligible)
            lines.append("  " + line(shift_type, demand, cap, len(eligible)))
            headroom = cap - demand
            if headroom < 0:
                errors.append(
                    f"{shift_type} weekend demand ({demand}) exceeds combined "
                    f"max_weekend_shifts of eligible doctors ({cap})."
                )
            elif headroom <= 2:
                warnings.append(
                    f"{shift_type} weekend is tight: {demand} shifts, {cap} capacity "
                    f"(headroom {headroom})."
                )

    return lines


def report(errors: list[str], warnings: list[str], summary: list[str] | None = None) -> int:
    if summary:
        for line in summary:
            print(line)
        print()
    if errors:
        print("Data check found problems:")
        for error in errors:
            print(f"  - {error}")
    if warnings:
        print("Data check warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        return 2
    print("Data check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

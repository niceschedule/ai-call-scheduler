#!/usr/bin/env python3
"""Small CP-SAT call schedule solver for the PostCallMD AI tutorial.

This is a teaching example, not a production scheduler. It keeps the rule set
small on purpose so a human or coding agent can understand and extend it.

The core idea:

1. Read structured CSV inputs.
2. Create a Boolean decision variable for each possible assignment.
3. Add hard constraints that must be true.
4. Add soft penalties for things we prefer to avoid.
5. Ask CP-SAT to minimize the total penalty.
6. Write the chosen assignments back to CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ortools.sat.python import cp_model


@dataclass(frozen=True)
class CoverageRow:
    date: date
    shift_type: str
    required_count: int


@dataclass(frozen=True)
class RequestRow:
    request_id: str
    clinician_id: str
    start_date: date
    end_date: date
    request_type: str
    hard: bool
    shift_type: str | None
    note: str


def parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def parse_int(value: str, default: int = 0) -> int:
    value = (value or "").strip()
    return int(value) if value else default


def read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig strips the byte-order mark Excel writes when you "Save as CSV UTF-8".
    # Without this, the first header (e.g. clinician_id) shows up as "﻿clinician_id"
    # and every row lookup silently misses.
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def dates_between(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def load_config(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def slugify(text: str) -> str:
    """Derive a stable, lowercase, alphanumeric+underscore key from a name.
    Used so users can leave clinician_id blank in clinicians.csv and have it
    derived from the name field, and so they can type names directly in
    requests.csv / history.csv."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def resolve_clinician_key(value: str, clinicians: dict[str, dict[str, str]]) -> str:
    """Map a request/history value to a canonical clinician_id. Matches
    direct ID first, then a slug of the value (so 'Alice Smith' resolves to
    'alice_smith'). Falls through unchanged so unknown-clinician errors
    still fire downstream."""
    value = value.strip()
    if not value or value in clinicians:
        return value
    slug = slugify(value)
    if slug in clinicians:
        return slug
    return value


def load_clinicians(path: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(path)
    clinicians: dict[str, dict[str, str]] = {}
    for row in rows:
        clinician_id = row.get("clinician_id", "").strip()
        name = row.get("name", "").strip()
        if not clinician_id:
            if not name:
                raise ValueError(f"Row in {path} has no clinician_id and no name.")
            clinician_id = slugify(name)
            if not clinician_id:
                raise ValueError(
                    f"Could not derive a clinician_id from name {name!r} in {path}. "
                    "Fill in clinician_id explicitly."
                )
            row["clinician_id"] = clinician_id
        if clinician_id in clinicians:
            raise ValueError(f"Duplicate clinician_id {clinician_id!r}")
        if parse_bool(row.get("active", "1")):
            clinicians[clinician_id] = row
    return clinicians


def load_coverage(path: Path) -> list[CoverageRow]:
    rows = []
    for row in read_csv(path):
        rows.append(
            CoverageRow(
                date=parse_date(row["date"]),
                shift_type=row["shift_type"].strip().upper(),
                required_count=parse_int(row["required_count"], 1),
            )
        )
    rows.sort(key=lambda r: (r.date, r.shift_type))
    return rows


def load_requests(
    path: Path, clinicians: dict[str, dict[str, str]]
) -> list[RequestRow]:
    requests = []
    for row in read_csv(path):
        raw_shift = row.get("shift_type", "").strip().upper()
        hard_value = row.get("hard", "1").strip()
        clinician_id = resolve_clinician_key(row["clinician_id"], clinicians)
        requests.append(
            RequestRow(
                request_id=row["request_id"].strip(),
                clinician_id=clinician_id,
                start_date=parse_date(row["start_date"]),
                end_date=parse_date(row["end_date"]),
                request_type=row["request_type"].strip().lower(),
                hard=parse_bool(hard_value or "1"),
                shift_type=raw_shift or None,
                note=row.get("note", "").strip(),
            )
        )
    return requests


def load_history(
    path: Path, clinicians: dict[str, dict[str, str]]
) -> tuple[Counter[str], Counter[str], Counter[tuple[str, int]]]:
    total = Counter()
    weekend = Counter()
    weekday = Counter()
    if not path.exists():
        return total, weekend, weekday
    for row in read_csv(path):
        clinician_id = resolve_clinician_key(row["clinician_id"], clinicians)
        day = parse_date(row["date"])
        total[clinician_id] += 1
        if is_weekend(day):
            weekend[clinician_id] += 1
        weekday[(clinician_id, day.weekday())] += 1
    return total, weekend, weekday


def eligibility_column(shift_type: str) -> str:
    return "can_" + shift_type.lower().replace(" ", "_").replace("-", "_")


def request_matches(request: RequestRow, day: date, shift_type: str) -> bool:
    if not (request.start_date <= day <= request.end_date):
        return False
    return request.shift_type is None or request.shift_type == shift_type


def solve(config_path: Path, verbose: bool = False) -> int:
    # The config file tells the solver where to find input CSVs, where to write
    # output, and how strongly to weight soft preferences.
    config = load_config(config_path)
    base_dir = config_path.parent.parent
    input_dir = base_dir / config["input_dir"]
    output_path = base_dir / config.get("output_csv", "output/schedule.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clinicians = load_clinicians(input_dir / "clinicians.csv")
    coverage = load_coverage(input_dir / "coverage.csv")
    requests = load_requests(input_dir / "requests.csv", clinicians)
    history_total, history_weekend, history_weekday = load_history(input_dir / "history.csv", clinicians)

    if not clinicians:
        raise ValueError("No active clinicians found.")
    if not coverage:
        raise ValueError("No coverage rows found.")

    for clinician_id in set(history_total) | set(history_weekend):
        if clinician_id not in clinicians:
            raise ValueError(
                f"history.csv references unknown clinician {clinician_id!r}."
            )

    # Every shift type in coverage.csv needs a matching eligibility column in
    # clinicians.csv. For example, OR expects can_or and OB expects can_ob.
    # This lets people add new shift types without changing code immediately.
    shift_types = sorted({row.shift_type for row in coverage})
    for shift_type in shift_types:
        column = eligibility_column(shift_type)
        missing = [cid for cid, row in clinicians.items() if column not in row]
        if missing:
            raise ValueError(
                f"Missing eligibility column {column!r} for shift type {shift_type!r}."
            )

    rules = config.get("rules", {})
    weights = rules.get("weights", {})
    default_min_gap = parse_int(str(rules.get("min_days_between_assignments_default", 1)))
    preferred_gap = parse_int(str(rules.get("preferred_days_between_assignments", 3)))

    model = cp_model.CpModel()
    coverage_ids = list(range(len(coverage)))
    clinician_ids = sorted(clinicians)

    # CP-SAT breaks objective ties by exploration order, which follows variable
    # creation order. Without this shuffle, alphabetically-adjacent clinicians
    # keep landing on the same shifts month after month. Seeding by year+month
    # of the coverage rotates the tiebreak across months while keeping each
    # individual solve reproducible. Multi-month solves use the most common
    # month's seed — proper per-month rotation would also require per-month
    # fairness aggregates (target_shifts is per-clinician across the whole
    # solve period, not per-month), which is out of scope for this tutorial.
    month_counts = Counter((row.date.year, row.date.month) for row in coverage)
    (seed_year, seed_month), _ = month_counts.most_common(1)[0]
    random.Random(seed_year * 100 + seed_month).shuffle(clinician_ids)

    # Decision variables:
    #
    # x[(coverage row, clinician)] is 1 if that clinician is assigned to that
    # required shift, otherwise 0.
    #
    # Example:
    #   x[(June 1 OB, Dr. Fox)] = 1 means Dr. Fox covers OB on June 1.
    x = {}
    for cov_id, cov in enumerate(coverage):
        for clinician_id in clinician_ids:
            x[(cov_id, clinician_id)] = model.NewBoolVar(
                f"assign_{cov.date}_{cov.shift_type}_{clinician_id}"
            )

    # HARD RULE 1:
    # Cover every required shift.
    #
    # If coverage.csv says June 1 needs one OB clinician, exactly one assignment
    # variable for that row must be selected.
    for cov_id, cov in enumerate(coverage):
        model.Add(sum(x[(cov_id, cid)] for cid in clinician_ids) == cov.required_count)

    # HARD RULE 2:
    # Only assign clinicians to shifts they are eligible to cover.
    #
    # If can_ob is 0, that clinician can never be assigned to OB rows.
    for cov_id, cov in enumerate(coverage):
        column = eligibility_column(cov.shift_type)
        for clinician_id in clinician_ids:
            if not parse_bool(clinicians[clinician_id].get(column, "0")):
                model.Add(x[(cov_id, clinician_id)] == 0)

    coverage_by_date: dict[date, list[int]] = defaultdict(list)
    for cov_id, cov in enumerate(coverage):
        coverage_by_date[cov.date].append(cov_id)

    day_assigned = {}
    for day, cov_ids in coverage_by_date.items():
        for clinician_id in clinician_ids:
            # day_assigned is 1 if the clinician works any shift that day.
            # Because it is a BoolVar and equals the sum of that person's daily
            # assignments, it also enforces at most one assignment per day.
            var = model.NewBoolVar(f"works_{day}_{clinician_id}")
            model.Add(var == sum(x[(cov_id, clinician_id)] for cov_id in cov_ids))
            day_assigned[(day, clinician_id)] = var

    # HARD RULE 3 and SOFT PREFERENCE 1:
    # Honor hard requests, try to honor soft requests, and pin locks.
    #
    # request_type=lock means the clinician MUST cover that (date, shift_type)
    # row. shift_type is required on locks — pinning to "anything that day"
    # isn't meaningful. Locks are always hard.
    #
    # Otherwise:
    # - hard=1 means the assignment is blocked.
    # - hard=0 means the solver may assign the clinician, but pays a penalty.
    objective_terms = []
    soft_request_weight = parse_int(str(weights.get("soft_request_violation", 25)))
    for request in requests:
        if request.clinician_id not in clinicians:
            raise ValueError(
                f"Request {request.request_id!r} references unknown clinician "
                f"{request.clinician_id!r}."
            )
        if request.request_type == "lock" and request.shift_type is None:
            raise ValueError(
                f"Lock request {request.request_id!r} must set shift_type."
            )
        matched_any = False
        for cov_id, cov in enumerate(coverage):
            if not request_matches(request, cov.date, cov.shift_type):
                continue
            matched_any = True
            var = x[(cov_id, request.clinician_id)]
            if request.request_type == "lock":
                model.Add(var == 1)
            elif request.hard:
                model.Add(var == 0)
            else:
                objective_terms.append(soft_request_weight * var)
        if request.request_type == "lock" and not matched_any:
            raise ValueError(
                f"Lock request {request.request_id!r} did not match any "
                f"coverage row for {request.clinician_id} on "
                f"{request.start_date}..{request.end_date} "
                f"shift_type={request.shift_type}. Check the date and shift_type "
                "against coverage.csv."
            )

    all_dates = sorted(coverage_by_date)

    # Per-clinician assignment limits and fairness goals.
    target_weight = parse_int(str(weights.get("target_deviation", 100)))
    weekend_weight = parse_int(str(weights.get("weekend_deviation", 35)))
    max_assignments_possible = len(coverage)

    assigned_count = {}
    weekend_count = {}
    for clinician_id in clinician_ids:
        assigned_expr = sum(x[(cov_id, clinician_id)] for cov_id in coverage_ids)
        weekend_expr = sum(
            x[(cov_id, clinician_id)]
            for cov_id, cov in enumerate(coverage)
            if is_weekend(cov.date)
        )
        assigned_count[clinician_id] = assigned_expr
        weekend_count[clinician_id] = weekend_expr

        # HARD RULE 4:
        # Respect maximum total shifts and maximum weekend shifts.
        max_shifts = parse_int(clinicians[clinician_id].get("max_shifts", ""), 999)
        max_weekends = parse_int(
            clinicians[clinician_id].get("max_weekend_shifts", ""), 999
        )
        model.Add(assigned_expr <= max_shifts)
        model.Add(weekend_expr <= max_weekends)

        # SOFT PREFERENCE 2:
        # Try to land near each clinician's target shift count.
        #
        # CP-SAT cannot directly minimize abs(assigned - target), so we model
        # "over" and "under" variables where:
        #   assigned - target = over - under
        # Then minimizing over + under minimizes the absolute deviation.
        target = parse_int(clinicians[clinician_id].get("target_shifts", ""), 0)
        over = model.NewIntVar(0, max_assignments_possible, f"over_target_{clinician_id}")
        under = model.NewIntVar(
            0, max_assignments_possible, f"under_target_{clinician_id}"
        )
        model.Add(assigned_expr - target == over - under)
        objective_terms.append(target_weight * (over + under))

        # SOFT PREFERENCE 3:
        # Same idea, but for weekend assignments.
        weekend_target = parse_int(
            clinicians[clinician_id].get("target_weekend_shifts", ""), 0
        )
        weekend_over = model.NewIntVar(
            0, max_assignments_possible, f"over_weekend_{clinician_id}"
        )
        weekend_under = model.NewIntVar(
            0, max_assignments_possible, f"under_weekend_{clinician_id}"
        )
        model.Add(weekend_expr - weekend_target == weekend_over - weekend_under)
        objective_terms.append(weekend_weight * (weekend_over + weekend_under))

    # SOFT PREFERENCE 4:
    # Balance current assignments plus historical burden.
    #
    # If someone already carried more call last month, the solver should avoid
    # increasing that gap unless coverage requires it.
    history_weight = parse_int(str(weights.get("history_balance", 40)))
    max_total = model.NewIntVar(0, 1000, "max_total_burden")
    min_total = model.NewIntVar(0, 1000, "min_total_burden")
    for clinician_id in clinician_ids:
        total_expr = assigned_count[clinician_id] + history_total[clinician_id]
        model.Add(total_expr <= max_total)
        model.Add(total_expr >= min_total)
    objective_terms.append(history_weight * (max_total - min_total))

    # SOFT PREFERENCE 5:
    # Spread weekday patterns across months. Penalize giving a clinician a
    # weekday they've already covered a lot in history.csv. Pulls apart
    # stagnation patterns like "Cary always gets Mondays" over time. No-op
    # on a first-month solve (history_weekday is empty). sample_rules.json
    # sets this weight to 0 so the article's sample output stays stable;
    # my_rules.template.json sets 20 so real groups get the term active.
    weekday_repeat_weight = parse_int(str(weights.get("weekday_repeat", 20)))
    for clinician_id in clinician_ids:
        for weekday in range(7):
            prior = history_weekday[(clinician_id, weekday)]
            if prior == 0:
                continue
            same_weekday_now = sum(
                x[(cov_id, clinician_id)]
                for cov_id, cov in enumerate(coverage)
                if cov.date.weekday() == weekday
            )
            objective_terms.append(weekday_repeat_weight * prior * same_weekday_now)

    # HARD RULE 5 and SOFT PREFERENCE 6:
    # Enforce minimum rest, then prefer more spacing when possible.
    #
    # min_days_between_assignments is hard. If min gap is 1, someone cannot work
    # Monday and Tuesday.
    #
    # preferred_days_between_assignments is soft. If preferred gap is 3, the
    # solver pays a small penalty for assignments that are 2 or 3 days apart,
    # but it may still use them when that is the best feasible schedule.
    rest_weight = parse_int(str(weights.get("rest_spacing", 5)))
    for clinician_id in clinician_ids:
        min_gap = parse_int(
            clinicians[clinician_id].get("min_days_between_assignments", ""),
            default_min_gap,
        )
        # Scan far enough to cover BOTH the hard floor and the soft preference.
        # A clinician's min_gap can exceed the (soft) preferred gap — e.g. a
        # personal "at least 5 days between calls" while the group prefers 3.
        # Stopping at preferred_gap would silently drop the hard a+b<=1 for
        # those wider pairs, letting the minimum-rest rule be violated.
        window = max(min_gap, preferred_gap)
        for i, day_a in enumerate(all_dates):
            for day_b in all_dates[i + 1 :]:
                gap = (day_b - day_a).days
                if gap > window:
                    break
                a = day_assigned[(day_a, clinician_id)]
                b = day_assigned[(day_b, clinician_id)]
                if gap <= min_gap:
                    model.Add(a + b <= 1)
                else:
                    both = model.NewBoolVar(f"close_{day_a}_{day_b}_{clinician_id}")
                    model.Add(both <= a)
                    model.Add(both <= b)
                    model.Add(both >= a + b - 1)
                    objective_terms.append(rest_weight * (preferred_gap + 1 - gap) * both)

    # The objective is the total penalty across all soft preferences. Hard rules
    # are not in the objective because they are mandatory.
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60
    # Reproducible by default: same inputs always produce the same schedule on
    # any machine. That lets the article show a specific arrangement and have
    # it actually match what a reader gets when they run the solver.
    # interleave_search + random_seed makes parallel search deterministic;
    # without interleave_search, 8 workers are faster but non-reproducible.
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 1
    solver.parameters.interleave_search = True
    if verbose:
        solver.parameters.log_search_progress = True
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("No feasible schedule found.")
        print("Try relaxing max_shifts, max_weekend_shifts, rest gaps, or hard requests.")
        return 2

    if verbose:
        print(solver.ResponseStats())

    # Convert selected assignment variables back into rows a human can inspect.
    rows = []
    for cov_id, cov in enumerate(coverage):
        for clinician_id in clinician_ids:
            if solver.Value(x[(cov_id, clinician_id)]) == 1:
                rows.append(
                    {
                        "date": cov.date.isoformat(),
                        "shift_type": cov.shift_type,
                        "clinician_id": clinician_id,
                        "name": clinicians[clinician_id]["name"],
                    }
                )

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date", "shift_type", "clinician_id", "name"]
        )
        writer.writeheader()
        writer.writerows(rows)

    # Lazy import keeps solver.py free of HTML/CSS context — see schedule_html.py
    # to change how the printable schedule looks.
    from schedule_html import write_html_schedule

    html_output_path = output_path.with_suffix(".html")
    write_html_schedule(html_output_path, coverage, clinicians, rows, requests, history_total)

    status_name = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"
    print(f"Status: {status_name}")
    if verbose:
        print(f"Objective: {solver.ObjectiveValue():.0f}")
    print(f"Wrote {len(rows)} assignments to {output_path}")
    print()
    print("Assignment summary:")
    for clinician_id in clinician_ids:
        current = int(solver.Value(assigned_count[clinician_id]))
        weekends = int(solver.Value(weekend_count[clinician_id]))
        prior = history_total[clinician_id]
        print(
            f"  {clinicians[clinician_id]['name']}: "
            f"{current} current, {weekends} weekend, {prior} prior"
        )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config/sample_rules.json",
        help="Path to JSON config, relative to this folder or absolute.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print solver diagnostics such as the objective score.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    return solve(config_path, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())

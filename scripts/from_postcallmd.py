#!/usr/bin/env python3
"""Turn a PostCallMD export into the solver's input files.

Input: the JSON object that the PostCallMD MCP tool export_solver_inputs
returns, saved to a file. It has six string keys: clinicians_csv,
coverage_csv, requests_csv, history_csv, shift_types_csv and notes_txt.

Output, in the data folder (data/my_data by default):

    clinicians.csv  coverage.csv  requests.csv  history.csv
    shift_types.csv (the solver does not read it; the cookbook rules do)
    notes.txt       (the export's notes, also printed below)

Also config/my_rules.json, copied from config/my_rules.template.json when it
does not exist yet. An existing config is left as it is.

Nothing in the export is dropped. A missing key, an empty file or a file
without the columns the solver needs stops the script with a message.

    .venv/bin/python scripts/from_postcallmd.py export.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

EXPORT_KEYS = (
    "clinicians_csv",
    "coverage_csv",
    "requests_csv",
    "history_csv",
    "shift_types_csv",
    "notes_txt",
)

# The columns each file must have for solver.py and scripts/check_my_data.py.
REQUIRED_COLUMNS = {
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
    "shift_types.csv": [
        "code",
        "name",
        "category",
        "post_call_ok",
        "next_day_off",
        "weekend_block",
        "color",
    ],
}

# Keys some MCP clients wrap a tool result in when they save it to a file.
WRAPPER_KEYS = ("structuredContent", "structured_content", "result", "data", "output")


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
    print(f"from_postcallmd: {message}", file=sys.stderr)
    return 2


def load_export(path: Path) -> dict:
    """Read the export JSON. Unwrap the one-level wrapper some MCP clients
    add, and a JSON string that holds the object."""
    with path.open(encoding="utf-8-sig") as f:
        data = json.load(f)
    for _ in range(3):
        if isinstance(data, str):
            data = json.loads(data)
            continue
        if isinstance(data, dict) and not any(k in data for k in EXPORT_KEYS):
            inner = next(
                (data[k] for k in WRAPPER_KEYS if isinstance(data.get(k), (dict, str))),
                None,
            )
            if inner is not None:
                data = inner
                continue
        break
    if not isinstance(data, dict):
        raise ValueError("the file does not hold a JSON object")
    return data


def csv_header(text: str) -> list[str]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    for row in reader:
        return [cell.strip() for cell in row]
    return []


def csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def month_of(coverage_csv: str) -> str | None:
    """YYYY-MM of the first coverage row, for the output file name."""
    for row in csv_rows(coverage_csv):
        value = (row.get("date") or "").strip()
        if len(value) >= 7:
            return value[:7]
    return None


def create_config(
    config_path: Path, template_path: Path, data_dir: Path, month: str | None
) -> dict:
    with template_path.open() as f:
        config = json.load(f)
    base_dir = config_path.parent.parent
    try:
        config["input_dir"] = str(data_dir.relative_to(base_dir))
    except ValueError:
        config["input_dir"] = str(data_dir)
    if month:
        config["output_csv"] = f"output/schedule_{month}.csv"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return config


def flag_lines(shift_types_csv: str) -> list[str]:
    """One line per shift type whose rules the solver does not enforce."""
    lines = []
    for row in csv_rows(shift_types_csv):
        flags = []
        if (row.get("next_day_off") or "").strip() == "1":
            flags.append("next_day_off")
        if (row.get("weekend_block") or "").strip() == "1":
            flags.append("weekend_block")
        if (row.get("post_call_ok") or "").strip() == "0":
            flags.append("post_call_ok=0")
        if flags:
            lines.append(f"  {row.get('code', '').strip()}: {', '.join(flags)}")
    return lines


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("export_json", help="The saved result of export_solver_inputs.")
    parser.add_argument(
        "--data-dir",
        default="data/my_data",
        help="Where to write the CSV files. Defaults to data/my_data.",
    )
    parser.add_argument(
        "--config",
        default="config/my_rules.json",
        help="Rules file to create when missing. Defaults to config/my_rules.json.",
    )
    parser.add_argument(
        "--template",
        default="config/my_rules.template.json",
        help="Template for a new rules file. Defaults to config/my_rules.template.json.",
    )
    args = parser.parse_args()

    export_path = resolve_path(root, args.export_json)
    if not export_path.exists():
        return fail(f"no such file: {export_path}")
    try:
        export = load_export(export_path)
    except (ValueError, json.JSONDecodeError) as exc:
        return fail(f"could not read {export_path}: {exc}")

    missing = [k for k in EXPORT_KEYS if k not in export]
    if missing:
        return fail(
            "the export is missing " + ", ".join(missing) + ". "
            "Save the full result of export_solver_inputs, which has six keys: "
            + ", ".join(EXPORT_KEYS) + "."
        )
    not_text = [k for k in EXPORT_KEYS if not isinstance(export[k], str)]
    if not_text:
        return fail("these keys must hold text: " + ", ".join(not_text))
    extra = sorted(k for k in export if k not in EXPORT_KEYS)

    problems: list[str] = []
    for key in EXPORT_KEYS[:-1]:
        filename = key[: -len("_csv")] + ".csv"
        header = csv_header(export[key])
        if not header:
            problems.append(f"{filename} is empty (key {key})")
            continue
        lacking = [c for c in REQUIRED_COLUMNS[filename] if c not in header]
        if lacking:
            problems.append(f"{filename} lacks columns: {', '.join(lacking)}")
    if not csv_rows(export["coverage_csv"]):
        problems.append("coverage.csv has no rows, so there is nothing to schedule")
    if problems:
        for p in problems:
            print(f"from_postcallmd: {p}", file=sys.stderr)
        return 2

    data_dir = resolve_path(root, args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, int]] = []
    for key in EXPORT_KEYS:
        filename = key[: -len("_csv")] + ".csv" if key.endswith("_csv") else "notes.txt"
        text = export[key]
        if not text.endswith("\n"):
            text += "\n"
        (data_dir / filename).write_text(text, encoding="utf-8", newline="")
        count = len(csv_rows(text)) if filename.endswith(".csv") else len(text.splitlines())
        written.append((filename, count))

    config_path = resolve_path(root, args.config)
    template_path = resolve_path(root, args.template)
    config_created = False
    if config_path.exists():
        with config_path.open() as f:
            config = json.load(f)
    else:
        if not template_path.exists():
            return fail(f"no template at {template_path}; cannot create {config_path}")
        config = create_config(config_path, template_path, data_dir, month_of(export["coverage_csv"]))
        config_created = True

    print(f"Wrote {display_path(root, data_dir)}/:")
    for filename, count in written:
        unit = "rows" if filename.endswith(".csv") else "lines"
        print(f"  {filename:<16} {count:>4} {unit}")
    if extra:
        print("Keys in the export that this script does not use: " + ", ".join(extra))
    if config_created:
        print(
            f"Created {display_path(root, config_path)} from "
            f"{display_path(root, template_path)}. Output goes to {config['output_csv']}."
        )
    else:
        print(
            f"Kept the existing {display_path(root, config_path)}. "
            f"It reads {config.get('input_dir')} and writes {config.get('output_csv')}."
        )
    configured_dir = resolve_path(config_path.parent.parent, config.get("input_dir", ""))
    if configured_dir.resolve() != data_dir.resolve():
        print(
            f"Warning: the config reads {display_path(root, configured_dir)}, "
            f"not {display_path(root, data_dir)}. Change input_dir or pass --data-dir."
        )

    flags = flag_lines(export["shift_types_csv"])
    if flags:
        print()
        print("Shift-type rules in shift_types.csv that the solver does not enforce by default:")
        for line in flags:
            print(line)
        print("See docs/adaptation-cookbook.md, section 'App rules from shift_types.csv'.")

    print()
    print("Notes from the export (read every line):")
    print("-" * 60)
    print(export["notes_txt"].rstrip("\n"))
    print("-" * 60)
    print()
    print("Next:")
    print(f"  .venv/bin/python scripts/check_my_data.py --config {display_path(root, config_path)}")
    print(f"  .venv/bin/python scripts/run_my_schedule.py --config {display_path(root, config_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Turn a PostCallMD export into the solver's input files.

Input: the JSON object that the PostCallMD MCP tool export_solver_inputs
returns, saved to a file. It has six string keys: clinicians_csv,
coverage_csv, requests_csv, history_csv, shift_types_csv and notes_txt.
A newer export also has settings_json, a JSON object of practice settings.
An export without it still loads.

Output, in the data folder (data/my_data by default):

    clinicians.csv  coverage.csv  requests.csv  history.csv
    shift_types.csv (solver.py reads its weekend_block, next_day_off and
                     post_call_ok flags and keeps them as hard rules)
    notes.txt       (the export's notes, unchanged, also printed below)
    settings.json   (settings_json, unchanged, when the export has it)

Also config/my_rules.json, copied from config/my_rules.template.json when it
does not exist yet. On every run, output_csv in the config is set to
output/schedule_YYYY-MM.csv for the month of coverage.csv, so a rerun for a
new month never writes over the last month's schedule. Each practice
setting in settings_json that the solver reads (avoid_b2b_weekends) is set
under "rules", so the app setting wins. The other keys of an existing
config are kept.

The script stops before it writes anything when:

* a key is missing, a file is empty, or a file lacks a column the solver
  needs;
* coverage.csv has no rows, or rows in more than one month;
* the existing config reads another folder (input_dir) than --data-dir.
  Pass --force to point input_dir at --data-dir.

A history row for a clinician who is not an active row of clinicians.csv,
for example someone who left the practice, would stop the data check and
the solver. The script moves such rows to history_unmatched.csv, names
them, and keeps the rest in history.csv. The solver does not read the
moved rows. They cannot change anyone's fairness this month.

Nothing in the export is dropped. notes.txt may have a "blocking" section
with lines that start with "BLOCKING:". Each one is a rule the solver may
not keep. The script prints them, and scripts/to_postcallmd_draft.py refuses
to write a draft while any is there. A line for weekend_block, next_day_off
or post_call_ok clears when config/my_rules.json lists the token in
"enforces" (the template lists all three) and solver.py has the rule (the
stock solver.py has all three).

    python3 scripts/py scripts/from_postcallmd.py export.json     (Windows: py -3 scripts/py ...)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
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

# Keys a newer export adds. An export without them still loads.
OPTIONAL_KEYS = ("settings_json",)

# Practice settings in settings_json that the script copies into the config,
# under "rules". The value is the type each must have.
SETTINGS_RULES = {"avoid_b2b_weekends": bool}

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


def launcher() -> str:
    """The command prefix that runs a script with the venv's Python here."""
    return "py -3 scripts/py" if os.name == "nt" else "python3 scripts/py"


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


def months_of(coverage_csv: str) -> list[str]:
    """Every YYYY-MM that a coverage row falls in, sorted."""
    return sorted(
        {
            (row.get("date") or "").strip()[:7]
            for row in csv_rows(coverage_csv)
            if re.match(r"\d{4}-\d{2}", (row.get("date") or "").strip())
        }
    )


def config_input_dir(config_path: Path, data_dir: Path) -> str:
    """input_dir for the config: relative to the config's base folder when
    the data folder is inside it, else absolute."""
    try:
        return data_dir.relative_to(config_path.parent.parent).as_posix()
    except ValueError:
        return str(data_dir)


def write_config(config_path: Path, config: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def split_history(history_csv: str, clinicians_csv: str) -> tuple[str, str | None, list[str]]:
    """Split history into rows for active clinicians and the rest. Returns
    the history text, the unmatched text (None when every row matches) and
    one line per unknown clinician."""
    known = {
        (row.get("clinician_id") or "").strip()
        for row in csv_rows(clinicians_csv)
        if (row.get("active") or "1").strip().lower() in {"1", "true", "yes", "y"}
    }
    known |= {(row.get("name") or "").strip() for row in csv_rows(clinicians_csv)
              if (row.get("clinician_id") or "").strip() in known}
    header = csv_header(history_csv)
    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    for row in csv_rows(history_csv):
        (matched if (row.get("clinician_id") or "").strip() in known else unmatched).append(row)
    if not unmatched:
        return history_csv, None, []

    def render(rows: list[dict[str, str]]) -> str:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=header, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()

    counts: dict[str, int] = {}
    for row in unmatched:
        key = (row.get("clinician_id") or "").strip() or "(blank)"
        counts[key] = counts.get(key, 0) + 1
    lines = [f"  {key}: {n} row(s)" for key, n in sorted(counts.items())]
    return render(matched), render(unmatched), lines


def parse_settings(text: str) -> tuple[dict, list[str]]:
    """Read settings_json. Returns the rules to set and the unknown keys.
    Raises ValueError when the text is not a JSON object or a value has the
    wrong type."""
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("settings_json must hold a JSON object")
    rules = {}
    for key, kind in SETTINGS_RULES.items():
        if key in data:
            if not isinstance(data[key], kind):
                raise ValueError(f"settings_json {key} must be {kind.__name__}, not {data[key]!r}")
            rules[key] = data[key]
    return rules, sorted(k for k in data if k not in SETTINGS_RULES)


def b2b_exempt_names(clinicians_csv: str) -> list[str]:
    """Names of active clinicians with allow_b2b_weekends = 1."""
    yes = {"1", "true", "yes", "y"}
    return [
        (row.get("name") or row.get("clinician_id") or "").strip()
        for row in csv_rows(clinicians_csv)
        if (row.get("allow_b2b_weekends") or "").strip().lower() in yes
        and (row.get("active") or "1").strip().lower() in yes
    ]


def blocking_lines(notes_txt: str) -> list[str]:
    """Lines of the export's notes that start with BLOCKING:."""
    return [line.strip() for line in notes_txt.splitlines() if line.lstrip().startswith("BLOCKING:")]


def flag_lines(shift_types_csv: str) -> list[str]:
    """One line per shift type that carries a rule flag."""
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
    parser.add_argument(
        "--solver",
        default="solver.py",
        help="The solver to check for the rules in enforces. Defaults to solver.py.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="When the existing config reads another folder, point it at --data-dir.",
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
    not_text = [
        k for k in EXPORT_KEYS + OPTIONAL_KEYS if k in export and not isinstance(export[k], str)
    ]
    if not_text:
        return fail("these keys must hold text: " + ", ".join(not_text))
    extra = sorted(k for k in export if k not in EXPORT_KEYS + OPTIONAL_KEYS)
    settings_rules: dict = {}
    unknown_settings: list[str] = []
    if "settings_json" in export:
        try:
            settings_rules, unknown_settings = parse_settings(export["settings_json"])
        except (ValueError, json.JSONDecodeError) as exc:
            return fail(f"could not read settings_json: {exc}")

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
    months: list[str] = []
    if not problems:
        months = months_of(export["coverage_csv"])
        if not csv_rows(export["coverage_csv"]):
            problems.append("coverage.csv has no rows, so there is nothing to schedule")
        elif not months:
            problems.append("coverage.csv has no dates in YYYY-MM-DD form")
        elif len(months) > 1:
            problems.append(
                "coverage.csv spans more than one month (" + ", ".join(months) + "). "
                "Export one month at a time."
            )
    if problems:
        for p in problems:
            print(f"from_postcallmd: {p}", file=sys.stderr)
        return 2
    month = months[0]
    output_csv = f"output/schedule_{month}.csv"

    data_dir = resolve_path(root, args.data_dir)
    config_path = resolve_path(root, args.config)
    template_path = resolve_path(root, args.template)

    # Read or create the config before anything is written, so a mismatch
    # stops the run with the data folder untouched.
    config_created = False
    input_dir_moved = False
    if config_path.exists():
        with config_path.open() as f:
            config = json.load(f)
        configured_dir = resolve_path(config_path.parent.parent, config.get("input_dir", ""))
        if configured_dir.resolve() != data_dir.resolve():
            if not args.force:
                return fail(
                    f"{display_path(root, config_path)} reads "
                    f"{display_path(root, configured_dir)}, not {display_path(root, data_dir)}. "
                    "Nothing was written. Pass the same folder with --data-dir, or pass --force "
                    "to point the config at --data-dir."
                )
            config["input_dir"] = config_input_dir(config_path, data_dir)
            input_dir_moved = True
    else:
        if not template_path.exists():
            return fail(f"no template at {template_path}; cannot create {config_path}")
        with template_path.open() as f:
            config = json.load(f)
        config["input_dir"] = config_input_dir(config_path, data_dir)
        config_created = True
    previous_output = config.get("output_csv")
    config["output_csv"] = output_csv
    # The practice settings win over the config: the app is the record.
    settings_changed: list[str] = []
    if settings_rules:
        rules = config.setdefault("rules", {})
        for key, value in settings_rules.items():
            if rules.get(key) != value:
                settings_changed.append(f"{key}: {json.dumps(rules.get(key))} -> {json.dumps(value)}")
            rules[key] = value
    data_dir.mkdir(parents=True, exist_ok=True)
    history_text, unmatched_text, unmatched_lines = split_history(
        export["history_csv"], export["clinicians_csv"]
    )
    unmatched_path = data_dir / "history_unmatched.csv"
    written: list[tuple[str, int]] = []
    file_names = {"notes_txt": "notes.txt", "settings_json": "settings.json"}
    for key in EXPORT_KEYS + tuple(k for k in OPTIONAL_KEYS if k in export):
        filename = file_names.get(key) or key[: -len("_csv")] + ".csv"
        text = history_text if key == "history_csv" else export[key]
        if not text.endswith("\n"):
            text += "\n"
        # open(newline="") keeps the export's line endings. Path.write_text
        # has no newline argument before Python 3.10.
        with (data_dir / filename).open("w", encoding="utf-8", newline="") as f:
            f.write(text)
        count = len(csv_rows(text)) if filename.endswith(".csv") else len(text.splitlines())
        written.append((filename, count))
    if unmatched_text is not None:
        with unmatched_path.open("w", encoding="utf-8", newline="") as f:
            f.write(unmatched_text)
        written.append((unmatched_path.name, len(csv_rows(unmatched_text))))
    elif unmatched_path.exists():
        unmatched_path.unlink()  # left from an earlier month

    write_config(config_path, config)

    print(f"Wrote {display_path(root, data_dir)}/:")
    for filename, count in written:
        unit = "rows" if filename.endswith(".csv") else "lines"
        print(f"  {filename:<16} {count:>4} {unit}")
    if extra:
        print("Keys in the export that this script does not use: " + ", ".join(extra))
    if unknown_settings:
        print("Settings in settings_json that this script does not use: " + ", ".join(unknown_settings))
    if unmatched_lines:
        print(
            "History rows for clinicians who are not active in clinicians.csv were moved "
            f"to {unmatched_path.name}. The solver does not read them:"
        )
        for line in unmatched_lines:
            print(line)
    if config_created:
        print(
            f"Created {display_path(root, config_path)} from "
            f"{display_path(root, template_path)}. Month {month}, output goes to {output_csv}."
        )
    else:
        print(
            f"Updated {display_path(root, config_path)} for month {month}. "
            f"It reads {config.get('input_dir')} and writes {output_csv}."
        )
        if previous_output and previous_output != output_csv:
            print(f"  output_csv was {previous_output}. That file is kept.")
        if input_dir_moved:
            print(f"  input_dir now points at {config['input_dir']} (--force).")

    if settings_rules:
        print()
        print(f"Practice settings from settings_json, set under rules in {display_path(root, config_path)}:")
        for key, value in settings_rules.items():
            print(f"  {key} = {json.dumps(value)}")
        for line in settings_changed:
            print(f"  changed {line}")
    exempt = b2b_exempt_names(export["clinicians_csv"])
    if exempt:
        print()
        print(
            "allow_b2b_weekends = 1 in clinicians.csv (consented). solver.py gives them no "
            "b2b_weekend penalty: " + ", ".join(exempt)
        )

    flags = flag_lines(export["shift_types_csv"])
    if flags:
        print()
        print("Shift-type rules in shift_types.csv. solver.py keeps them as hard rules, with no code change:")
        for line in flags:
            print(line)
        print("scripts/check_schedule.py checks them on the output. See docs/csv-schema.md, shift_types.csv.")

    print()
    print("Notes from the export (read every line):")
    print("-" * 60)
    print(export["notes_txt"].rstrip("\n"))
    print("-" * 60)
    # The same check the draft script makes, so the STOP line names only
    # what will really stop the draft.
    from to_postcallmd_draft import split_blocking

    solver_path = resolve_path(root, args.solver)
    solver_text = solver_path.read_text(encoding="utf-8") if solver_path.exists() else ""
    enforces = config.get("enforces") if isinstance(config.get("enforces"), list) else []
    blocking, cleared, _ = split_blocking(blocking_lines(export["notes_txt"]), enforces, solver_text)
    if cleared:
        print()
        print(f"BLOCKING lines that solver.py keeps (enforces in {display_path(root, config_path)}):")
        for line in cleared:
            print(f"  {line}")
    if blocking:
        print()
        print(
            f"STOP: the export has {len(blocking)} BLOCKING line(s). The solver may not keep "
            "these rules, so scripts/to_postcallmd_draft.py will not write a draft:"
        )
        for line in blocking:
            print(f"  {line}")
        print(
            "Tell the admin about each line. A must_on or must_off line needs a change in the app. "
            "A weekend_block, next_day_off or post_call_ok line clears when config/my_rules.json lists "
            'its token in "enforces". The stock solver.py keeps all three. Do not write code for them.'
        )
    print()
    print("Next:")
    print(f"  {launcher()} scripts/check_my_data.py --config {display_path(root, config_path)}")
    print(f"  {launcher()} scripts/run_my_schedule.py --config {display_path(root, config_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

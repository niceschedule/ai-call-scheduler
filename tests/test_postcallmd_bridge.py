"""Tests for the two PostCallMD bridge scripts.

scripts/from_postcallmd.py turns the saved export_solver_inputs JSON into
the solver's input files. scripts/to_postcallmd_draft.py turns the solver
output into the submit_schedule_draft arguments. The fixtures are
data/sample with app UUIDs and the app's shift codes ("OR Call"), plus a
get_practice_overview result with shift-type ids.

    .venv/bin/python -m pytest tests/
    python tests/test_postcallmd_bridge.py   (stdlib only, as in CI)
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
EXPORT = FIXTURES / "postcallmd_export.json"
OVERVIEW = FIXTURES / "postcallmd_overview.json"
FROM_SCRIPT = REPO / "scripts" / "from_postcallmd.py"
DRAFT_SCRIPT = REPO / "scripts" / "to_postcallmd_draft.py"
EXPORT_FILES = ("clinicians.csv", "coverage.csv", "requests.csv", "history.csv", "shift_types.csv", "notes.txt")


def run(*args: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *map(str, args)], cwd=REPO, capture_output=True, text=True
    )


def check(result: subprocess.CompletedProcess, code: int = 0) -> None:
    assert result.returncode == code, (
        f"expected exit {code}, got {result.returncode}.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def import_export(root: Path) -> tuple[Path, Path, subprocess.CompletedProcess]:
    """Run from_postcallmd.py into a temp root. Returns data dir, config, result."""
    data_dir = root / "data" / "my_data"
    config = root / "config" / "my_rules.json"
    result = run(FROM_SCRIPT, EXPORT, "--data-dir", data_dir, "--config", config)
    return data_dir, config, result


def test_from_postcallmd_writes_every_file_and_the_config():
    export = json.loads(EXPORT.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir, config, result = import_export(root)
        check(result)
        for name in EXPORT_FILES:
            assert (data_dir / name).exists(), f"{name} was not written"
        # Nothing dropped: every export row is in the file.
        assert len(rows(data_dir / "clinicians.csv")) == export["clinicians_csv"].count("\n") - 1
        assert len(rows(data_dir / "coverage.csv")) == export["coverage_csv"].count("\n") - 1
        assert (data_dir / "notes.txt").read_text() == export["notes_txt"]
        # The notes and the unenforced shift-type flags are printed.
        assert "Notes from the export" in result.stdout
        assert "prefer_on" in result.stdout
        assert "OB Call: next_day_off, weekend_block, post_call_ok=0" in result.stdout
        # Config from the template, pointed at the data and a dated output.
        assert config.exists()
        cfg = json.loads(config.read_text())
        assert cfg["input_dir"] == "data/my_data"
        assert cfg["output_csv"] == "output/schedule_2026-06.csv"
        assert cfg["rules"]["weights"]["target_deviation"] == 100


def test_from_postcallmd_keeps_an_existing_config():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "config" / "my_rules.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"input_dir": "data/my_data", "output_csv": "output/x.csv", "rules": {}}))
        _, _, result = import_export(root)
        check(result)
        assert "Kept the existing" in result.stdout
        assert json.loads(config.read_text())["output_csv"] == "output/x.csv"


def test_from_postcallmd_fails_on_a_missing_key():
    export = json.loads(EXPORT.read_text())
    del export["shift_types_csv"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        partial = root / "export.json"
        partial.write_text(json.dumps(export))
        result = run(FROM_SCRIPT, partial, "--data-dir", root / "d", "--config", root / "c" / "r.json")
        check(result, 2)
        assert "missing shift_types_csv" in result.stderr
        assert not (root / "d").exists(), "nothing may be written on a bad export"


def test_from_postcallmd_fails_on_a_missing_column():
    export = json.loads(EXPORT.read_text())
    export["clinicians_csv"] = export["clinicians_csv"].replace("max_shifts,", "cap,", 1)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        broken = root / "export.json"
        broken.write_text(json.dumps(export))
        result = run(FROM_SCRIPT, broken, "--data-dir", root / "d", "--config", root / "c" / "r.json")
        check(result, 2)
        assert "clinicians.csv lacks columns: max_shifts" in result.stderr


def test_from_postcallmd_unwraps_a_client_wrapper():
    export = json.loads(EXPORT.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wrapped = root / "export.json"
        wrapped.write_text(json.dumps({"structuredContent": export}))
        result = run(FROM_SCRIPT, wrapped, "--data-dir", root / "d", "--config", root / "c" / "r.json")
        check(result)
        assert (root / "d" / "coverage.csv").exists()


def test_export_solves_optimal_and_round_trips_to_a_draft():
    """The whole app route: import, data check, solve, draft."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir, config, result = import_export(root)
        check(result)

        check(run(REPO / "scripts" / "check_my_data.py", "--config", config))

        solve = run(REPO / "solver.py", "--config", config)
        check(solve)
        assert "Status: OPTIMAL" in solve.stdout, solve.stdout
        schedule = root / "output" / "schedule_2026-06.csv"
        assert schedule.exists()
        schedule_rows = rows(schedule)
        assert len(schedule_rows) == len(rows(data_dir / "coverage.csv"))
        assert {r["shift_type"] for r in schedule_rows} == {"OR CALL", "OB CALL"}

        draft_path = root / "output" / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", draft_path)
        check(draft_run)
        draft = json.loads(draft_path.read_text())
        assert draft["mode"] == "fill_empty"
        assert draft["start_date"] == "2026-06-01"
        assert draft["end_date"] == max(r["date"] for r in schedule_rows)
        assert len(draft["assignments"]) == len(schedule_rows)

        overview = json.loads(OVERVIEW.read_text())
        ids = {s["code"]: s["id"] for s in overview["shift_types"]}
        expected = {
            (r["date"], r["clinician_id"], ids["OR Call" if r["shift_type"] == "OR CALL" else "OB Call"])
            for r in schedule_rows
        }
        got = {(a["date"], a["clinician_id"], a["shift_type_id"]) for a in draft["assignments"]}
        assert got == expected
        for a in draft["assignments"]:
            uuid.UUID(a["clinician_id"])
            uuid.UUID(a["shift_type_id"])
        assert "OB Call" in draft_run.stdout and "Assignments per clinician" in draft_run.stdout


def test_to_postcallmd_draft_fails_on_a_code_with_no_id():
    overview = json.loads(OVERVIEW.read_text())
    overview["shift_types"] = [s for s in overview["shift_types"] if s["code"] != "OB Call"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir, config, result = import_export(root)
        check(result)
        check(run(REPO / "solver.py", "--config", config))
        short = root / "overview.json"
        short.write_text(json.dumps(overview))
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", short, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "has the code 'OB CALL'" in draft_run.stderr
        assert "Known codes: AM, OR Call" in draft_run.stderr
        assert not out.exists(), "a draft with a missing id must not be written"


def test_to_postcallmd_draft_rejects_slug_ids():
    """The built-in sample has slug ids, not UUIDs, so it cannot be sent to the app."""
    sample = REPO / "output" / "sample_schedule.csv"
    if not sample.exists():
        check(run(REPO / "solver.py"))
    overview = json.loads(OVERVIEW.read_text())
    for s in overview["shift_types"]:
        s["code"] = s["code"].replace(" Call", "")  # OR, OB, so the codes match
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ov = root / "overview.json"
        ov.write_text(json.dumps(overview))
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", ov, "--schedule", sample, "--out", out)
        check(draft_run, 2)
        assert "not a UUID" in draft_run.stderr
        assert not out.exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok - {name}")

"""Tests for the two PostCallMD bridge scripts.

scripts/from_postcallmd.py turns the saved export_solver_inputs JSON into
the solver's input files. scripts/to_postcallmd_draft.py turns the solver
output into the submit_schedule_draft arguments. The fixtures are
data/sample with app UUIDs and the app's shift codes ("OR Call"), plus a
get_practice_overview result with shift-type ids.

    python3 scripts/py --dev -m pytest tests/
    python tests/test_postcallmd_bridge.py   (stdlib only, as in CI)
"""

from __future__ import annotations

import csv
import json
import shutil
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


def unblock(data_dir: Path) -> None:
    """Make notes.txt look like an export with nothing to block, so a draft
    test reaches the check it is about."""
    notes = data_dir / "notes.txt"
    lines = notes.read_text().splitlines()
    kept = [line for line in lines if not line.startswith("BLOCKING:")]
    notes.write_text("\n".join("blocking: none" if line == "blocking:" else line for line in kept) + "\n")


def ruleless_solver(root: Path) -> Path:
    """A solver file without the shift-type rules, as an old or edited
    solver.py would be. The draft gate reads only its text."""
    path = root / "old_solver.py"
    path.write_text("# a solver.py without weekend_block, next_day_off or post_call_ok\n", encoding="utf-8")
    return path


def set_enforces(config: Path, tokens: list[str]) -> None:
    cfg = json.loads(config.read_text())
    cfg["enforces"] = tokens
    config.write_text(json.dumps(cfg))


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
        # The template lists the three rules in enforces, and solver.py keeps them.
        assert "STOP" not in result.stdout
        assert "BLOCKING lines that solver.py keeps" in result.stdout
        # Config from the template, pointed at the data and a dated output.
        assert config.exists()
        cfg = json.loads(config.read_text())
        assert cfg["input_dir"] == "data/my_data"
        assert cfg["output_csv"] == "output/schedule_2026-06.csv"
        assert cfg["rules"]["weights"]["target_deviation"] == 100


def test_from_postcallmd_keeps_an_existing_config_but_sets_the_month():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "config" / "my_rules.json"
        config.parent.mkdir()
        rules = {"weights": {"target_deviation": 7}}
        config.write_text(json.dumps({"input_dir": "data/my_data", "output_csv": "output/x.csv", "rules": rules}))
        _, _, result = import_export(root)
        check(result)
        assert f"Updated {config} for month 2026-06" in result.stdout
        assert "output_csv was output/x.csv" in result.stdout
        cfg = json.loads(config.read_text())
        assert cfg["output_csv"] == "output/schedule_2026-06.csv"
        assert cfg["rules"] == rules, "the other keys of an existing config are kept"


def write_export(root: Path, export: dict) -> Path:
    path = root / "export.json"
    path.write_text(json.dumps(export))
    return path


def test_from_postcallmd_names_the_output_after_the_month_on_every_run():
    export = json.loads(EXPORT.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir, config, result = import_export(root)
        check(result)
        assert json.loads(config.read_text())["output_csv"] == "output/schedule_2026-06.csv"

        july = dict(export, coverage_csv=export["coverage_csv"].replace("2026-06-", "2026-07-"))
        rerun = run(FROM_SCRIPT, write_export(root, july), "--data-dir", data_dir, "--config", config)
        check(rerun)
        assert json.loads(config.read_text())["output_csv"] == "output/schedule_2026-07.csv"
        assert "output_csv was output/schedule_2026-06.csv" in rerun.stdout


def test_from_postcallmd_stops_on_more_than_one_month():
    export = json.loads(EXPORT.read_text())
    export["coverage_csv"] = export["coverage_csv"].replace("2026-06-14,OB Call", "2026-07-14,OB Call")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run(FROM_SCRIPT, write_export(root, export), "--data-dir", root / "d", "--config", root / "c" / "r.json")
        check(result, 2)
        assert "spans more than one month (2026-06, 2026-07)" in result.stderr
        assert not (root / "d").exists()


def test_from_postcallmd_stops_on_an_input_dir_mismatch_unless_forced():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "config" / "my_rules.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"input_dir": "data/other", "output_csv": "output/x.csv", "rules": {}}))
        data_dir, _, result = import_export(root)
        check(result, 2)
        assert f"reads {root / 'data' / 'other'}, not {data_dir}." in result.stderr
        assert "Nothing was written" in result.stderr
        assert not data_dir.exists(), "nothing may be written on a mismatch"
        assert json.loads(config.read_text())["input_dir"] == "data/other"

        forced = run(FROM_SCRIPT, EXPORT, "--data-dir", data_dir, "--config", config, "--force")
        check(forced)
        assert (data_dir / "coverage.csv").exists()
        assert json.loads(config.read_text())["input_dir"] == "data/my_data"
        assert "input_dir now points at data/my_data (--force)" in forced.stdout


def test_from_postcallmd_writes_blocking_notes_through_and_flags_them():
    export = json.loads(EXPORT.read_text())
    export["notes_txt"] += (
        "\nblocking\n"
        "BLOCKING: OB Call has weekend_block, and the stored solver does not keep it.\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir = root / "data" / "my_data"
        result = run(FROM_SCRIPT, write_export(root, export), "--data-dir", data_dir, "--config", root / "config" / "r.json")
        check(result)
        assert (data_dir / "notes.txt").read_text() == export["notes_txt"]
        # The five shift-type lines clear. This line names no rule token, so it stays.
        assert "STOP: the export has 1 BLOCKING line(s)" in result.stdout
        assert "  BLOCKING: OB Call has weekend_block" in result.stdout


def test_an_export_without_settings_json_keeps_the_config_default():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, config, result = import_export(Path(tmp))
        check(result)
        assert not (data_dir / "settings.json").exists()
        assert "Practice settings" not in result.stdout
        assert json.loads(config.read_text())["rules"]["avoid_b2b_weekends"] is True


def test_settings_json_sets_avoid_b2b_weekends_in_the_config():
    export = json.loads(EXPORT.read_text())
    export["settings_json"] = json.dumps({"avoid_b2b_weekends": False, "later_key": 1})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir = root / "data" / "my_data"
        config = root / "config" / "my_rules.json"
        config.parent.mkdir()
        # An existing config that says true: the practice setting wins.
        config.write_text(json.dumps({
            "input_dir": "data/my_data", "rules": {"avoid_b2b_weekends": True, "weights": {"b2b_weekend": 40}},
        }))
        result = run(FROM_SCRIPT, write_export(root, export), "--data-dir", data_dir, "--config", config)
        check(result)
        cfg = json.loads(config.read_text())
        assert cfg["rules"]["avoid_b2b_weekends"] is False
        assert cfg["rules"]["weights"] == {"b2b_weekend": 40}, "other rules are kept"
        assert json.loads((data_dir / "settings.json").read_text())["avoid_b2b_weekends"] is False
        assert "avoid_b2b_weekends = false" in result.stdout
        assert "changed avoid_b2b_weekends: true -> false" in result.stdout
        assert "Settings in settings_json that this script does not use: later_key" in result.stdout
        assert "Keys in the export that this script does not use" not in result.stdout


def test_a_bad_settings_json_writes_nothing():
    export = json.loads(EXPORT.read_text())
    for bad in ('{"avoid_b2b_weekends": "no"}', "[1]", "{not json"):
        export["settings_json"] = bad
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run(FROM_SCRIPT, write_export(root, export), "--data-dir", root / "d", "--config", root / "c" / "r.json")
            check(result, 2)
            assert "could not read settings_json" in result.stderr
            assert not (root / "d").exists()


def test_allow_b2b_weekends_column_passes_through_and_is_named():
    export = json.loads(EXPORT.read_text())
    lines = export["clinicians_csv"].splitlines()
    lines[0] += ",allow_b2b_weekends"
    lines[1:] = [line + ("," + ("1" if i == 0 else "0")) for i, line in enumerate(lines[1:])]
    export["clinicians_csv"] = "\n".join(lines) + "\n"
    first = lines[1].split(",")[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir = root / "data" / "my_data"
        config = root / "config" / "my_rules.json"
        result = run(FROM_SCRIPT, write_export(root, export), "--data-dir", data_dir, "--config", config)
        check(result)
        people = rows(data_dir / "clinicians.csv")
        assert [r["allow_b2b_weekends"] for r in people][:2] == ["1", "0"]
        assert f"b2b_weekend penalty: {first}" in result.stdout
        check(run(REPO / "scripts" / "check_my_data.py", "--config", config))


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
    """The whole app route: import, data check, solve, draft. The fixture's
    shift types carry all three flags. The stock solver keeps them, and the
    template lists them in enforces, so the draft gets through with no code
    change. Without enforces, or with a solver that lacks a rule, it stops."""
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

        check(run(REPO / "scripts" / "check_schedule.py", "--config", config))

        # Without the tokens in enforces, the gate holds.
        draft_path = root / "output" / "draft.json"
        set_enforces(config, [])
        blocked = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", draft_path)
        check(blocked, 2)
        assert "5 BLOCKING line(s)" in blocked.stderr
        assert not draft_path.exists()

        # enforces alone clears nothing: a solver that lacks the rules stays blocked.
        set_enforces(config, ["weekend_block", "next_day_off", "post_call_ok"])
        claimed = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config,
                      "--solver", ruleless_solver(root), "--out", draft_path)
        check(claimed, 2)
        assert "weekend_block is in enforces, but solver.py lacks" in claimed.stderr
        assert not draft_path.exists()

        # The stock solver keeps all three, so the lines clear.
        solver = REPO / "solver.py"
        schedule_rows = rows(schedule)
        assert len(schedule_rows) == len(rows(data_dir / "coverage.csv"))
        assert {r["shift_type"] for r in schedule_rows} == {"OR CALL", "OB CALL"}

        draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--solver", solver, "--out", draft_path)
        check(draft_run)
        assert "BLOCKING lines cleared" in draft_run.stdout
        assert draft_run.stdout.count("  BLOCKING: ") == 5

        # Next month's import makes the same check, so it says no STOP.
        again = run(FROM_SCRIPT, EXPORT, "--data-dir", data_dir, "--config", config, "--solver", solver)
        check(again)
        assert "STOP" not in again.stdout
        assert "BLOCKING lines that solver.py keeps" in again.stdout
        draft = json.loads(draft_path.read_text())
        assert draft["mode"] == "fill_empty"
        # Calendar bounds of the month, not the first and last assignment.
        assert draft["start_date"] == "2026-06-01"
        assert draft["end_date"] == "2026-06-30"
        assert max(r["date"] for r in schedule_rows) < draft["end_date"]
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

        # A rerun for the same month writes the same file, byte for byte.
        first = draft_path.read_bytes()
        check(run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--solver", solver, "--out", draft_path))
        assert draft_path.read_bytes() == first


def test_enforces_clears_only_the_listed_rules_and_never_a_request():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir, config, result = import_export(root)
        check(result)
        solver = REPO / "solver.py"
        check(run(solver, "--config", config))
        notes = data_dir / "notes.txt"
        notes.write_text(
            notes.read_text()
            + "BLOCKING: must_on A. Fox 2026-06-20: A. Fox must work on 2026-06-20, and no file carries it.\n"
        )
        set_enforces(config, ["weekend_block", "must_on"])
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--solver", solver, "--out", out)
        check(draft_run, 2)
        # weekend_block clears. next_day_off and post_call_ok (4 lines) and must_on stay.
        assert "has 5 BLOCKING line(s)" in draft_run.stderr
        assert "BLOCKING: weekend_block" not in draft_run.stderr
        assert "BLOCKING: must_on A. Fox" in draft_run.stderr
        assert "a must_on line cannot be cleared" in draft_run.stderr
        assert not out.exists()


def solved_root(tmp: str) -> tuple[Path, Path, Path]:
    """Import the fixture export and solve it. Returns root, data dir, config."""
    root = Path(tmp)
    data_dir, config, result = import_export(root)
    check(result)
    unblock(data_dir)
    check(run(REPO / "solver.py", "--config", config))
    return root, data_dir, config


def test_to_postcallmd_draft_refuses_blocking_notes():
    with tempfile.TemporaryDirectory() as tmp:
        root, data_dir, config = solved_root(tmp)
        notes = data_dir / "notes.txt"
        notes.write_text(
            notes.read_text()
            + "\nblocking\n"
            + "BLOCKING: must_on request for A. Fox on 2026-06-20 has no solver form.\n"
            + "  BLOCKING: OB Call has next_day_off, and solver.py does not keep it.\n"
        )
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "2 BLOCKING line(s)" in draft_run.stderr
        assert "BLOCKING: must_on request for A. Fox" in draft_run.stderr
        assert "BLOCKING: OB Call has next_day_off" in draft_run.stderr
        assert "nothing was written" in draft_run.stderr
        assert not out.exists(), "a blocked draft must not be written"


def test_to_postcallmd_draft_needs_notes():
    with tempfile.TemporaryDirectory() as tmp:
        root, data_dir, config = solved_root(tmp)
        (data_dir / "notes.txt").unlink()
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "no notes.txt" in draft_run.stderr
        assert not out.exists()


def test_to_postcallmd_draft_rejects_a_clinician_not_on_the_roster():
    with tempfile.TemporaryDirectory() as tmp:
        root, data_dir, config = solved_root(tmp)
        roster = (data_dir / "clinicians.csv").read_text()
        reed = "0b1f4f1e-6a7c-4a3b-9c1d-2e3f4a5b6c06"
        (data_dir / "clinicians.csv").write_text(roster.replace(reed, "0b1f4f1e-6a7c-4a3b-9c1d-2e3f4a5b6cff"))
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "is not an active clinician in" in draft_run.stderr
        assert reed in draft_run.stderr and "S. Reed" in draft_run.stderr
        assert not out.exists()


def test_to_postcallmd_draft_month_flag():
    with tempfile.TemporaryDirectory() as tmp:
        root, _, config = solved_root(tmp)
        out = root / "draft.json"
        same = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out, "--month", "2026-06")
        check(same)
        draft = json.loads(out.read_text())
        assert (draft["start_date"], draft["end_date"]) == ("2026-06-01", "2026-06-30")
        out.unlink()

        wrong = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out, "--month", "2026-07")
        check(wrong, 2)
        assert "dates outside the month 2026-07" in wrong.stderr
        assert not out.exists()

        bad = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out, "--month", "June")
        check(bad, 2)
        assert "--month must be YYYY-MM" in bad.stderr


def test_to_postcallmd_draft_names_an_inactive_shift_type():
    overview = json.loads(OVERVIEW.read_text())
    for s in overview["shift_types"]:
        if s["code"] == "OB Call":
            s["active"] = False
    with tempfile.TemporaryDirectory() as tmp:
        root, _, config = solved_root(tmp)
        ov = root / "overview.json"
        ov.write_text(json.dumps(overview))
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", ov, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "'OB CALL' is inactive in the app" in draft_run.stderr
        assert not out.exists()


def test_to_postcallmd_draft_rejects_a_shift_type_id_that_is_not_a_uuid():
    overview = json.loads(OVERVIEW.read_text())
    overview["shift_types"][0]["id"] = "or-call"
    with tempfile.TemporaryDirectory() as tmp:
        root, _, config = solved_root(tmp)
        ov = root / "overview.json"
        ov.write_text(json.dumps(overview))
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", ov, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "'or-call', which is not a UUID" in draft_run.stderr
        assert not out.exists()


def test_to_postcallmd_draft_fails_on_a_code_with_no_id():
    overview = json.loads(OVERVIEW.read_text())
    overview["shift_types"] = [s for s in overview["shift_types"] if s["code"] != "OB Call"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir, config, result = import_export(root)
        check(result)
        unblock(data_dir)
        check(run(REPO / "solver.py", "--config", config))
        short = root / "overview.json"
        short.write_text(json.dumps(overview))
        out = root / "draft.json"
        draft_run = run(DRAFT_SCRIPT, "--overview", short, "--config", config, "--out", out)
        check(draft_run, 2)
        assert "the code 'OB CALL' has no active shift type" in draft_run.stderr
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
        data_dir, _, result = import_export(root)
        check(result)
        unblock(data_dir)
        ov = root / "overview.json"
        ov.write_text(json.dumps(overview))
        out = root / "draft.json"
        draft_run = run(
            DRAFT_SCRIPT, "--overview", ov, "--schedule", sample, "--data-dir", data_dir, "--out", out
        )
        check(draft_run, 2)
        assert "not a UUID" in draft_run.stderr
        assert not out.exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok - {name}")

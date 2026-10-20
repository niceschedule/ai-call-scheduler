"""The bridge on a larger export than the sample.

tests/fixtures/matrix_export.json is a made-up practice in the shape of a
PostCallMD export for August 2026:

* 12 clinicians. P. Lund takes no call (target_shifts and max_shifts 0).
* 3 call shift types in the files. matrix_overview.json lists 10 shift
  types: those 3 and 7 daily ones, which a call-scope export leaves out.
* requests.csv and history.csv have a header and no rows.
* Saturday the 1st and Monday the 31st have no coverage, so the first and
  last assignment are not the first and last day of the month.

A variant adds history for a clinician who is not on the roster.
tests/make_matrix_fixture.py wrote both files.

    python3 scripts/py --dev -m pytest tests/
    python3 scripts/py tests/test_fixture_matrix.py   (stdlib only)
"""

from __future__ import annotations

import atexit
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
EXPORT = FIXTURES / "matrix_export.json"
OVERVIEW = FIXTURES / "matrix_overview.json"
FROM_SCRIPT = REPO / "scripts" / "from_postcallmd.py"
DRAFT_SCRIPT = REPO / "scripts" / "to_postcallmd_draft.py"
ZERO_CALL = "P. Lund"


def run(*args: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *map(str, args)], cwd=REPO, capture_output=True, text=True)


def check(result: subprocess.CompletedProcess, code: int = 0) -> None:
    assert result.returncode == code, (
        f"expected exit {code}, got {result.returncode}.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def new_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="matrix-"))
    atexit.register(shutil.rmtree, root, True)
    return root


@lru_cache(maxsize=None)
def solved() -> tuple[Path, subprocess.CompletedProcess, subprocess.CompletedProcess]:
    """Import, check and solve the matrix export once. Returns the root, the
    import result and the solve result."""
    root = new_root()
    imported = run(FROM_SCRIPT, EXPORT, "--data-dir", root / "data" / "my_data", "--config", root / "config" / "my_rules.json")
    check(imported)
    check(run(REPO / "scripts" / "check_my_data.py", "--config", root / "config" / "my_rules.json"))
    solve = run(REPO / "solver.py", "--config", root / "config" / "my_rules.json")
    check(solve)
    return root, imported, solve


def roster() -> dict[str, str]:
    root, _, _ = solved()
    return {r["name"]: r["clinician_id"] for r in rows(root / "data" / "my_data" / "clinicians.csv")}


def test_the_fixture_has_the_matrix_shape():
    export = json.loads(EXPORT.read_text())
    overview = json.loads(OVERVIEW.read_text())
    assert export["clinicians_csv"].count("\n") - 1 == 12
    categories = [s["category"] for s in overview["shift_types"]]
    assert len(categories) == 10 and categories.count("daily") == 7
    export_codes = {line.split(",")[0] for line in export["shift_types_csv"].splitlines()[1:]}
    daily_codes = {s["code"] for s in overview["shift_types"] if s["category"] == "daily"}
    assert export_codes and not export_codes & daily_codes, "daily shift types must be absent"
    assert export["requests_csv"].count("\n") == 1 and export["history_csv"].count("\n") == 1


def test_import_handles_empty_requests_and_history():
    root, imported, _ = solved()
    data = root / "data" / "my_data"
    assert rows(data / "requests.csv") == [] and rows(data / "history.csv") == []
    assert not (data / "history_unmatched.csv").exists()
    assert "requests.csv        0 rows" in imported.stdout
    assert "does not enforce" not in imported.stdout, "no flags are set, so no flag lines"
    cfg = json.loads((root / "config" / "my_rules.json").read_text())
    assert cfg["output_csv"] == "output/schedule_2026-08.csv"


def test_solve_fills_every_row_and_leaves_the_zero_call_clinician_out():
    root, _, solve = solved()
    assert "Status: OPTIMAL" in solve.stdout or "Status: FEASIBLE" in solve.stdout, solve.stdout
    schedule = rows(root / "output" / "schedule_2026-08.csv")
    coverage = rows(root / "data" / "my_data" / "coverage.csv")
    assert len(schedule) == sum(int(r["required_count"]) for r in coverage) == 67
    assert roster()[ZERO_CALL] not in {r["clinician_id"] for r in schedule}
    dates = sorted(r["date"] for r in schedule)
    assert (dates[0], dates[-1]) == ("2026-08-02", "2026-08-30")


def test_draft_uses_month_bounds_and_only_call_ids():
    root, _, _ = solved()
    out = root / "output" / "draft.json"
    config = root / "config" / "my_rules.json"
    draft_run = run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out)
    check(draft_run)
    draft = json.loads(out.read_text())
    assert (draft["start_date"], draft["end_date"]) == ("2026-08-01", "2026-08-31")
    assert len(draft["assignments"]) == 67

    overview = json.loads(OVERVIEW.read_text())
    call_ids = {s["id"] for s in overview["shift_types"] if s["category"] == "call"}
    assert {a["shift_type_id"] for a in draft["assignments"]} == call_ids
    on_roster = set(roster().values())
    for a in draft["assignments"]:
        assert a["clinician_id"] in on_roster
        uuid.UUID(a["shift_type_id"])
    assert roster()[ZERO_CALL] not in {a["clinician_id"] for a in draft["assignments"]}

    first = out.read_bytes()
    check(run(DRAFT_SCRIPT, "--overview", OVERVIEW, "--config", config, "--out", out, "--month", "2026-08"))
    assert out.read_bytes() == first, "a rerun for the same month must write the same draft"


def test_history_for_a_clinician_not_on_the_roster_is_moved_aside():
    export = json.loads(EXPORT.read_text())
    abbott = next(line.split(",")[0] for line in export["clinicians_csv"].splitlines() if ",B. Abbott," in line)
    departed = "6f0c1d2e-dead-4000-8000-00000000beef"
    export["history_csv"] += (
        f"2026-07-03,{abbott},OR Call,final\n"
        f"2026-07-04,{departed},OR Call,final\n"
        f"2026-07-11,{departed},Night Call,final\n"
    )
    root = new_root()
    path = root / "export.json"
    path.write_text(json.dumps(export))
    data = root / "data" / "my_data"
    config = root / "config" / "my_rules.json"
    imported = run(FROM_SCRIPT, path, "--data-dir", data, "--config", config)
    check(imported)
    assert [r["clinician_id"] for r in rows(data / "history.csv")] == [abbott]
    assert [r["clinician_id"] for r in rows(data / "history_unmatched.csv")] == [departed, departed]
    assert f"  {departed}: 2 row(s)" in imported.stdout
    check(run(REPO / "scripts" / "check_my_data.py", "--config", config))

    # The next month has no such rows, so the old file goes.
    check(run(FROM_SCRIPT, EXPORT, "--data-dir", data, "--config", config))
    assert not (data / "history_unmatched.csv").exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok - {name}")

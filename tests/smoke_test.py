"""Smoke test for the sample solve.

Runs solver.py against the committed sample data and checks that it finds an
OPTIMAL schedule and writes the output files. Stdlib only, so CI needs no test
framework:

    python tests/smoke_test.py

The assertions are pytest-compatible too. requirements-dev.txt has pytest,
and the launcher installs it:

    python3 scripts/py --dev -m pytest tests/
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUTPUTS = (
    REPO / "output" / "sample_schedule.csv",
    REPO / "output" / "sample_schedule.html",
)


def test_sample_solve_is_optimal_and_writes_output():
    result = subprocess.run(
        [sys.executable, "solver.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"solver.py exited with {result.returncode}.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # The solver stops after 60 seconds. A slow machine (the Windows CI
    # runner) can find the schedule but run out of time to prove it is the
    # best one, and then it reports FEASIBLE. CI sets SMOKE_ACCEPT_FEASIBLE
    # there. Everywhere else the sample must report OPTIMAL.
    accepted = ["Status: OPTIMAL"]
    if os.environ.get("SMOKE_ACCEPT_FEASIBLE") == "1":
        accepted.append("Status: FEASIBLE")
    assert any(status in result.stdout for status in accepted), (
        f"expected the sample solve to report OPTIMAL.\n--- stdout ---\n{result.stdout}"
    )
    for path in OUTPUTS:
        assert path.exists(), f"solver.py did not write {path.relative_to(REPO)}"
        assert path.stat().st_size > 0, f"{path.relative_to(REPO)} is empty"


def test_min_rest_is_enforced_beyond_preferred_gap():
    """Regression: a per-clinician min_days_between_assignments LARGER than the
    global preferred gap must still be enforced. The pair scan used to stop at
    the preferred gap, silently dropping the hard rest constraint for wider
    pairs. Here 'solo' is the only eligible clinician and must cover two shifts
    4 days apart, but has a 5-day minimum rest — so the solve must be infeasible.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = root / "data"
        cfg_dir = root / "cfg"  # config_path.parent.parent must be `root`
        data.mkdir()
        cfg_dir.mkdir()

        (data / "clinicians.csv").write_text(
            "clinician_id,name,active,can_x,target_shifts,max_shifts,"
            "target_weekend_shifts,max_weekend_shifts,min_days_between_assignments\n"
            "solo,Solo Doc,1,1,2,9,0,9,5\n"
        )
        (data / "coverage.csv").write_text(
            "date,shift_type,required_count\n"
            "2026-06-01,X,1\n"
            "2026-06-05,X,1\n"
        )
        (data / "requests.csv").write_text(
            "request_id,clinician_id,start_date,end_date,request_type,hard,shift_type,note\n"
        )
        cfg = cfg_dir / "rules.json"
        cfg.write_text(json.dumps({
            "input_dir": "data",
            "output_csv": "out/schedule.csv",
            "rules": {
                "min_days_between_assignments_default": 1,
                "preferred_days_between_assignments": 3,
                "weights": {},
            },
        }))

        result = subprocess.run(
            [sys.executable, "solver.py", "--config", str(cfg)],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        # solve() returns 2 (and prints "No feasible schedule") when the rest
        # rule can't be satisfied. A returncode of 0 / "Status:" would mean the
        # 5-day minimum was ignored — the bug this guards against.
        assert result.returncode == 2 and "No feasible schedule" in result.stdout, (
            "min_days_between_assignments=5 was not enforced for a 4-day gap.\n"
            f"--- returncode ---\n{result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


if __name__ == "__main__":
    test_sample_solve_is_optimal_and_writes_output()
    print("ok - sample solve is OPTIMAL and wrote output/sample_schedule.{csv,html}")
    test_min_rest_is_enforced_beyond_preferred_gap()
    print("ok - minimum rest is enforced beyond the preferred gap")

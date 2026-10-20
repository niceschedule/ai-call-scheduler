"""Write tests/fixtures/matrix_export.json and matrix_overview.json.

The ids are uuid5 values, so a rerun writes the same files.

    python3 scripts/py tests/make_matrix_fixture.py
"""
import json
import uuid
from datetime import date
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

NS = uuid.UUID("6f0c1d2e-0000-4000-8000-000000000000")
def uid(kind, key): return str(uuid.uuid5(NS, f"{kind}:{key}"))

people = [  # name, fte, or, ob, night, target, max, wk_target, wk_max
    ("B. Abbott", "1.0", 1, 1, 1, 6, 8, 2, 4),
    ("D. Baker", "1.0", 1, 1, 1, 6, 8, 2, 4),
    ("E. Castro", "1.0", 1, 0, 1, 6, 8, 2, 4),
    ("F. Diaz", "1.0", 1, 1, 1, 6, 8, 2, 4),
    ("G. Evans", "1.0", 1, 1, 0, 6, 8, 2, 4),
    ("H. Ford", "0.8", 1, 1, 1, 5, 7, 1, 3),
    ("I. Grant", "1.0", 1, 0, 1, 6, 8, 2, 4),
    ("K. Hayes", "1.0", 1, 1, 1, 6, 8, 2, 4),
    ("L. Iqbal", "1.0", 1, 1, 1, 6, 8, 2, 4),
    ("M. Jones", "0.6", 1, 1, 1, 4, 6, 1, 3),
    ("N. Kim", "1.0", 1, 1, 1, 6, 8, 2, 4),
    ("P. Lund", "0.5", 0, 0, 0, 0, 0, 0, 0),  # daily only: zero call
]
call_types = [("OR Call", "#1d4ed8"), ("OB Call", "#be185d"), ("Night Call", "#7c3aed")]
daily_types = ["AM Room", "PM Room", "Pain Clinic", "Pre-op", "Endo", "Admin Day", "Cardiac"]

clin = ["clinician_id,name,active,fte,can_or_call,can_ob_call,can_night_call,target_shifts,max_shifts,target_weekend_shifts,max_weekend_shifts,min_days_between_assignments"]
for name, fte, o, b, n, t, m, wt, wm in people:
    clin.append(f"{uid('clinician', name)},{name},1,{fte},{o},{b},{n},{t},{m},{wt},{wm},1")

cov = ["date,shift_type,required_count"]
# August 2026: Saturday the 1st and Monday the 31st have no coverage.
for d in range(2, 31):
    day = date(2026, 8, d)
    cov.append(f"{day},OR Call,1")
    cov.append(f"{day},Night Call,1")
    if day.weekday() >= 5:
        cov.append(f"{day},OB Call,1")

st = ["code,name,category,post_call_ok,next_day_off,weekend_block,color"]
for code, color in call_types:
    st.append(f"{code},{code},call,1,0,0,{color}")

notes = "\n".join([
    "PostCallMD solver export for Matrix Test Practice, month 2026-08, scope call.",
    "Scope: call. Daily shift types are left out of every file. Daily shifts stay in Daily Staffing in the app.",
    "shift_types.csv lists each shift type in the files with its rules: post_call_ok, next_day_off, weekend_block and color. The open-source solver does not read this file. Read it before you adapt the solver.",
    "clinician_id is the PostCallMD clinician UUID. name is the display name.",
    "Request mapping: vacation -> vacation, hard=1. must_off -> no_call, hard=1. prefer_off -> prefer_off, hard=0. Only approved requests dated in the month are included.",
    "No requests in the month.",
    "No history before the month.",
    "P. Lund has no yearly call target, so target_shifts and max_shifts are 0.",
]) + "\n"

export = {
    "clinicians_csv": "\n".join(clin) + "\n",
    "coverage_csv": "\n".join(cov) + "\n",
    "requests_csv": "request_id,clinician_id,start_date,end_date,request_type,hard,shift_type,note\n",
    "history_csv": "date,clinician_id,shift_type,status\n",
    "shift_types_csv": "\n".join(st) + "\n",
    "notes_txt": notes,
}
site = uid("site", "main")
overview = {
    "sites": [{"id": site, "code": "MAIN", "name": "Main Hospital"}],
    "shift_types": [
        {"id": uid("shift", code), "code": code, "name": code, "category": "call", "facility_id": site,
         "active": True, "post_call_ok": True, "next_day_off": False, "weekend_block": False, "color": color}
        for code, color in call_types
    ] + [
        {"id": uid("shift", code), "code": code, "name": code, "category": "daily", "facility_id": site,
         "active": True, "post_call_ok": True, "next_day_off": False, "weekend_block": False, "color": "#0f766e"}
        for code in daily_types
    ],
    "periods": [], "request_types": [], "settings": {}, "stored_solvers": [],
}
assert len(overview["shift_types"]) == 10 and len(people) == 12
FIXTURES.joinpath("matrix_export.json").write_text(json.dumps(export, indent=2) + "\n")
FIXTURES.joinpath("matrix_overview.json").write_text(json.dumps(overview, indent=2) + "\n")

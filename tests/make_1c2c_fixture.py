"""Write tests/fixtures/onec_twoc_export.json and onec_twoc_overview.json.

A made-up normal anesthesia group in the shape of a PostCallMD export for
November 2026, with the two call shifts most groups have:

* 1C, first call, every day: weekend_block = 1, next_day_off = 1,
  post_call_ok = 0.
* 2C, second call, weekdays: post_call_ok = 0.

Ten clinicians. One may not take 2C. One works half time. The export's
caps follow the app's formula. History is October 2026, so Saturday
October 31 (1C, a weekend block) runs into Sunday November 1. Requests: a
vacation week, a must_off on a block Saturday, a prefer_off, and a locked
(imported) entry on a block Saturday. notes.txt carries the four BLOCKING
lines the app wrote before the solver kept these rules.

The ids are uuid5 values, so a rerun writes the same files.

    python3 scripts/py tests/make_1c2c_fixture.py
"""
import json
import math
import uuid
from datetime import date, timedelta
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NS = uuid.UUID("1c2c0000-0000-4000-8000-000000000000")


def uid(kind, key):
    return str(uuid.uuid5(NS, f"{kind}:{key}"))


people = [  # name, fte, can_1c, can_2c
    ("A. Adams", 1.0, 1, 1),
    ("B. Brooks", 1.0, 1, 1),
    ("C. Chen", 1.0, 1, 1),
    ("D. Dorsey", 1.0, 1, 1),
    ("E. Ellis", 1.0, 1, 0),  # no second call
    ("F. Flores", 1.0, 1, 1),
    ("G. Gupta", 1.0, 1, 1),
    ("H. Hale", 0.5, 1, 1),  # half time
    ("J. Ives", 1.0, 1, 1),
    ("K. Kerr", 1.0, 1, 1),
]
ids = {p[0]: uid("clinician", p[0]) for p in people}

days = [date(2026, 11, 1) + timedelta(days=i) for i in range(30)]
coverage = []
for d in days:
    coverage.append((d, "1C", 1))
    if d.weekday() < 5:
        coverage.append((d, "2C", 1))

# The export's targets and caps (solver_files.py): an fte share of the
# month's call demand, and max(target + 2, 1.5 x target) as the cap.
demand = len(coverage)
weekend_demand = sum(n for d, _, n in coverage if d.weekday() >= 5)
fte_sum = sum(p[1] for p in people)


def cap(target, floor):
    return max(target + floor, math.ceil(target * 1.5))


clin = ["clinician_id,name,active,fte,can_1c,can_2c,target_shifts,max_shifts,"
        "target_weekend_shifts,max_weekend_shifts,min_days_between_assignments"]
for name, fte, c1, c2 in people:
    t = round(demand * fte / fte_sum)
    wt = round(weekend_demand * fte / fte_sum)
    clin.append(f"{ids[name]},{name},1,{fte},{c1},{c2},{t},{cap(t, 2)},{wt},{cap(wt, 1)},")

cov = ["date,shift_type,required_count"] + [f"{d},{code},{n}" for d, code, n in coverage]

st = [
    "code,name,category,post_call_ok,next_day_off,weekend_block,color",
    "1C,First Call,call,0,1,1,#1d4ed8",
    "2C,Second Call,call,0,0,0,#be185d",
]

# October 2026 history: a plain rotation. Weekends are blocks. Saturday the
# 31st (1C) is G. Gupta, so Gupta keeps Sunday November 1.
names = [p[0] for p in people]
hist = ["date,clinician_id,shift_type,status"]
oct_days = [date(2026, 10, 1) + timedelta(days=i) for i in range(31)]
rotation = ["A. Adams", "B. Brooks", "C. Chen", "D. Dorsey", "F. Flores", "J. Ives", "K. Kerr"]
k = 0
saturday_holder = names[0]
for d in oct_days:
    if d.weekday() == 6:
        first = saturday_holder
    elif d == date(2026, 10, 31):
        first = "G. Gupta"
    else:
        first = names[k % len(names)]
        k += 1
    if d.weekday() == 5:
        saturday_holder = first
    hist.append(f"{d},{ids[first]},1C,final")
    if d.weekday() < 5:
        second = rotation[(oct_days.index(d) + 3) % len(rotation)]
        if second == first:
            second = rotation[(oct_days.index(d) + 4) % len(rotation)]
        hist.append(f"{d},{ids[second]},2C,final")

req = ["request_id,clinician_id,start_date,end_date,request_type,hard,shift_type,note"]
req.append(f"vacation-{ids['A. Adams'][:8]}-2026-11-09,{ids['A. Adams']},2026-11-09,2026-11-15,vacation,1,,Beach")
req.append(f"no_call-{ids['B. Brooks'][:8]}-2026-11-21,{ids['B. Brooks']},2026-11-21,2026-11-21,no_call,1,,Wedding")
req.append(f"prefer_off-{ids['C. Chen'][:8]}-2026-11-05,{ids['C. Chen']},2026-11-05,2026-11-05,prefer_off,0,,")
req.append(f"lock-1c2c0001,{ids['D. Dorsey']},2026-11-14,2026-11-14,lock,1,1C,")
req.append(f"lock-1c2c0002,{ids['F. Flores']},2026-11-19,2026-11-19,lock,1,2C,")

notes = "\n".join([
    "PostCallMD solver files",
    "Practice: One-Two Call Test Group",
    "Month: 2026-11",
    "Scope: call",
    "",
    "blocking:",
    "BLOCKING: next_day_off 1C: The day after a 1C shift is off. Submit only if the solver gives that clinician no assignment on the next day.",
    "BLOCKING: weekend_block 1C: One clinician works 1C on Saturday and on the Sunday after it. The stock solver keeps Saturday and Sunday apart, so it breaks this rule.",
    "BLOCKING: post_call_ok 1C: A clinician who was on call the day before may not work 1C. Submit only if the solver keeps that rule.",
    "BLOCKING: post_call_ok 2C: A clinician who was on call the day before may not work 2C. Submit only if the solver keeps that rule.",
    "Each BLOCKING line is a hard rule or request that a draft may break. Do not submit a draft until the solver enforces every one, or the admin changes the practice.",
    "",
    "What the files cannot express, and how each field was filled:",
    "- Shift types in the files: 1C (call), 2C (call).",
    "- Locked entries of the month become lock rows.",
]) + "\n"

export = {
    "clinicians_csv": "\n".join(clin) + "\n",
    "coverage_csv": "\n".join(cov) + "\n",
    "requests_csv": "\n".join(req) + "\n",
    "history_csv": "\n".join(hist) + "\n",
    "shift_types_csv": "\n".join(st) + "\n",
    "notes_txt": notes,
}
site = uid("site", "main")
overview = {
    "sites": [{"id": site, "code": "MAIN", "name": "Main Hospital"}],
    "shift_types": [
        {"id": uid("shift", "1C"), "code": "1C", "name": "First Call", "category": "call",
         "facility_id": None, "active": True, "post_call_ok": False, "next_day_off": True,
         "weekend_block": True, "color": "#1d4ed8"},
        {"id": uid("shift", "2C"), "code": "2C", "name": "Second Call", "category": "call",
         "facility_id": None, "active": True, "post_call_ok": False, "next_day_off": False,
         "weekend_block": False, "color": "#be185d"},
    ],
    "periods": [], "request_types": [], "settings": {}, "stored_solvers": [],
}
FIXTURES.joinpath("onec_twoc_export.json").write_text(json.dumps(export, indent=2) + "\n")
FIXTURES.joinpath("onec_twoc_overview.json").write_text(json.dumps(overview, indent=2) + "\n")

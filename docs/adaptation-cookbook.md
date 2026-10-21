# Adaptation Cookbook

Worked examples of changes a real group will want, in order of difficulty, plus a note on the three rules a PostCallMD export names in `shift_types.csv`, which the solver keeps with no code change. Each one shows:

1. What rows/columns to change in the CSVs.
2. What (if anything) to change in `solver.py`.
3. How to test it.

These are designed so an agent can read this file, pattern-match, and implement the change with you. Start from a working configured schedule in `data/my_data/` and `config/my_rules.json`, then change one thing and run `scripts/run_my_schedule.py` to compare the output.

The pattern beneath all three: **change one thing, re-run, verify.** Do not stack changes.

---

## 1. Add a third shift type (e.g. NIGHT)

The solver already handles arbitrary shift types — no code changes needed. You just need to update two CSVs.

### CSV changes

**`coverage.csv`** — add rows for the new shift on the dates it's needed:

```csv
date,shift_type,required_count
2026-06-01,NIGHT,1
2026-06-02,NIGHT,1
2026-06-03,NIGHT,1
...
```

**`clinicians.csv`** — add a new column `can_night`. Put `1` next to every clinician eligible for NIGHT, `0` for the rest:

```csv
clinician_id,name,active,can_or,can_ob,can_night,target_shifts,max_shifts,target_weekend_shifts,max_weekend_shifts,min_days_between_assignments
cary,M. Cary,1,1,1,1,5,6,2,3,1
fox,A. Fox,1,1,1,0,5,6,2,3,1
...
```

### Code changes

None. The solver discovers shift types from `coverage.csv` and resolves the matching `can_<shift>` column automatically — see the block that builds `shift_types` and verifies eligibility columns near the top of `solve()` in `solver.py`. The HTML renderer also picks a stable color for the new shift type automatically (see `SHIFT_PALETTE` and `shift_colors` in `schedule_html.py`).

### How to test

```bash
.venv/bin/python scripts/run_my_schedule.py
```

You should see a valid schedule and roughly `previous assignment count + N` assignments where `N` is the number of new NIGHT coverage rows. Open the configured HTML output and confirm:

- NIGHT shifts are filled.
- Only clinicians with `can_night=1` got NIGHT shifts.
- Total assignments per clinician are still balanced.

If you get `Missing eligibility column 'can_night' for shift type 'NIGHT'.` — you forgot the column. Add it.

If you get `No feasible schedule found.` — too few clinicians have `can_night=1` to cover the new demand, or it conflicts with `max_shifts`. Either raise `max_shifts`, add more eligible clinicians, or reduce NIGHT `required_count`.

---

## 2. Lock a clinician to a specific shift (already built in)

"Cary takes Friday June 5 OR no matter what." Maybe Cary asked for it, maybe it's a teaching obligation, maybe it's already published. No code change needed — locks are a request type.

### CSV changes

Add a row to `requests.csv` with `request_type=lock` and the shift named:

```csv
request_id,clinician_id,start_date,end_date,request_type,hard,shift_type,note
l1,cary,2026-06-05,2026-06-05,lock,1,OR,Teaching block
l2,fox,2026-06-12,2026-06-12,lock,1,OB,Already published
```

`shift_type` is required on locks; `hard` is ignored (locks are always hard). The date range can be a single day or a range — a range will lock that clinician onto every coverage row of `shift_type` in the window.

### Code changes

None.

### How to test

```bash
.venv/bin/python scripts/run_my_schedule.py
```

- The locked rows must appear exactly as specified in the HTML/CSV output.
- Total counts may shift slightly (the locked person now has one assignment "spent" in a specific slot).
- If you locked something the rules forbid (vacation overlap, ineligible shift, two locks for the same slot), the solver reports `No feasible schedule found.` — remove the conflicting lock and re-run.
- If the lock's date or shift_type doesn't match a row in `coverage.csv`, the solver raises `Lock request 'X' did not match any coverage row...` — fix the typo or add the coverage row.

---

## 3. Add a weekend-pairing rule (no Sat + Sun back to back)

"Don't make anyone work both Saturday and Sunday of the same weekend" is a fairness rule almost every group has. The dummy solver doesn't enforce it; the OPTIMAL output happens to spread it out, but for a tighter month the solver will sometimes stack both weekend days on one person.

### CSV changes

None. This is a pure code rule.

### Code changes

In `solver.py`, after the existing weekend objective block (search for `weekend_count` and the `SOFT PREFERENCE` for weekend deviation), add a hard constraint:

```python
# HARD RULE: no clinician works both Saturday and Sunday of the same weekend.
for clinician_id in clinician_ids:
    for day in all_dates:
        if day.weekday() != 5:  # 5 = Saturday
            continue
        sunday = day + timedelta(days=1)
        sat_var = day_assigned.get((day, clinician_id))
        sun_var = day_assigned.get((sunday, clinician_id))
        if sat_var is not None and sun_var is not None:
            model.Add(sat_var + sun_var <= 1)
```

`timedelta` is already imported at the top of `solver.py` — you don't need to re-import it.

This says: for every (Saturday, Sunday) pair in the solve window, no clinician can be on both. Note this uses `day_assigned`, which is the per-day "does this clinician work today" boolean already built by the solver.

The agent prompt: *"Add a hard rule that no clinician is assigned to both Saturday and Sunday of the same weekend. Use the existing day_assigned variables. Apply across all weekends in the solve window."*

### Soft version

If you want this as a preference instead of a hard rule, swap `model.Add(sat_var + sun_var <= 1)` for an objective penalty:

```python
both_weekend = model.NewBoolVar(f"both_we_{clinician_id}_{day}")
model.AddBoolAnd([sat_var, sun_var]).OnlyEnforceIf(both_weekend)
model.AddBoolOr([sat_var.Not(), sun_var.Not()]).OnlyEnforceIf(both_weekend.Not())
objective_terms.append(50 * both_weekend)  # weight = 50, tune as needed
```

### How to test

Re-run the configured schedule and check the HTML/CSV output: no clinician should have an assignment on both Saturday and Sunday of the same weekend.

If you can't find a feasible schedule, your group may not have enough weekend-eligible coverage to support the rule. Either soften it, raise `max_weekend_shifts`, or accept that some pairings have to happen during heavy months.

---

## 4. Extend the weekday-pattern penalty

The solver ships a soft preference that penalizes giving a clinician a weekday they've already covered a lot in `history.csv` — that's what pulls "Cary always gets Mondays" apart over time. It's the `weekday_repeat` weight (default `20` in `my_rules.template.json`, `0` in `sample_rules.json` so the article's sample output stays stable). Tuning notes for the weight live in `docs/csv-schema.md`; the implementation is `SOFT PREFERENCE 5` in `solver.py`.

If you want a related pattern the built-in term doesn't cover, copy `SOFT PREFERENCE 5` and re-key it. Variants worth doing:

- **Same shift_type on the same weekday** (Monday OR and Monday OB are different "slots" in some groups): key on `(clinician_id, shift_type, weekday)` instead of `(clinician_id, weekday)`. Useful when one clinician should be Monday-OR-only and another Monday-OB-only.
- **First weekend / second weekend / etc.**: bucket weekends in the solve window by ordinal position and key on `(clinician_id, weekend_ordinal)`. Catches "Cary takes the first weekend every month."
- **Same calendar day** (someone always covering the 1st): key on `(clinician_id, day_of_month)`.

The agent prompt: *"Read the SOFT PREFERENCE 5 block in solver.py, then add a copy that keys on `(clinician_id, shift_type, weekday)` instead of `(clinician_id, weekday)`. Use a separate config key like `shift_weekday_repeat` so it tunes independently. Extend load_history to return the new counter."*

---

## App rules from shift_types.csv (built in)

A PostCallMD export (see the app-route section of `AGENTS.md`) carries a fifth file, `shift_types.csv`, with one row per shift type and three rule flags. The stock `solver.py` reads the file and keeps all three as hard rules. **You do not write code for them.** A change of a flag in the app reaches the solver with the next export.

| Flag | Rule the solver keeps |
| --- | --- |
| `weekend_block` | `1`: one person works Saturday and the Sunday after it. |
| `next_day_off` | `1`: the day after the shift is off. |
| `post_call_ok` | `0`: a clinician who worked a `call` shift the day before may not work this shift. |

The code is in `solve()` in `solver.py`: search for `HARD RULE (shift_types.csv`. `docs/csv-schema.md`, section `shift_types.csv`, gives the exact behavior, including the weekend-block exception to the rest rule and the days just before the month. `scripts/check_schedule.py` checks the three rules, and every other hard rule, on the output.

`config/my_rules.template.json` lists the three tokens in `enforces`, so `scripts/to_postcallmd_draft.py` clears the export's `BLOCKING:` lines for them. It clears a line only when `solver.py` still has the code for the rule, so a solver that lost a rule stays blocked. A `must_on` or `must_off` line never clears this way: the export does not carry the request, so no solver can keep it.

To turn a rule off for one shift type, change the flag in the app and export again. Do not edit the rule out of `solver.py`. Use entry 3 (keep Saturday and Sunday apart) only for a shift type without `weekend_block`.

`tests/test_shift_rules.py` solves the sample with each flag, and a 1C/2C fixture practice with all of them, and checks the output with `scripts/check_schedule.py`.

## No call next to a vacation (built in)

The stock `solver.py` has two strong soft rules: no call on the day after a hard vacation (`avoid_call_after_vacation`) and no call on the day before one (`avoid_call_before_vacation`). Both are on by default. **You do not write code for them.** Turn one off with `false` under `rules` in the config. Change its weight (`call_after_vacation`, `call_before_vacation`, 500 each) to make it weaker or stronger. `docs/csv-schema.md` gives the exact behavior.

The rules are soft, so a month without enough people still solves. Each break is a line in the solver output and a `WARNING` line in `scripts/check_schedule.py`. Tell the admin about each one. `tests/test_vacation_rules.py` covers both rules, the switches, a forced break, a lock and the days outside the month.

---

## What to add next

The pattern is the same for almost any rule:

- **Holiday balancing:** add a `holidays.csv` (date list), count assignments on those dates per clinician, add an objective term that minimizes the spread.
- **Post-call recovery without a PostCallMD export:** write a `shift_types.csv` by hand, with `next_day_off` set to `1` on the shift type. The solver keeps it with no code change.
- **Partner vs non-partner:** add an `is_partner` column to `clinicians.csv`, then add an objective that pushes leftover call onto partners after non-partners hit their `target_shifts`.
- **Site-specific eligibility:** already supported. Just use distinct shift types (`NORTH`, `SOUTH`, `EAST`) and matching `can_*` columns.

For each one, the agent prompt is: *"Read solver.py, then add `<rule>`. Add it as a hard rule by default. Tell me what to change in the CSVs and how to test it. Run the configured schedule after."*

Add one rule at a time. Always re-run the configured schedule. Compare outputs before and after.

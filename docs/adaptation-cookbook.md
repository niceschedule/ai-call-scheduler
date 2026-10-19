# Adaptation Cookbook

Worked examples of changes a real group will want, in order of difficulty, plus the three rules a PostCallMD export names in `shift_types.csv`. Each one shows:

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

## App rules from shift_types.csv

A PostCallMD export (see the app-route section of `AGENTS.md`) carries a fifth file, `shift_types.csv`, with one row per shift type and three rule flags the solver does not enforce on its own:

| Flag | Meaning in the app |
| --- | --- |
| `next_day_off` | `1` means the day after this shift is off. |
| `weekend_block` | `1` means one person works Saturday and Sunday of this shift together. |
| `post_call_ok` | `0` means a clinician who was on call the day before may not work this shift. |

`scripts/from_postcallmd.py` prints which shift types carry which flag. The three entries below read the flag from the file, so a group without `shift_types.csv` is not affected, and a change of a flag in the app reaches the solver with the next export. Add one entry at a time. Each needs a plain-English description and a clear yes before you edit `solver.py`.

One fact to know first: with `min_days_between_assignments` of 1 or more, which is the template default, no clinician works two days in a row anyway. The first and third entries change the schedule only when a group sets the rest gap to 0, so that people may work back to back except where a flag forbids it. The second entry needs the rest rule to make one exception, and shows how.

### Shared helper: read the flags

Add this function to `solver.py` after `eligibility_column`:

```python
def load_shift_flags(path: Path) -> dict[str, dict]:
    """Read shift_types.csv from a PostCallMD export. Keyed by the upper-case
    code, the same key coverage rows use. Empty when the file is absent, so a
    group without the file solves as before."""
    if not path.exists():
        return {}
    flags: dict[str, dict] = {}
    for row in read_csv(path):
        code = row.get("code", "").strip().upper()
        if not code:
            continue
        flags[code] = {
            "category": row.get("category", "").strip().lower(),
            "post_call_ok": parse_bool(row.get("post_call_ok", "").strip() or "1"),
            "next_day_off": parse_bool(row.get("next_day_off", "").strip() or "0"),
            "weekend_block": parse_bool(row.get("weekend_block", "").strip() or "0"),
        }
    return flags
```

Then, in `solve()`, right after the line that loads history:

```python
    shift_flags = load_shift_flags(input_dir / "shift_types.csv")
```

The three entries below go into `solve()` after the block that builds `day_assigned` (search for `day_assigned[(day, clinician_id)] = var`) and before `HARD RULE 3`. `timedelta`, `date` and `defaultdict` are already imported.

---

## 5. Post-call day off (`next_day_off`)

"After OR call, the next day is off." The flag is `next_day_off` in `shift_types.csv`.

### CSV changes

None. The flag comes from the export. To let people work back to back on the shifts without the flag, set `min_days_between_assignments_default` to `0` in `config/my_rules.json`, or set the column to `0` for the people it applies to.

### Code changes

```python
    # HARD RULE: a shift with next_day_off gives the next day off.
    # The Saturday row of a weekend_block shift is skipped: the block itself
    # covers Sunday, and the day off follows the Sunday row.
    for cov_id, cov in enumerate(coverage):
        flags = shift_flags.get(cov.shift_type, {})
        if not flags.get("next_day_off"):
            continue
        if cov.date.weekday() == 5 and flags.get("weekend_block"):
            continue
        for clinician_id in clinician_ids:
            tomorrow = day_assigned.get((cov.date + timedelta(days=1), clinician_id))
            if tomorrow is not None:
                model.Add(x[(cov_id, clinician_id)] + tomorrow <= 1)
```

For a weekend-block shift, the Saturday row is skipped on purpose. The block covers Sunday, and the day off follows the Sunday row.

### How to test

```bash
.venv/bin/python scripts/run_my_schedule.py
```

Open the CSV output. For every row whose shift type has `next_day_off = 1`, the same clinician has no row on the next date. If the solver finds no schedule, the group is short of people for consecutive days: raise `max_shifts`, or lower `required_count`.

The agent prompt: *"Add a hard rule that a shift type with next_day_off = 1 in shift_types.csv gives the clinician the next day off. Use the existing day_assigned variables. Skip the Saturday row of a weekend_block shift."*

---

## 6. Weekend block (`weekend_block`)

"Whoever has OB call on Saturday also has it on Sunday." The flag is `weekend_block` in `shift_types.csv`. This is the opposite of entry 3, which keeps Saturday and Sunday apart. Use one or the other for a shift type, not both.

### CSV changes

None. The flag comes from the export. Note that a block counts as two weekend shifts against `target_weekend_shifts` and `max_weekend_shifts`. If the check script reports weekend capacity as tight after you add the rule, raise `max_weekend_shifts` for the people who take the block, or ask for the yearly weekend number in the app to change.

### Code changes

First, the pairing:

```python
    # HARD RULE: a weekend_block shift is worked Saturday and Sunday by the
    # same person. weekend_block_pairs lets the rest rule allow that one
    # back-to-back pair.
    slot_index = {(cov.date, cov.shift_type): cov_id for cov_id, cov in enumerate(coverage)}
    weekend_block_pairs: list[tuple[int, int]] = []
    for cov_id, cov in enumerate(coverage):
        if cov.date.weekday() != 5 or not shift_flags.get(cov.shift_type, {}).get("weekend_block"):
            continue
        sunday_id = slot_index.get((cov.date + timedelta(days=1), cov.shift_type))
        if sunday_id is None:
            continue
        weekend_block_pairs.append((cov_id, sunday_id))
        for clinician_id in clinician_ids:
            model.Add(x[(cov_id, clinician_id)] == x[(sunday_id, clinician_id)])
```

Second, the rest rule must allow that one back-to-back pair. In `HARD RULE 5` (search for `if gap <= min_gap:`), replace

```python
                if gap <= min_gap:
                    model.Add(a + b <= 1)
```

with

```python
                if gap <= min_gap:
                    # A weekend block is the one allowed back-to-back pair.
                    block = [
                        x[(sat_id, clinician_id)]
                        for sat_id, _ in weekend_block_pairs
                        if coverage[sat_id].date == day_a and gap == 1
                    ]
                    model.Add(a + b <= 1 + sum(block))
```

For everyone who does not hold the block, `sum(block)` is 0 and the rest rule is unchanged.

### How to test

```bash
.venv/bin/python scripts/run_my_schedule.py
```

Open the CSV output. For every Saturday row of a `weekend_block` shift type, the Sunday row of the same shift type names the same clinician. Everyone else still has no two days in a row. If the solver finds no schedule, the weekend caps are the usual cause. Raise `max_weekend_shifts` to at least 2 for the people who take the block.

The agent prompt: *"Add a hard rule that a shift type with weekend_block = 1 in shift_types.csv is worked by the same clinician on Saturday and on the Sunday after it. Let the minimum-rest rule allow that one pair. Keep the rule off for every other shift type."*

---

## 7. Post-call eligibility (`post_call_ok`)

"Nobody works the pain clinic shift the day after call." The flag is `post_call_ok` in `shift_types.csv`. A `0` means the shift may not be worked by a clinician who was on call the day before. "On call" means any row whose shift type has `category = call` in the same file.

This entry matters most with `scope = all` exports, where daily shifts sit beside call shifts, and for groups with a rest gap of 0. With `scope = call` and a rest gap of 1 or more it changes nothing, because entry 5 and the rest rule already keep the day after call empty.

### CSV changes

None. The flag comes from the export.

### Code changes

```python
    # HARD RULE: a shift with post_call_ok = 0 is not worked by a clinician
    # who was on call the day before. "On call" means any row whose shift
    # type has category call in shift_types.csv. The Sunday of a weekend
    # block is exempt, because the block is one assignment.
    call_rows_by_date: dict[date, list[int]] = defaultdict(list)
    for cov_id, cov in enumerate(coverage):
        if shift_flags.get(cov.shift_type, {}).get("category") == "call":
            call_rows_by_date[cov.date].append(cov_id)
    for cov_id, cov in enumerate(coverage):
        flags = shift_flags.get(cov.shift_type, {})
        if flags.get("post_call_ok", True):
            continue
        for prev_id in call_rows_by_date.get(cov.date - timedelta(days=1), []):
            prev = coverage[prev_id]
            if (
                prev.date.weekday() == 5
                and prev.shift_type == cov.shift_type
                and flags.get("weekend_block")
            ):
                continue
            for clinician_id in clinician_ids:
                model.Add(x[(prev_id, clinician_id)] + x[(cov_id, clinician_id)] <= 1)
```

### How to test

```bash
.venv/bin/python scripts/run_my_schedule.py
```

Open the CSV output. For every row whose shift type has `post_call_ok = 0`, the same clinician has no `call` row on the day before, except the Sunday of a weekend block. If the solver finds no schedule, fewer people are free the day after call than the shift needs. Lower its `required_count`, or set `post_call_ok` to `1` in the app for that shift and export again.

The agent prompt: *"Add a hard rule that a shift type with post_call_ok = 0 in shift_types.csv is not worked by a clinician who worked a call-category shift the day before. Exempt the Sunday of a weekend block."*

---

## What to add next

The pattern is the same for almost any rule:

- **Holiday balancing:** add a `holidays.csv` (date list), count assignments on those dates per clinician, add an objective term that minimizes the spread.
- **Post-call recovery for one shift type without `shift_types.csv`:** entry 5 keys on the file. For a group that has none, hard-code the shift type: replace `shift_flags.get(cov.shift_type, {}).get("next_day_off")` with `cov.shift_type == "NIGHT"`.
- **Partner vs non-partner:** add an `is_partner` column to `clinicians.csv`, then add an objective that pushes leftover call onto partners after non-partners hit their `target_shifts`.
- **Site-specific eligibility:** already supported. Just use distinct shift types (`NORTH`, `SOUTH`, `EAST`) and matching `can_*` columns.

For each one, the agent prompt is: *"Read solver.py, then add `<rule>`. Add it as a hard rule by default. Tell me what to change in the CSVs and how to test it. Run the configured schedule after."*

Add one rule at a time. Always re-run the configured schedule. Compare outputs before and after.

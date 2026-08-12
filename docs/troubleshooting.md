# Troubleshooting

What to do when the solver or data check complains. The messages below come from `solver.py` or the preflight in `scripts/check_my_data.py`, so an agent can grep for them.

The general rule when something breaks: **remove the most recent change first.** Most failures here are introduced by edits to the CSVs, not by the solver itself.

---

## Setup errors

### `ModuleNotFoundError: No module named 'ortools'`

You skipped the install, or you're running the system Python instead of the venv one.

Fix:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python solver.py
```

On Windows the venv path is different — use `.venv\Scripts\python` wherever this guide shows `.venv/bin/python`.

### OR-Tools install hangs or fails

`ortools` is a compiled package. On a slow connection or an older machine, install can take 30–60 seconds with no progress output. That's normal; let it finish.

If it actually errors, the usual culprits are:

- Python older than 3.9 — check with `python3 --version`. OR-Tools 9.x needs 3.9+.
- An x86-only Python on Apple Silicon. Reinstall a native arm64 Python.
- Corporate network blocking PyPI. Use a personal network or have your agent install through a mirror.

---

## CSV / data errors

These either raise as `ValueError` in the solver or are reported by `scripts/check_my_data.py` before the solve runs.

### `Missing clinician_id in <path>`

A row in `clinicians.csv` has an empty `clinician_id` column. Open the file and check for trailing blank rows or a row where the first column is missing.

### `Duplicate clinician_id 'X'`

Two rows in `clinicians.csv` use the same `clinician_id`. IDs must be unique. Note that `clinician_id` is the stable key — change the name if you need to, but don't reuse an ID.

### `No active clinicians found.`

Every row in `clinicians.csv` has `active=0`, or the file is empty. Set `active=1` on at least the clinicians you want included in this solve.

### `No coverage rows found.`

`coverage.csv` is empty (or only has a header). The solver has nothing to schedule. Generate coverage rows for the dates and shift types you want filled. See the [coverage section of the walkthrough](https://postcallmd.com/how-to-make-a-schedule-with-ai/#coverage) for the script pattern.

### `Missing eligibility column 'can_X' for shift type 'X'.`

You added a new shift type to `coverage.csv` but didn't add the matching eligibility column to `clinicians.csv`. The rule is one-to-one:

- shift type `OR` → column `can_or`
- shift type `OB` → column `can_ob`
- shift type `NIGHT` → column `can_night`
- shift type `CARDIAC` → column `can_cardiac`

Open `clinicians.csv`, add a `can_<shift_lowercase>` column, and put `1` next to every clinician who can cover it.

### `Request 'X' references unknown clinician 'Y'.`

A row in `requests.csv` has a `clinician_id` that doesn't exist in `clinicians.csv` (or that clinician has `active=0`). Either typo, or the clinician was renamed/removed and the request wasn't updated. Fix the ID or delete the request row.

### `history.csv references unknown clinician 'Y'.`

Same idea: a row in `history.csv` points to a `clinician_id` that isn't in `clinicians.csv`, or to one whose `active` is `0`. Update the ID, mark the clinician active, or drop the history row.

### `requests.csv row X has unknown request_type 'Y'`

The only supported request types are `vacation`, `no_call`, `prefer_off`, and `lock`. Fix the typo or change the row to one of those values.

### `requests.csv row X starts after it ends`

The `start_date` is later than `end_date`. Swap the dates or fix the typo.

### `requests.csv row X names shift_type 'Y', but coverage.csv has no rows for that shift.`

The request names a shift that is not in the current schedule period. Usually this is a typo (`OBB` instead of `OB`) or coverage was generated before the new shift type was added.

### `Lock request 'X' must set shift_type.`

A row in `requests.csv` has `request_type=lock` but a blank `shift_type`. Locks pin a clinician to a specific shift on a specific date — they always need the shift named. Fill in `shift_type` (e.g. `OR`).

### `Lock request 'X' did not match any coverage row for ...`

The lock points at a date and shift_type that aren't in `coverage.csv`. Usually a typo in the date or the shift name. Open `coverage.csv` and confirm that exact (date, shift_type) row exists. If not, either fix the lock or add the coverage row.

### `coverage.csv repeats YYYY-MM-DD SHIFT on rows ...`

The data check found multiple rows for the same date and shift type. That can be intentional, but most groups should combine them into one row with the right `required_count`; otherwise the solver treats each row as additional demand.

### `OR demand (X) exceeds combined max_shifts of eligible doctors (Y). No solver can cover this.`

`check_my_data.py` found that the per-shift demand can't be covered even if every eligible doctor maxed out on that one shift. Three ways to fix:

- Raise `max_shifts` on the doctors eligible for that shift (their actual cap was set too low).
- Make more doctors eligible (flip `can_<shift>` to `1` for someone who can cover it).
- Lower `required_count` in `coverage.csv` for that shift (or remove some of its rows).

### `OR is tight: X shifts needed, Y capacity (headroom N). One lock or vacation may make it infeasible.`

Not an error — the solver will run — but a chief should know. Any of the same three moves (raise max, add eligibility, reduce demand) buys headroom. Tightness is computed per shift type and per weekend; both can fire independently.

---

## Solver errors

### `No feasible schedule found. Try relaxing max_shifts, max_weekend_shifts, rest gaps, or hard requests.`

The solver tried, and there is no way to satisfy every hard rule simultaneously. The error message lists the four most common culprits in rough order. Walk them in this order:

1. **Did you just add a new hard rule or a new hard request?** Comment that change out and re-solve. If it works without that rule, the conflict is there — relax it or make it soft (set `hard=0` in `requests.csv`).
2. **Does total demand exceed total capacity?** Sum the `required_count` column in `coverage.csv` for the solve window. Compare to the sum of `max_shifts` across all active clinicians. If demand > max capacity, the model can't possibly cover. Lower `required_count` somewhere or raise `max_shifts`.
3. **Are weekend caps too tight?** Count weekend coverage rows (Sat + Sun within the solve window). Sum `max_weekend_shifts` across clinicians. If demand exceeds cap, you'll be infeasible.
4. **Did you set a long `min_days_between_assignments`?** A 3+ day rest gap with daily coverage is hard to satisfy with a small group. Try lowering the gap.
5. **Is anyone's eligibility wrong?** If `can_or` is set to 0 for everyone who can actually cover OR, the solver has no eligible candidates.

The fastest debugging move is: temporarily delete `requests.csv` (or move it aside), re-solve. If that succeeds, you know it's a request that's blocking. Then add requests back one at a time.

### Status: `FEASIBLE` (instead of `OPTIMAL`)

The solver hit its 60-second time limit before proving optimality. The schedule is still valid — every hard rule was respected — it just may not be the *most* fair option. For dummy data this should never happen; for larger real-world inputs, you can either:

- Accept it (the schedule is correct, just possibly slightly imbalanced)
- Raise the time limit in `solver.py` (search for the `cp_model.CpSolver()` block and look for the time-limit field)
- Reduce the size of the solve window (one month at a time, not three)

---

## Behavior surprises (not errors, but "wait why")

### Someone got fewer shifts than their `target_shifts`

`target_shifts` is a soft preference, not a guarantee. The solver will trade off targets against weekend fairness, rest spacing, and request honoring. If a clinician has tight `max_weekend_shifts` and a bunch of vacation, they'll naturally come in under target. Increase `target_deviation` in the weights (see `docs/csv-schema.md`) to push targets harder.

### A soft request was ignored

Soft requests pay a penalty (`soft_request_violation`, default 25) but don't block. If another objective term outweighs the penalty (e.g. honoring it would force a much larger fairness imbalance), the solver will violate the request. Raise `soft_request_violation` in the weights, or make the request hard.

### Vacations on weekdays only — weekends fine?

Check the `shift_type` column in `requests.csv`. If it's blank, the request blocks *all* shifts on those dates. If it has a value (e.g. `OB`), it only blocks that shift type — other shifts on the same date are still fair game.

### History.csv seems ignored

History is used for the `history_balance` weight only. It doesn't block; it tries to give people who carried more burden recently a lighter load now. If you want history to *block* (e.g. "Fox covered the last 3 weekends, do not assign Fox this weekend"), encode that as a hard request, not a history row.

---

## When to ask for help

If you've worked through this list and the solver still won't solve, paste the full error message and the relevant CSV row(s) into your coding agent and ask it to walk the model with you. If the agent is stuck too, email `moultrie@postcallmd.com` with the CSVs and the error output.

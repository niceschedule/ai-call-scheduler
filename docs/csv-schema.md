# CSV Schema

The sample solver reads four CSV files from the configured input directory.

## `clinicians.csv`

| Column | Required | Meaning |
| --- | --- | --- |
| `clinician_id` | No | Stable ID used by other files. If blank, derived from `name` (e.g. `Alice Smith` → `alice_smith`). Fill in explicitly when two clinicians share a name. |
| `name` | Yes | Display name for output, and the source for the auto-derived `clinician_id`. |
| `active` | Yes | `1` if the clinician can be scheduled. |
| `fte` | No | Full-time share, such as `1.0` or `0.8`. Informational in v1: the solver does not read it. The PostCallMD export fills it, and the sample carries it, so the files match. |
| `can_or` | Yes for `OR` shifts | `1` if eligible for OR. |
| `can_ob` | Yes for `OB` shifts | `1` if eligible for OB. |
| `target_shifts` | Yes | Preferred number of assignments in the solve period. |
| `max_shifts` | Yes | Hard maximum assignments in the solve period. |
| `target_weekend_shifts` | Yes | Preferred number of Saturday/Sunday assignments. |
| `max_weekend_shifts` | Yes | Hard maximum Saturday/Sunday assignments. |
| `min_days_between_assignments` | No | Hard rest spacing. If blank, the config default is used. |

For new shift types, add a column named `can_<shift_type_lowercase>`.
For example, `CARDIAC` uses `can_cardiac`.

In `requests.csv` and `history.csv`, the `clinician_id` value can be the canonical id, the slug of a name, or the name itself — they all resolve to the same clinician. So `r1,Alice Smith,...` works the same as `r1,alice_smith,...` once Alice exists in `clinicians.csv`.

## `coverage.csv`

| Column | Required | Meaning |
| --- | --- | --- |
| `date` | Yes | ISO date, `YYYY-MM-DD`. |
| `shift_type` | Yes | Shift to cover, such as `OR` or `OB`. |
| `required_count` | Yes | Number of clinicians needed for that date and shift type. |

Most groups don't type `coverage.csv` by hand — they describe the recurring pattern in `shift_pattern.csv` and let `scripts/generate_coverage.py` fan it out across the month.

## `shift_pattern.csv`

Lives in the same directory as `coverage.csv`. Drives `scripts/generate_coverage.py`. If the file is missing, the script falls back to a built-in default of OR + OB every day.

| Column | Required | Meaning |
| --- | --- | --- |
| `shift_type` | Yes | `OR`, `OB`, `NIGHT`, ... |
| `weekday_mask` | Yes | Seven characters, Mon–Sun. `1` = include, `0` = skip. |
| `required_count` | Yes | Number of clinicians needed for each day this row applies to. |

Common masks: `1111111` every day, `1111100` weekdays only, `0000011` weekends only, `0000100` Fridays only.

To run different counts on weekdays vs. weekends for the same shift, add two rows:

```csv
shift_type,weekday_mask,required_count
OR,1111100,2
OR,0000011,1
```

## `requests.csv`

| Column | Required | Meaning |
| --- | --- | --- |
| `request_id` | Yes | Stable ID for the request. |
| `clinician_id` | Yes | Must match `clinicians.csv`. |
| `start_date` | Yes | ISO date, inclusive. |
| `end_date` | Yes | ISO date, inclusive. |
| `request_type` | Yes | Example: `vacation`, `no_call`, `prefer_off`, `lock`. |
| `hard` | Yes | `1` means the solver must honor it. `0` means avoid it if possible. Blank defaults to `1` (hard block) — fill in `0` explicitly for soft requests. Ignored for `lock`. |
| `shift_type` | Required for `lock` | Blank means all shifts. Fill in to block or prefer off only for one shift type. Required when `request_type` is `lock`. |
| `note` | No | Human context. Not used by v1 solver. |

`lock` inverts the meaning of the row: instead of blocking the clinician from that shift, it **pins** them to it. Use it for already-published assignments, teaching obligations, or post-publication trades. Locks are always hard and require `shift_type`. The solver will raise an error if a lock points at a date/shift combination that isn't in `coverage.csv`.

## `history.csv`

| Column | Required | Meaning |
| --- | --- | --- |
| `date` | Yes | ISO date. |
| `clinician_id` | Yes | Must match `clinicians.csv`. |
| `shift_type` | Yes | Historical shift type. |
| `status` | No | Example: `final`. Informational in v1. |

History is used for fairness balancing. The days just before the month also count for the hard rules, so a month does not start against last month's end:

- The minimum rest gap, `next_day_off` and `post_call_ok` read the history rows on the days before the first coverage day.
- A Sunday on the first day of the month, of a `weekend_block` shift type, goes to the people who worked that shift on the Saturday before, in history. When such a person has a hard request on the Sunday or is not eligible, the solver prints a note and leaves them out.

## `shift_types.csv`

Optional. A PostCallMD export writes it. When it is present, the solver keeps its three flags as hard rules, with no code change, and every shift type in `coverage.csv` needs a row (the solver stops on a missing row, because a missing row would switch its rules off).

| Column | Meaning |
| --- | --- |
| `code` | The shift type, as in `coverage.csv`. Letter case does not matter. |
| `name` | Display name. Not read. |
| `category` | `call` or `daily`. A row with `call` counts as "on call" for `post_call_ok`. |
| `post_call_ok` | `0`: a clinician who worked a `call` shift the day before may not work this shift. Blank means `1`. |
| `next_day_off` | `1`: the clinician has no assignment on the day after this shift. Blank means `0`. |
| `weekend_block` | `1`: the same clinician works this shift on Saturday and on the Sunday after it. Blank means `0`. Saturday and Sunday must need the same `required_count`. A Saturday or Sunday without its partner row is solved as one day, and the solver prints a note. |
| `color` | Display color. Not read. |

The Saturday and Sunday of a block are one assignment. So the minimum rest gap, `next_day_off` and `post_call_ok` allow that one pair, and the day off of `next_day_off` follows the Sunday. A block counts as two weekend shifts against `target_weekend_shifts` and `max_weekend_shifts`, so a person needs `max_weekend_shifts` of 2 or more to take one.

`scripts/check_schedule.py` checks these rules, and the other hard rules, on the solver's output.

## `config/sample_rules.json`

The config file controls where data is read from, where output is written, and the relative weights of the soft objectives. The sample looks like this:

```json
{
  "input_dir": "data/sample",
  "output_csv": "output/sample_schedule.csv",
  "rules": {
    "min_days_between_assignments_default": 1,
    "preferred_days_between_assignments": 3,
    "weights": {
      "target_deviation": 100,
      "history_balance": 40,
      "weekend_deviation": 35,
      "soft_request_violation": 25,
      "rest_spacing": 5
    }
  }
}
```

### Top-level keys

| Key | Meaning |
| --- | --- |
| `input_dir` | Directory containing the four CSVs. Relative to the config file. |
| `output_csv` | Where the solved schedule is written. Relative to the config file. |
| `enforces` | PostCallMD route only. The shift-type rules this solver keeps: `weekend_block`, `next_day_off`, `post_call_ok`. `my_rules.template.json` lists all three. `scripts/to_postcallmd_draft.py` clears a `BLOCKING:` line for a listed rule only when `solver.py` has the code for that rule. The stock `solver.py` has it for all three. The solver does not read this key: it keeps the rules from `shift_types.csv` either way. |

### `rules.min_days_between_assignments_default`

Hard floor on the rest gap between any clinician's two assignments. Used when `min_days_between_assignments` in `clinicians.csv` is blank for that clinician.

`1` means no two assignments on consecutive days. `2` means at least one full day off between assignments. `0` allows back-to-back.

### `rules.avoid_b2b_weekends`

`true` (the default) turns on the `b2b_weekend` penalty: the solver tries not to give one clinician two weekends in a row. `false` turns it off. This is the PostCallMD practice setting "avoid back-to-back weekends". The export does not carry that setting yet, so set it here.

### `rules.history_balance`

What the `history_balance` weight balances. `"total"` (the default when the key is absent): history plus this month, the same for every clinician. `"target"` (set in `my_rules.template.json`): history plus this month, minus `target_shifts` for each month of the clinician's history and this month. With `"target"`, a half-time clinician with half the target is even at half the calls.

### `rules.preferred_days_between_assignments`

The *preferred* rest gap — the solver pays a soft penalty when two assignments fall closer than this. The hard floor (above) is still enforced; this is the "ideal" spacing the solver tries to reach.

### `rules.weights`

These are the relative importances of the soft objectives. Higher number = the solver works harder to optimize that thing. They are unitless — only the *ratio* between them matters. Doubling all of them is a no-op.

| Weight | Default | What it controls |
| --- | --- | --- |
| `target_deviation` | 100 | Penalty per shift over or under each clinician's `target_shifts`. Highest weight in the sample — the solver tries hardest to hit targets. |
| `history_balance` | 40 | Penalty when (history + current) totals are imbalanced across clinicians. Pulls down whoever covered the most recent months. With `rules.history_balance` set to `"target"`, the totals are counted against each clinician's `target_shifts`. |
| `weekend_deviation` | 35 | Penalty per weekend shift over or under each clinician's `target_weekend_shifts`. Weekend fairness is tracked separately from total fairness. |
| `soft_request_violation` | 25 | Penalty when a `hard=0` request is violated. Set higher than `target_deviation` if you want soft requests to almost always be honored even at the cost of fairness. |
| `weekday_repeat` | 20 (0 in sample) | Penalty per historical `(clinician, weekday)` per new assignment on that weekday. Pulls apart stagnation patterns like "Cary always gets Mondays" over time. No-op on a first-month solve (history is empty). `sample_rules.json` sets this to `0` so the article's sample schedule stays stable; `my_rules.template.json` sets `20` so real groups get the term active. |
| `b2b_weekend` | 40 (0 in sample) | Penalty when one clinician works two weekends in a row. A weekend is Saturday and Sunday. The last weekend before the month, from history, counts. Off when `rules.avoid_b2b_weekends` is `false`. |
| `rest_spacing` | 5 | Small penalty whenever two assignments fall closer than `preferred_days_between_assignments`. The closer the pair, the higher the penalty. Low weight by design — the hard rest gap is what guarantees rest; this just gently spreads things out beyond the floor. |

### Tuning the weights

The shape of the schedule changes when these ratios change. A few common moves:

- **Targets feel ignored** → raise `target_deviation` to 150 or 200.
- **Weekend fairness keeps slipping** → raise `weekend_deviation` to match or exceed `target_deviation`.
- **Soft requests keep getting trampled** → raise `soft_request_violation` to 50 or above.
- **People keep getting double-booked tight even though rules say min 1 day** → raise `rest_spacing` to 20 or 30. Note: this is a soft preference; it cannot create rest the hard rules don't allow.
- **The same person keeps getting the same weekday month after month** → raise `weekday_repeat` to 40 or 60. The penalty compounds with history depth, so as history grows you may want to ease it back down so this one term doesn't dominate.

Always change one weight at a time. Solve. Look at the output. Compare to before. Tuning is iterative; there is no "correct" set of numbers — only the set that produces schedules your group accepts.

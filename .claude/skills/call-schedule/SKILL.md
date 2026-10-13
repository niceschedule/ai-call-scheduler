---
name: call-schedule
description: Use when someone wants to build, adjust, or re-run an on-call schedule for a medical group with this repo (ai-call-scheduler). Covers the sample run, first-time setup from a conversation, solving a month, fixing infeasible results, and rolling to the next month.
---

# Call schedule

This repo builds on-call schedules with a deterministic solver (Google OR-Tools CP-SAT). You do not invent the schedule. You turn the person's rules into CSV files and config, then run the solver. The same inputs always give the same schedule.

The person is usually a clinician, not a programmer. Speak in plain English. Say "it could not find a schedule that works", not "infeasible". Do not quote raw solver scores or weights. Run commands yourself instead of handing them to the person.

Read `AGENTS.md` first. It sets the tone and says which files to leave alone (`solver.py`, `schedule_html.py`, `README.md`, `docs/*`, `config/sample_rules.json`, `data/sample/*`). If the person's own prompt asks for something different, follow the prompt.

## 1. Run the sample and show it

Show a real schedule before asking questions.

1. From the repo root, run `./scripts/quickstart.sh`. It installs what is missing, solves the sample, and opens `output/sample_schedule.html`.
2. Manual equivalent: `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, `.venv/bin/python solver.py`, then open the HTML file (`open` on macOS, `xdg-open` on Linux, `start` on Windows).
3. Confirm the output says `Status: OPTIMAL`.
4. Tell the person this is a made-up six-doctor group. Mention that dragging a shift onto another person on the same day makes a one-off swap in the HTML. Bigger changes are done by re-solving.
5. Ask about their group in one open question: how many people, what shifts, what period.

Skip the sample if `data/my_data/` and `config/my_rules.json` already exist, unless something broke.

## 2. Set up a real group

Follow `docs/new-practice-setup.md` for phrasing and defaults. Read `docs/csv-schema.md` and `docs/scheduler-agent-skill.md` before you translate rules.

Ask about all four of these by name. Skipping one silently weakens fairness.

- Roster and eligibility: who is on staff and which shifts each person can cover.
- Coverage pattern: which shifts run on which days, and how many people each needs.
- Rest rules: how many days between assignments, weekend limits, any hard maximums per person.
- Time off for the period, including "nobody is out", and recent call history, including "starting fresh".

Then build the files:

1. Copy the templates: `cp -R data/template data/my_data` and `cp config/my_rules.template.json config/my_rules.json`.
2. Fill `data/my_data/clinicians.csv`. Leave `clinician_id` blank. It is derived from the name. Add a `can_<shift>` column for every shift type.
3. Fill `data/my_data/shift_pattern.csv` with rows of `shift_type,weekday_mask,required_count`. The mask has seven characters, Monday to Sunday.
4. Generate the month of coverage: `.venv/bin/python scripts/generate_coverage.py --year YYYY --month M`.
5. Fill `requests.csv` (types `vacation`, `no_call`, `prefer_off`, `lock`) and `history.csv`.
6. Check `config/my_rules.json` for the input folder, the output path, and the rule weights.

Before you run anything, read the person's data back in plain English and ask them to correct it. `data/my_data/` and `config/my_rules.json` are git-ignored, so they stay local.

For rules the CSVs cannot express, see `docs/adaptation-cookbook.md`. Changing `solver.py` needs a plain-English description and a clear yes first. Make one change at a time.

## 3. Solve a month

1. Run `.venv/bin/python scripts/run_my_schedule.py`. It checks the data, prints a capacity summary, solves, and opens the HTML.
2. Read the capacity summary before the solve result. If a shift is short or tight, say so in human terms, for example "OR coverage is tight, so extra time off may make it hard".
3. Report in plain language: whether a valid schedule was found, how many shifts were filled, what ran tight, and one thing to try next, as a question.
4. A status of `FEASIBLE` instead of `OPTIMAL` means the time limit ran out. The schedule is valid but may be slightly less balanced. See `docs/troubleshooting.md`.

The real output path is set in `config/my_rules.json` (`output_csv`).

## 4. When no schedule is found

Use `docs/troubleshooting.md`, section "No feasible schedule found". Work in this order:

1. Undo the most recent rule or request change and re-solve. If that works, the conflict is in that change.
2. Compare total demand (sum of `required_count` in `coverage.csv`) with total capacity (sum of `max_shifts`). Weekend rows against `max_weekend_shifts` too.
3. Lower a long `min_days_between_assignments`. Three days or more with daily coverage is hard for a small group.
4. Check eligibility columns. A shift with no eligible person cannot be filled.
5. If still stuck, move `requests.csv` aside and re-solve. Then add requests back one at a time. Making a hard request soft (`hard=0`) is often the fix.

Tell the person which coverage rows were hardest to fill, in plain terms, and offer the options: relax a limit, make a request soft, or reduce demand. Let them choose. Other error messages (missing columns, unknown clinicians, bad dates) are listed in the same file with fixes.

## 5. Next month

Do not restart the tutorial each month.

1. Run `.venv/bin/python scripts/start_next_month.py`. It infers the month from the output filename, or pass `--year YYYY --month M`. Its first line names the month. Confirm it with the person.
2. The script files the last schedule into history, regenerates coverage, and points the config at a new dated output file.
3. Update `requests.csv` with new time off, or pass `--reset-requests` for a clean file.
4. Edit `clinicians.csv` only for roster, eligibility, or target changes.
5. Run `.venv/bin/python scripts/run_my_schedule.py` and give a short report.

## Privacy

Say this once when real data first appears. The solver runs locally. The chat itself, and any files you read, may be sent to the person's AI provider. Real names are fine for most groups. Use made-up ids for public examples or sensitive setups. Details are in `docs/agent-privacy.md`.

## Publish to PostCallMD

Placeholder: Steps to upload output/schedule.csv to PostCallMD are being written; see the hosted product at https://postcallmd.com/.

Do not invent upload steps. Until this section is filled in, do not tell the person that an upload is possible from this repo.

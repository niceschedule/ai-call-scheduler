---
name: call-schedule
description: Use when someone wants to build, adjust, or re-run an on-call schedule for a medical group with this repo (ai-call-scheduler). Covers the sample run, first-time setup from a conversation, solving a month, fixing infeasible results, and rolling to the next month.
---

# Call schedule

This repo builds on-call schedules with a deterministic solver (Google OR-Tools CP-SAT). You do not invent the schedule. You turn the person's rules into CSV files and config, then run the solver. The same inputs always give the same schedule.

The person is usually a clinician, not a programmer. Speak in plain English. Say "it could not find a schedule that works", not "infeasible". Do not quote raw solver scores or weights. Run commands yourself instead of handing them to the person.

Read `AGENTS.md` first. It sets the tone and says which files to leave alone (`solver.py`, `schedule_html.py`, `README.md`, `docs/*`, `config/sample_rules.json`, `data/sample/*`). If the person's own prompt asks for something different, follow the prompt.

## Commands on every system

Run every Python step through `scripts/py`. It finds the venv's Python on macOS, Linux and Windows, and it makes `.venv` on first use. This file writes the macOS and Linux form, `python3 scripts/py <script>`. On Windows, type `py -3` where a step says `python3`. If Windows has no `py` command, use `python`. Python 3.9 or newer is needed.

## 1. Run the sample and show it

Show a real schedule before asking questions.

1. From the repo root, run `python3 scripts/quickstart.py`. It installs what is missing, solves the sample, and opens `output/sample_schedule.html`. If the machine has no git, download `https://github.com/niceschedule/ai-call-scheduler/archive/refs/heads/main.zip`, unpack it, and run the script from the unpacked folder.
2. Manual equivalent: `python3 scripts/py solver.py`, then open the HTML file (`open` on macOS, `xdg-open` on Linux, `start` on Windows).
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
4. Generate the month of coverage: `python3 scripts/py scripts/generate_coverage.py --year YYYY --month M`.
5. Fill `requests.csv` (types `vacation`, `no_call`, `prefer_off`, `lock`) and `history.csv`.
6. Check `config/my_rules.json` for the input folder, the output path, and the rule weights.

Before you run anything, read the person's data back in plain English and ask them to correct it. `data/my_data/` and `config/my_rules.json` are git-ignored, so they stay local.

For rules the CSVs cannot express, see `docs/adaptation-cookbook.md`. Changing `solver.py` needs a plain-English description and a clear yes first. Make one change at a time.

## 3. Solve a month

1. Run `python3 scripts/py scripts/run_my_schedule.py`. It checks the data, prints a capacity summary, solves, and opens the HTML.
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

Do not restart the tutorial each month. If the person came through PostCallMD, use "Each month on the app route" at the end of this file instead. The steps below are for a group whose data lives in the CSV files.

1. Run `python3 scripts/py scripts/start_next_month.py`. It infers the month from the output filename, or pass `--year YYYY --month M`. Its first line names the month. Confirm it with the person.
2. The script files the last schedule into history, regenerates coverage, and points the config at a new dated output file.
3. Update `requests.csv` with new time off, or pass `--reset-requests` for a clean file.
4. Edit `clinicians.csv` only for roster, eligibility, or target changes.
5. Run `python3 scripts/py scripts/run_my_schedule.py` and give a short report.

## Privacy

Say this once when real data first appears. The solver runs locally. The chat itself, and any files you read, may be sent to the person's AI provider. Real names are fine for most groups. Use made-up ids for public examples or sensitive setups. Details are in `docs/agent-privacy.md`.

## Publish to PostCallMD

Use this section when the person came through the PostCallMD MCP server. You can tell: the tools `export_solver_inputs`, `get_practice_overview`, `submit_schedule_draft` and `save_solver` are in your tool list. On that route the roster, coverage, requests and history are already in the app, so skip section 2. Read the app-route section of `AGENTS.md` first.

Two scripts carry the data between the app and the solver. You paste nothing by hand in either direction.

1. Read `get_scheduling_rules`. The newest record is the practice's rulebook. Follow it.
2. Call `export_solver_inputs` for the month (`YYYY-MM`). Save the whole result, all six keys, as JSON to `output/postcallmd_export.json`.
3. Run `python3 scripts/py scripts/from_postcallmd.py output/postcallmd_export.json`. It writes the five CSV files and `notes.txt` into `data/my_data/` and creates `config/my_rules.json` from the template when it is missing. It stops on a missing key or column.
4. Read the notes it prints. They name the requests and rules the files cannot express. Tell the person about each one. The three shift-type rules (`next_day_off`, `weekend_block`, `post_call_ok`) have worked code in `docs/adaptation-cookbook.md`, section "App rules from shift_types.csv". Add one only with a clear yes, and follow the install order there: weekend block first. If the script prints a STOP line, the notes have `BLOCKING:` lines. Stop. Tell the person each one. Do not solve or submit until a new export has none.
5. Run `python3 scripts/py scripts/run_my_schedule.py`. Fix every error the data check reports before the solve. Report the result as in section 3.
6. Call `get_practice_overview`. Save the whole result as JSON to `output/postcallmd_overview.json`. Its `shift_types` array carries the ids.
7. Run `python3 scripts/py scripts/to_postcallmd_draft.py --overview output/postcallmd_overview.json --month YYYY-MM`. It reads the solver output named in `config/my_rules.json` and writes `output/postcallmd_draft.json`. `start_date` and `end_date` are the first and last day of the month. It stops on a shift code with no id, a clinician who is not in `data/my_data/clinicians.csv`, a date outside the month, or a `BLOCKING:` line in `data/my_data/notes.txt`.
8. Call `submit_schedule_draft` with `start_date`, `end_date`, `mode` and `assignments` taken from that file. `mode` is `fill_empty`: it adds entries and skips cells that already hold one.
9. Report `created`, `skipped` and `apply_findings` from the response, plus the call count per clinician that the draft script printed. If `skipped` is not zero, say which cells the app already had.
10. Ask the person to review the draft in the app. After they confirm it, call `save_solver` once. Send `config/my_rules.json`, every repo file you changed (for example `solver.py` after a cookbook rule), the rules as numbered plain sentences in `rules_text`, the steps of "Each month on the app route" below as `run_instructions` (not these first-month steps), and the month as `source_draft_month`. The next month then starts from the stored solver.

Never publish. The admin publishes in the app. The only app writes on this route are `submit_schedule_draft` and `save_solver`. To undo a draft, call `revert_schedule_draft` with the `solver_run_id` from the submit response.

If `submit_schedule_draft` reports an unknown `shift_type_id`, the saved overview is stale. Save `get_practice_overview` again and rerun step 7.

## Each month on the app route

This loop replaces `scripts/start_next_month.py` on the app route. Do not run that script, and do not edit `requests.csv` or `history.csv` by hand. The export carries the month's approved requests and the app's history.

1. If `get_solver` returns a stored solver, write each of its files to its path in the clone first.
2. Read `get_scheduling_rules`. Follow the newest record.
3. Export: call `export_solver_inputs` for the month (`YYYY-MM`). Save the whole result as JSON to `output/postcallmd_export.json`.
4. Import: run `python3 scripts/py scripts/from_postcallmd.py output/postcallmd_export.json`. It replaces the files in `data/my_data/` and points `config/my_rules.json` at `output/schedule_YYYY-MM.csv`, so last month's schedule stays. If it prints a STOP line, stop and tell the person each `BLOCKING:` line.
5. Solve: run `python3 scripts/py scripts/run_my_schedule.py`. Report as in section 3.
6. Draft: call `get_practice_overview` and save the result to `output/postcallmd_overview.json`. Then run `python3 scripts/py scripts/to_postcallmd_draft.py --overview output/postcallmd_overview.json --month YYYY-MM`.
7. Submit: call `submit_schedule_draft` with `start_date`, `end_date`, `mode` and `assignments` from `output/postcallmd_draft.json`. Report `created`, `skipped` and `apply_findings`.

Call `save_solver` again only when a solver file changed this month, for example after a new cookbook rule. A one-month change to `clinicians.csv` is gone after the new export. Make a lasting change in the app.

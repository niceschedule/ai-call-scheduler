#!/usr/bin/env python3
"""HTML rendering for the call schedule.

Kept separate from solver.py so the solver file stays focused on the model
(decision variables, hard rules, soft preferences). Edit this file when you
want to change how the printable schedule looks; edit solver.py when you want
to change how the schedule is built.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from html import escape
from pathlib import Path

from solver import (
    CoverageRow,
    RequestRow,
    dates_between,
    is_weekend,
    parse_int,
)


def hard_block_label(request: RequestRow, code_map: dict[str, str] | None = None) -> str:
    # Block chips stay as short as the call codes so the grid reads as one tidy
    # row of chips. A shift-specific block names the shift by its chip code
    # ("NO OB", "NO DOW"); a blanket no-call day is just "OFF".
    if request.shift_type:
        code = (code_map or {}).get(request.shift_type, request.shift_type)
        return f"NO {code}"
    if request.request_type == "vacation":
        return "VAC"
    if request.request_type == "no_call":
        return "OFF"
    return "OFF"


def hard_block_kind(request: RequestRow) -> str:
    return "vacation" if request.request_type == "vacation" else "block"


# Chip colors. Each entry is a (background, ink) pair for a pill chip. The point
# of a *fixed, ordered* palette — rather than hashing each shift name to a color —
# is that the chips a schedule actually uses are guaranteed to be far apart in
# hue. assign_shift_colors() hands these out in order, so the first few shift
# types (almost every real schedule has only 2–4) land on the most distinct
# colors. Crucially, none of these is a grey or a near-black: those tones are
# reserved for vacation/off below, so an "off" chip can never be mistaken for a
# call chip — a real mix-up we hit when vacation borrowed a tint from the pool.
_BLUE = ("#caddef", "#15466c")
_AMBER = ("#f1dcab", "#6e4a16")
_PINK = ("#f4cadd", "#8a2f5c")
_GREEN = ("#cee6b9", "#33561a")
_PURPLE = ("#dccbed", "#523a6b")
_TEAL = ("#bbe2dc", "#125b55")
_CORAL = ("#f6c7bc", "#8f3329")
_SIENNA = ("#e6cfb2", "#79491e")

# Ordered most-distinct-first. OR and OB pin to the article's heritage blue and
# amber via SHIFT_PREFERENCES; every other shift type is drawn from this pool in
# order. Eight clearly different hues — well past the 2–4 a real call schedule
# uses, and the early entries are the furthest apart.
CHIP_PALETTE = [_BLUE, _AMBER, _PINK, _GREEN, _PURPLE, _TEAL, _CORAL, _SIENNA]

# A couple of shift names keep a fixed color so existing schedules don't re-hue
# when this file changes. Only names whose color is part of the tool's identity
# belong here; anything else just takes the next free color from CHIP_PALETTE.
SHIFT_PREFERENCES = {
    "OR": _BLUE,
    "OB": _AMBER,
}

# Vacation and hard blocks are deliberately NOT in the color pool, so an "off"
# chip can never look like a call chip. Vacation is the one DARK, hue-less chip
# on the grid — a slate fill with light text — which reads as "this person is
# OFF" and stands clear of every (light, tinted) call chip. A plain block
# (no-call / "NO OB") is the faintest chip: a pale grey outline that sits quietly
# behind the colored calls. Both print cleanly and look official.
VACATION_BG = "#3a434e"
VACATION_INK = "#eef1f4"
VACATION_RULE = "#3a434e"
BLOCK_BG = "#eceef1"
BLOCK_INK = "#586069"
BLOCK_RULE = "#ccd1d7"


SOFT_REQUEST_PHRASE = {
    "vacation": "vacation",
    "no_call": "no-call request",
    "prefer_off": "preference to be off",
}


def assign_shift_colors(shift_types) -> dict[str, tuple[str, str]]:
    """Map every shift type to a DISTINCT chip color (background, ink).

    Colors are handed out from CHIP_PALETTE in name order, so the result is
    stable across re-runs and the first few shift types — all most schedules
    ever have — land on the hues that are furthest apart. A name listed in
    SHIFT_PREFERENCES (OR, OB) keeps its heritage color when it's still free;
    everyone else takes the next unused palette color, in order.

    With more shift types than the pool has colors (8), the extras wrap around
    and reuse a hue — well past anything a real call schedule needs."""
    ordered = sorted(shift_types, key=str.upper)
    assignment: dict[str, tuple[str, str]] = {}
    used: set[tuple[str, str]] = set()
    # Honor heritage preferences first, while their color is still free.
    for st in ordered:
        pref = SHIFT_PREFERENCES.get(st.upper())
        if pref and pref not in used:
            assignment[st] = pref
            used.add(pref)
    # Everyone else takes the next unused palette color, in order.
    for st in ordered:
        if st in assignment:
            continue
        choice = next((c for c in CHIP_PALETTE if c not in used), None)
        if choice is None:
            choice = CHIP_PALETTE[len(used) % len(CHIP_PALETTE)]
        assignment[st] = choice
        used.add(choice)
    return assignment


# Noise words for code generation: connectors plus the common org/site prefix
# that's often abbreviated inconsistently (Saint/St). Kept deliberately small so
# it never swallows a real qualifier — a shift literally named "North" still
# codes as NOR. Only applied when dropping these still leaves a distinctive word.
_CODE_STOPWORDS = {"st", "saint", "ste", "the", "of", "and", "at"}


def _tokenize(name: str) -> list[str]:
    """Split a shift name into lowercase word tokens. Handles spaces, hyphens,
    underscores and slashes, plus camelCase ('NightOR' -> ['night', 'or'])."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", spaced) if t]


def _code_candidates(tokens: list[str], max_len: int):
    """Yield candidate chip codes for one name, best first. `tokens` is the
    name's *distinctive* words (shared words already removed), in reading order.

    Two or more distinctive words lead with their initials (down town -> DT,
    east side -> ES); a single distinctive word leads with its first letters
    (downtown -> DOW, cardiology -> CAR). Everything else is a collision
    fallback, kept as short as the max allows."""
    if not tokens:
        return
    head = tokens[0]
    multiword = len(tokens) >= 2
    initials = "".join(t[0] for t in tokens)  # down town -> DT, east side -> ES
    if multiword:
        yield initials[:max_len]              # prefer initials when there are
    yield head[:max_len]                      # several words; else DOW / CAR
    # First letter of the lead word + two *distinct* later letters of it, so a
    # shared lead resolves to another full-length code (cardiology vs
    # cardiothoracic -> CAR / CAD) rather than a doubled letter or a stub.
    for i in range(1, len(head)):
        for j in range(i + 1, len(head)):
            yield head[0] + head[i] + head[j]
    # Shorter fallbacks, then a digit suffix as a last resort.
    yield head[:2]
    if multiword:
        yield initials[:2]
    for d in "234567890":
        yield head[:2] + d
        yield head[0] + d


def assign_shift_codes(shift_types) -> dict[str, str]:
    """Map each shift type to a short, unique, uppercase chip code (max 3 chars).

    A name that's already a tidy code is kept as-is (OR, OB, LSC, RMC). A longer
    name is abbreviated from the part that makes it *distinct*: words shared
    across several shift names are dropped, so a common "Saint Francis" prefix
    never drives the code (no SFD / SFE that differ only in a trailing letter).
    What remains becomes the code —
      * two or more distinctive words -> their initials
        ("Saint Francis Down Town" / "...East Side" -> DT / ES);
      * a single distinctive word -> its first letters
        ("Saint Francis Downtown" -> DOW, "Cardiology" -> CAR).
    Codes are capped at three characters and never collide. A single run-together
    word can't be split, so "Eastside" yields EAS rather than ES — write it as
    two words ("East Side") if you want the initials."""
    names = sorted(shift_types, key=str.upper)
    tokens = {n: (_tokenize(n) or [n.lower()]) for n in names}

    # Document frequency: how many shift names contain each word. A word that
    # shows up in several names (a shared "Saint Francis") carries no signal, so
    # only the words unique to this shift drive its code.
    df: Counter[str] = Counter()
    for toks in tokens.values():
        for t in set(toks):
            df[t] += 1

    codes: dict[str, str] = {}
    used: set[str] = set()
    for n in names:
        # Distinctive words = those unique to this shift, minus noise words. The
        # noise list also catches the prefix that's often abbreviated
        # inconsistently ("Saint Francis Downtown" vs "St Francis Eastside"):
        # without it, "saint" and "st" each look unique and leak in, giving the
        # too-similar SD / SE instead of DOW / EAS. Fall back gracefully if a
        # filter would leave nothing.
        unique = [t for t in tokens[n] if df[t] == 1]
        distinctive = [t for t in unique if t not in _CODE_STOPWORDS] or unique or tokens[n]
        code = next(
            (c.upper() for c in _code_candidates(distinctive, 3) if c and c.upper() not in used),
            None,
        )
        if code is None:
            # Absurdly many near-identical names — synthesize something free.
            base = "".join(ch for ch in n.upper() if ch.isalnum())[:2] or "X"
            i = 1
            while f"{base}{i}"[:3] in used:
                i += 1
            code = f"{base}{i}"[:3]
        codes[n] = code
        used.add(code)
    return codes


def shift_css_class(shift_type: str) -> str:
    # Prefix avoids collisions with built-in CSS class names and HTML elements.
    return "s-" + shift_type.lower().replace(" ", "_").replace("-", "_")


def build_schedule_summary(
    clinicians: dict[str, dict[str, str]],
    assignments: list[dict[str, str]],
    requests: list[RequestRow],
    history_total: Counter[str],
    totals: dict[str, int],
    weekend_totals: dict[str, int],
    soft_violations: list[tuple[RequestRow, date, str]],
) -> list[str]:
    """Plain-English bullets describing what the solver did. Rendered below
    the grid as a chief-facing review pane (hidden on print)."""
    bullets: list[str] = []

    n_shifts = len(assignments)
    n_active = sum(1 for cid in clinicians if totals.get(cid, 0) > 0)
    bullets.append(f"Filled all {n_shifts} shifts across {n_active} clinicians.")

    hard_blocks = [r for r in requests if r.hard and r.request_type != "lock"]
    if hard_blocks:
        word = "request" if len(hard_blocks) == 1 else "requests"
        bullets.append(f"All {len(hard_blocks)} time-off {word} honored.")

    locks = [r for r in requests if r.request_type == "lock"]
    if len(locks) > 3:
        bullets.append(f"{len(locks)} pre-set assignments locked in place.")
    else:
        for r in locks:
            name = clinicians.get(r.clinician_id, {}).get("name", r.clinician_id)
            if r.start_date == r.end_date:
                when = r.start_date.strftime("%a %b %-d")
            else:
                when = f"{r.start_date.strftime('%b %-d')}–{r.end_date.strftime('%b %-d')}"
            bullets.append(f"{name} pinned to {r.shift_type} on {when}.")

    active_totals = [totals[cid] for cid in clinicians if totals.get(cid, 0) > 0]
    if len(active_totals) >= 2:
        spread = max(active_totals) - min(active_totals)
        if spread == 0:
            bullets.append("Every clinician got the exact same number of shifts.")
        elif spread == 1:
            bullets.append(
                "Workload is within 1 shift across everyone — the tightest spread possible."
            )

    callouts: list[str] = []
    for cid in sorted(clinicians, key=lambda c: clinicians[c]["name"]):
        name = clinicians[cid]["name"]
        assigned = totals.get(cid, 0)
        weekend = weekend_totals.get(cid, 0)
        max_shifts = parse_int(clinicians[cid].get("max_shifts", ""), 999)
        max_weekend = parse_int(clinicians[cid].get("max_weekend_shifts", ""), 999)
        target = parse_int(clinicians[cid].get("target_shifts", ""), 0)
        if max_shifts < 999 and assigned >= max_shifts:
            callouts.append(f"{name} hit their max of {max_shifts} shifts.")
        elif target and target - assigned >= 2:
            callouts.append(f"{name} came in under target ({assigned} of {target}).")
        elif target and assigned - target >= 1:
            callouts.append(f"{name} went over target ({assigned} vs {target}).")
        if max_weekend < 999 and weekend >= max_weekend:
            callouts.append(f"{name} hit their weekend max of {max_weekend}.")
    bullets.extend(callouts[:5])

    if history_total and totals:
        top_cid, top_prior = max(history_total.items(), key=lambda kv: kv[1])
        if top_cid in clinicians and top_prior > 0:
            this_month = totals.get(top_cid, 0)
            avg = sum(totals.values()) / max(len(totals), 1)
            if this_month < top_prior and this_month <= avg:
                name = clinicians[top_cid]["name"]
                bullets.append(
                    f"{name} carried the most call last month ({top_prior} shifts); "
                    f"got a lighter load this month ({this_month}) so the stacking doesn't repeat."
                )

    for r, d, shift in soft_violations:
        name = clinicians.get(r.clinician_id, {}).get("name", r.clinician_id)
        when = d.strftime("%a %b %-d")
        phrase = SOFT_REQUEST_PHRASE.get(r.request_type, r.request_type.replace("_", " "))
        suffix = f" ({r.note})" if r.note else ""
        bullets.append(
            f"Couldn't honor {name}'s {phrase} on {when} — covered {shift}.{suffix}"
        )

    return bullets


def write_html_schedule(
    output_path: Path,
    coverage: list[CoverageRow],
    clinicians: dict[str, dict[str, str]],
    assignments: list[dict[str, str]],
    requests: list[RequestRow],
    history_total: Counter[str],
):
    all_dates = sorted({c.date for c in coverage})
    if not all_dates:
        return

    clinician_ids = sorted(clinicians.keys(), key=lambda cid: clinicians[cid]["name"])
    num_days = len(all_dates)

    assignment_map: dict[tuple[date, str], list[str]] = defaultdict(list)
    for a in assignments:
        d = datetime.strptime(a["date"], "%Y-%m-%d").date()
        assignment_map[(d, a["clinician_id"])].append(a["shift_type"])

    # Codes are needed both for the call chips and for shift-specific block
    # chips ("NO OB"), so resolve them up front.
    shift_types = sorted({c.shift_type for c in coverage})
    shift_code_map = assign_shift_codes(shift_types)

    hard_block_map: dict[tuple[date, str], list[tuple[str, str]]] = defaultdict(list)
    for r in requests:
        if r.hard:
            for d in dates_between(r.start_date, r.end_date):
                label = hard_block_label(r, shift_code_map)
                item = (label, hard_block_kind(r))
                items = hard_block_map[(d, r.clinician_id)]
                if item not in items:
                    items.append(item)

    totals: dict[str, int] = defaultdict(int)
    weekend_totals: dict[str, int] = defaultdict(int)
    for (d, cid), shifts in assignment_map.items():
        totals[cid] += len(shifts)
        if is_weekend(d):
            weekend_totals[cid] += len(shifts)

    soft_violations: list[tuple[RequestRow, date, str]] = []
    for r in requests:
        if r.hard:
            continue
        for d in dates_between(r.start_date, r.end_date):
            for shift in assignment_map.get((d, r.clinician_id), []):
                if r.shift_type is None or r.shift_type == shift:
                    soft_violations.append((r, d, shift))

    summary_bullets = build_schedule_summary(
        clinicians, assignments, requests, history_total, totals, weekend_totals, soft_violations
    )
    summary_items = "".join(f"<li>{escape(b)}</li>\n" for b in summary_bullets)
    summary_html = (
        f'<section class="schedule-summary"><h2>Summary</h2><ul>{summary_items}</ul></section>'
        if summary_items
        else ""
    )

    shift_color_map = assign_shift_colors(shift_types)

    # Each assignment renders as a pill chip (.pill) inside an otherwise-blank
    # cell, so the grid reads as a row of chips rather than blocks of color. The
    # color lives on the chip, not the cell.
    #   Screen: filled chip + inset frame.
    #   Default print: outline-only chip (white fill, hairline border) so it
    #     reads as a chip in B&W without burning ink; the code itself is enough
    #     to identify the shift.
    #   "Print in color" (body.print-color): force-print the filled chip.
    shift_css = ""
    for st in shift_types:
        bg, ink = shift_color_map[st]
        cls = shift_css_class(st)
        shift_css += (
            f"    .gcell.{cls} .pill {{ background: {bg}; color: {ink}; box-shadow: inset 0 0 0 1px {ink}59; }}\n"
            f"    .swatch.{cls} {{ background: {bg}; color: {ink}; border: 1px solid {ink}59; }}\n"
            # The edited-marker dot picks up the *original* shift's ink, so a
            # cell that lost an OR still shows a blue dot even though it now
            # renders as empty. Cells that were originally empty fall back to
            # currentColor (set on .gcell.edited::after below).
            f"    .gcell.edited[data-orig-cls=\"{cls}\"]::after {{ background: {ink}; }}\n"
            f"    @media print {{ .gcell.{cls} .pill {{ background: #fff !important; color: #000 !important; box-shadow: inset 0 0 0 1px #b9bfc6 !important; }} }}\n"
            f"    @media print {{ body.print-color .gcell.{cls} .pill {{ background: {bg} !important; color: {ink} !important; box-shadow: inset 0 0 0 1px {ink}59 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }} }}\n"
        )
    shift_css += (
        f"    .gcell.vacation .pill {{ background: {VACATION_BG}; color: {VACATION_INK}; box-shadow: inset 0 0 0 1px {VACATION_RULE}; }}\n"
        f"    .swatch.vacation {{ background: {VACATION_BG}; color: {VACATION_INK}; border: 1px solid {VACATION_BG}; }}\n"
        f"    .gcell.block .pill {{ background: {BLOCK_BG}; color: {BLOCK_INK}; box-shadow: inset 0 0 0 1px {BLOCK_RULE}; }}\n"
        f"    .swatch.block {{ background: {BLOCK_BG}; color: {BLOCK_INK}; border: 1px solid {BLOCK_RULE}; }}\n"
        f"    @media print {{\n"
        f"      .gcell.vacation .pill {{ background: #fff !important; color: #000 !important; box-shadow: inset 0 0 0 1px #000 !important; font-style: italic; }}\n"
        f"      .gcell.block .pill {{ background: #fff !important; color: #000 !important; box-shadow: inset 0 0 0 1px #b9bfc6 !important; }}\n"
        f"      body.print-color .gcell.vacation .pill {{ background: {VACATION_BG} !important; color: {VACATION_INK} !important; box-shadow: inset 0 0 0 1px {VACATION_RULE} !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; font-style: normal; }}\n"
        f"      body.print-color .gcell.block .pill {{ background: {BLOCK_BG} !important; color: {BLOCK_INK} !important; box-shadow: inset 0 0 0 1px {BLOCK_RULE} !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}\n"
        f"    }}\n"
    )

    # Header row.
    header_html = '<div class="gh corner">Clinician</div>\n'
    for d in all_dates:
        weekend_cls = " weekend" if is_weekend(d) else ""
        header_html += f'<div class="gh{weekend_cls}">{d.strftime("%a %-d")}</div>\n'
    header_html += '<div class="gh totals col-total">Total</div>\n'
    header_html += '<div class="gh totals col-weekend">Weekend</div>\n'

    # Clinician rows.
    fingerprint_lines: list[str] = []
    rows_html = ""
    for cid in clinician_ids:
        rows_html += f'<div class="gname" data-cid="{escape(cid)}">{escape(clinicians[cid]["name"])}</div>\n'
        for d in all_dates:
            weekend_cls = " weekend" if is_weekend(d) else ""
            shifts = assignment_map.get((d, cid), [])
            hard_blocks = hard_block_map.get((d, cid), [])
            iso = d.isoformat()
            coords = f' data-date="{iso}" data-cid="{escape(cid)}"'

            if shifts:
                # data-shift carries the real shift names (so CSV export and
                # drag-and-drop stay faithful to the solver's schema); the chip
                # only *displays* the short code.
                label = " / ".join(shifts)
                code_label = " / ".join(shift_code_map[s] for s in shifts)
                cls = shift_css_class(shifts[0])
                for s in shifts:
                    fingerprint_lines.append(f"a|{iso}|{cid}|{s}")
                # data-orig-* lets the JS detect when a swap returns a cell to
                # its solver-original state so chain moves only flag the net
                # change, not every cell touched along the way.
                rows_html += (
                    f'<div class="gcell{weekend_cls} {cls}"{coords}'
                    f' draggable="true" data-shift="{escape(label)}"'
                    f' data-shift-class="{cls}"'
                    f' data-orig-shift="{escape(label)}" data-orig-cls="{cls}"'
                    f' title="Call (solver) — drag up/down to reassign within this day">'
                    f'<span class="pill">{escape(code_label)}</span></div>\n'
                )
            elif hard_blocks:
                label = " / ".join(label for label, _kind in hard_blocks)
                block_kind = "vacation" if any(kind == "vacation" for _label, kind in hard_blocks) else "block"
                for blk_label, kind in hard_blocks:
                    fingerprint_lines.append(f"b|{iso}|{cid}|{kind}|{blk_label}")
                rows_html += (
                    f'<div class="gcell{weekend_cls} {block_kind}"{coords}'
                    f' data-locked="true"><span class="pill">{escape(label)}</span></div>\n'
                )
            else:
                rows_html += f'<div class="gcell{weekend_cls} empty"{coords}></div>\n'

        target = parse_int(clinicians[cid].get("target_shifts", ""), 0)
        weekend_target = parse_int(clinicians[cid].get("target_weekend_shifts", ""), 0)
        total = totals[cid]
        weekend = weekend_totals[cid]

        cid_attr = f' data-cid="{escape(cid)}"'
        total_inner = f'{total}<span class="target">/{target}</span>' if target else f'{total}'
        rows_html += f'<div class="gtotal col-total"{cid_attr}>{total_inner}</div>\n'
        weekend_inner = f'{weekend}<span class="target">/{weekend_target}</span>' if weekend_target else f'{weekend}'
        rows_html += f'<div class="gtotal col-weekend"{cid_attr}>{weekend_inner}</div>\n'

    fingerprint = hashlib.sha256("\n".join(fingerprint_lines).encode()).hexdigest()[:16]
    # Filename slug for the "Export CSV" button. Same-month schedules
    # collapse to YYYY-MM; ones that span months use a date range so the
    # filename stays self-describing if a chief downloads it weeks later.
    if all_dates[0].strftime("%Y-%m") == all_dates[-1].strftime("%Y-%m"):
        period_slug = all_dates[0].strftime("%Y-%m")
    else:
        period_slug = f'{all_dates[0].isoformat()}_{all_dates[-1].isoformat()}'

    # Real-shift-name -> chip code, handed to the client so cells edited via
    # drag-and-drop relabel to the same code the solver used. Escape "<" so a
    # stray shift name can't break out of the <script> block.
    shift_code_json = json.dumps(shift_code_map).replace("<", "\\u003c")

    # Legend. Each swatch is a mini chip showing the code, so the legend doubles
    # as the key from code to full shift name (NIG → Night call).
    legend_items = ""
    for st in shift_types:
        cls = shift_css_class(st)
        legend_items += (
            f'<span class="lg"><span class="swatch {cls}">{escape(shift_code_map[st])}</span> '
            f'{escape(st)} call</span>\n'
        )
    legend_items += '<span class="lg"><span class="swatch vacation">VAC</span> Vacation</span>\n'
    legend_items += '<span class="lg"><span class="swatch block">OFF</span> No-call / hard block</span>\n'

    css = (
        """
    :root {
      --bg: #ffffff;
      --ink: #000000;
      --muted: #666666;
      --rule: #dddddd;
      --rule-strong: #000000;
      --weekend: #f5f5f5;
      --font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    }
    body {
      margin: 0;
      padding: 40px;
      background: var(--bg);
      color: var(--ink);
      font-family: var(--font-sans);
      font-size: 13px;
      line-height: 1.2;
    }
    *, *::before, *::after { box-sizing: border-box; }
    .container { max-width: 1600px; margin: 0 auto; }
    header {
      border-bottom: 2px solid var(--ink);
      padding-bottom: 12px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
    }
    h1 { font-size: 20px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.05em; margin: 0; }
    .subtitle { color: var(--muted); font-size: 12px; font-weight: 600; text-transform: uppercase; }

    .grid-frame { border: 1px solid var(--rule-strong); background: #fff; }
    /* padding-bottom keeps macOS overlay scrollbars from sitting on top of
       the last row when the grid is wider than the viewport. */
    .grid-frame-scroll { overflow-x: auto; padding-bottom: 14px; }
    .grid-schedule {
      display: grid;
      /* minmax(0, 1fr) lets day cells shrink to fit any viewport — a 31-day
         month at the previous 48px minimum forced horizontal scroll on most
         laptops. Chip labels stay short (3-char shift codes); on narrow
         viewports the cells just compress further rather than overflowing. */
      grid-template-columns: 140px repeat(var(--n-days), minmax(0, 1fr)) 60px 60px;
    }
    .gh {
      padding: 8px 4px;
      border-bottom: 1px solid var(--rule-strong);
      border-right: 1px solid var(--rule);
      background: #fcfcfc;
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      text-align: center;
    }
    .gh.corner { text-align: left; padding-left: 12px; position: sticky; left: 0; z-index: 3; }
    .gh.col-total { position: sticky; right: 60px; z-index: 3; }
    .gh.col-weekend { position: sticky; right: 0; z-index: 3; }
    .gh.weekend { background: var(--weekend); }

    .gname {
      display: flex;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid var(--rule);
      border-right: 2px solid var(--rule-strong);
      background: #fff;
      font-weight: 700;
      position: sticky;
      left: 0;
      z-index: 2;
    }
    .gcell {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 4px;
      border-bottom: 1px solid var(--rule);
      border-right: 1px solid var(--rule);
      /* Keep a chip label contained within its own cell. Without this, a
         shift name wider than the column (e.g. "RMC_CALL" in the dense print
         layout) renders past the cell edge and over the neighboring day.
         nowrap stops it wrapping to a second line and inflating row height. */
      overflow: hidden;
      white-space: nowrap;
    }
    .gcell.weekend { background: #fafafa; }
    .gcell.empty { color: #eee; }

    /* The chip itself. Color (background/ink/frame) is attached per shift type
       in the generated block below; this is just the shape. */
    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 30px;
      max-width: 100%;
      padding: 3px 9px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.02em;
      line-height: 1.3;
      white-space: nowrap;
    }

    /* Drag-and-drop affordances. Pills lock to their day column (vertical
       moves only); vacation/block cells reject drops. State persists in
       localStorage keyed by a fingerprint of the original solve. */
    .gcell[draggable="true"] { cursor: grab; user-select: none; }
    .gcell[draggable="true"]:active { cursor: grabbing; }
    .gcell.dragging { opacity: 0.35; }
    .gcell.drop-target { outline: 2px dashed #f59e0b; outline-offset: -3px; }
    .gcell.edited { position: relative; }
    .gcell.edited::after {
      content: '';
      position: absolute;
      top: 3px;
      right: 3px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      /* Default for cells that gained a shift (no data-orig-cls): use the
         current shift's ink. Per-shift overrides for cells that *lost* a
         shift are generated alongside the .gcell.{cls} rules below so the
         dot reflects the original shift's color. */
      background: currentColor;
      pointer-events: none;
    }
    .edits-bar {
      display: none;
      align-items: center;
      gap: 12px;
      margin-left: auto;
      padding: 4px 10px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #8a5a00;
      background: #fff7e0;
      border: 1px solid #f0d68a;
      white-space: nowrap;
    }
    .edits-bar.visible { display: inline-flex; }

    /* Once the user has made any edits, the solver-generated summary
       ("C. Johns went over target 5 vs 4") no longer matches the grid.
       Hide it so the on-screen totals are the only source of truth. */
    body.has-edits .schedule-summary { display: none; }
    .edits-bar button {
      padding: 2px 8px;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: #fff;
      color: #5a3a00;
      border: 1px solid #d6b85a;
      cursor: pointer;
    }
    .edits-bar button:hover { background: #fff1c2; }
    @media print {
      .gcell.edited::after, .gcell.drop-target { display: none !important; outline: none !important; }
      .edits-bar { display: none !important; visibility: hidden !important; }
    }

    .gtotal {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 8px 4px;
      border-bottom: 1px solid var(--rule);
      border-left: 2px solid var(--rule-strong);
      background: #fff;
      font-weight: 800;
      position: sticky;
      z-index: 2;
    }
    .gtotal.col-total { right: 60px; }
    .gtotal.col-weekend { right: 0; border-left: 1px solid var(--rule); }
    .gtotal .target { color: var(--muted); font-size: 9px; font-weight: 400; margin-left: 2px; }

    .grid-legend { display: flex; flex-wrap: wrap; gap: 20px; margin: 24px 0; font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--muted); }
    .lg { display: inline-flex; align-items: center; gap: 6px; }
    /* A swatch is a miniature chip: it carries the shift's color AND its code,
       so the legend reads "[NIG] Night call". Color is set per shift type in
       the generated block above. */
    .swatch {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 26px;
      padding: 1px 6px;
      border-radius: 999px;
      border: 1px solid #ccc;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: 0.02em;
    }

    .schedule-summary {
      margin-top: 24px;
      padding: 14px 18px;
      border: 1px solid var(--rule);
      border-left: 3px solid #1a4769;
      background: #fcfcfa;
      font-size: 13px;
    }
    .schedule-summary h2 {
      font-size: 10px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin: 0 0 8px;
      color: var(--muted);
    }
    .schedule-summary ul { margin: 0; padding-left: 18px; }
    .schedule-summary li { margin: 4px 0; line-height: 1.45; }

    /* Branding band lives INSIDE .grid-frame so a chief who tries to crop
       it off the printout has to slice into the schedule box itself. */
    .grid-watermark {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 12px;
      border-top: 1px solid var(--rule);
      background: #fcfcfc;
      color: var(--muted);
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .grid-watermark a {
      color: var(--ink);
      text-decoration: none;
      font-weight: 800;
    }

    .print-actions {
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      gap: 12px;
      /* Reserve the bar's height so it doesn't push the grid down when it
         appears after a drag. Bar = 11px font + 4px padding + 1px border ≈ 28px. */
      min-height: 28px;
    }
    .btn-subtle {
      padding: 4px 10px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: #eee;
      color: #333;
      border: 1px solid #ddd;
      cursor: pointer;
      /* Keep label on one line so the row height doesn't change when the
         edits-bar appears and squeezes horizontal space. */
      white-space: nowrap;
    }
    .btn-subtle:hover { background: #ddd; }

    @media print {
      @page { size: landscape; margin: 0.4in; }
      body { padding: 0; font-size: 9px; }
      .container { max-width: none; }
      .print-actions { display: none; }
      header { border-bottom-width: 1.5pt; margin-bottom: 12pt; }
      .grid-frame { border-width: 0.75pt; }
      /* Without this the scroll container stays at overflow-x:auto and the
         grid renders at its max-content width — the right edge (Total +
         Weekend columns and any days past ~8) falls off the page. */
      .grid-frame-scroll { overflow: visible; }
      /* Default print: hide the legend (swatches are colorless without
         print-color-adjust). In "Print in color" mode we restore it. */
      .grid-legend { display: none; }
      body.print-color .grid-legend { display: flex; }
      body.print-color .swatch {
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }
      .grid-schedule {
        grid-template-columns: 80px repeat(var(--n-days), minmax(0, 1fr)) 36px 40px;
        width: 100%;
      }
      .gh { padding: 4px 2px; font-size: 8px; border-width: 0.5pt; position: static; }
      /* Higher-specificity reset: .gh.corner / .gh.col-total / .gh.col-weekend
         have position: sticky for on-screen scrolling, which Chrome's print
         engine then anchors mid-page. Force them back to static for print. */
      .gh.corner, .gh.col-total, .gh.col-weekend { position: static; }
      .gname { padding: 4px; font-size: 8px; position: static; border-width: 0.5pt; border-right-width: 1.2pt; }
      .gcell { min-height: 20px; padding: 1px; border-width: 0.5pt; }
      /* Let chips shrink to fit a tight (e.g. 31-day) landscape page instead of
         forcing the grid wider than the paper. */
      .pill { min-width: 0; padding: 1px 4px; font-size: 8px; letter-spacing: 0; }
      .gtotal { padding: 2px; font-size: 8px; position: static; border-width: 0.5pt; border-left-width: 1.2pt; }
      .gh, .gname, .gcell, .gtotal, .gh.weekend, .gcell.weekend { background: white !important; }
      /* Summary is a chief-facing review pane — the printout is for the
         group and stays focused on the grid. */
      .schedule-summary { display: none; }
      body.print-color .grid-legend { margin: 12pt 0; }
      .grid-watermark {
        padding: 4pt 8pt;
        border-top-width: 0.5pt;
        font-size: 7pt;
        background: white !important;
      }
    }
"""
        + shift_css
        + """
    </style>"""
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Call Schedule | PostCallMD</title>
    <style>{css}</style>
</head>
<body>
    <div class="container">
        <div class="print-actions">
            <button class="btn-subtle" onclick="window.print()">Print Schedule</button>
            <button class="btn-subtle" onclick="printInColor()">Print in Color</button>
            <button class="btn-subtle" onclick="exportCsv()">Export CSV</button>
            <span class="edits-bar" id="edits-bar">
                <span id="edits-count">0 edits</span>
                <button onclick="resetEdits()">Reset to solver</button>
            </span>
        </div>
        <script>
            function printInColor() {{
                document.body.classList.add('print-color');
                window.print();
            }}
            window.addEventListener('afterprint', function () {{
                document.body.classList.remove('print-color');
            }});

            // ---- Drag-and-drop editing ----------------------------------
            // Pills lock to their day column. Drop targets are cells on the
            // same date that aren't vacation/block (data-locked). Swaps
            // exchange shift/class/text; empty targets just move the pill.
            // Final per-cell state is persisted in localStorage keyed by the
            // fingerprint below — re-running the solver invalidates edits.
            const FINGERPRINT = "{fingerprint}";
            const STORAGE_KEY = "niceschedule-edits:" + FINGERPRINT;
            const PERIOD_SLUG = "{period_slug}";
            const CODE_MAP = {shift_code_json};

            function cellKey(cell) {{
                return cell.dataset.date + "|" + cell.dataset.cid;
            }}

            // A shift's chip shows a short code; data-shift keeps the real name.
            // "OR / OB" maps part-by-part so multi-shift cells stay correct.
            function codeForShift(shift) {{
                return shift.split(' / ').map(function (s) {{
                    return CODE_MAP[s] || s;
                }}).join(' / ');
            }}

            function setPill(cell, shift) {{
                cell.textContent = '';
                const span = document.createElement('span');
                span.className = 'pill';
                span.textContent = codeForShift(shift);
                cell.appendChild(span);
            }}

            function applyCellState(cell, shift, cls) {{
                const oldCls = cell.dataset.shiftClass;
                if (oldCls) cell.classList.remove(oldCls);
                if (shift) {{
                    cell.classList.add(cls);
                    cell.classList.remove('empty');
                    setPill(cell, shift);
                    cell.setAttribute('draggable', 'true');
                    cell.dataset.shift = shift;
                    cell.dataset.shiftClass = cls;
                    cell.setAttribute('title', 'Call (edited) — drag up/down to reassign');
                }} else {{
                    cell.classList.add('empty');
                    cell.textContent = '';
                    cell.removeAttribute('draggable');
                    delete cell.dataset.shift;
                    delete cell.dataset.shiftClass;
                    cell.removeAttribute('title');
                }}
            }}

            function swapCells(src, dst) {{
                const srcShift = src.dataset.shift || '';
                const srcCls = src.dataset.shiftClass || '';
                const dstShift = dst.dataset.shift || '';
                const dstCls = dst.dataset.shiftClass || '';
                const srcCid = src.dataset.cid;
                const dstCid = dst.dataset.cid;
                applyCellState(src, dstShift, dstCls);
                applyCellState(dst, srcShift, srcCls);
                refreshEditedFlag(src);
                refreshEditedFlag(dst);
                recomputeTotals(srcCid);
                recomputeTotals(dstCid);
                saveState();
            }}

            // A cell is "edited" only if its current shift differs from what
            // the solver originally put there. Chain moves (A→B→C) leave B
            // unmarked because B is back to its original empty state.
            function refreshEditedFlag(cell) {{
                const origShift = cell.dataset.origShift || '';
                const origCls = cell.dataset.origCls || '';
                const curShift = cell.dataset.shift || '';
                const curCls = cell.dataset.shiftClass || '';
                if (curShift === origShift && curCls === origCls) {{
                    cell.classList.remove('edited');
                }} else {{
                    cell.classList.add('edited');
                }}
            }}

            // Totals (Total + Weekend columns) are baked into the HTML by the
            // solver. After a swap we recompute them client-side for the two
            // doctors whose cells changed. Multi-shift cells ("OR / OB") count
            // by the number of shifts, matching the Python aggregation.
            function recomputeTotals(cid) {{
                if (!cid) return;
                let total = 0, weekend = 0;
                document.querySelectorAll('.gcell[data-cid="' + cid + '"]').forEach(cell => {{
                    const s = cell.dataset.shift;
                    if (!s) return;
                    const n = s.split(' / ').length;
                    total += n;
                    if (cell.classList.contains('weekend')) weekend += n;
                }});
                const totalEl = document.querySelector(
                    '.gtotal.col-total[data-cid="' + cid + '"]'
                );
                const weekendEl = document.querySelector(
                    '.gtotal.col-weekend[data-cid="' + cid + '"]'
                );
                if (totalEl && totalEl.firstChild) totalEl.firstChild.nodeValue = String(total);
                if (weekendEl && weekendEl.firstChild) weekendEl.firstChild.nodeValue = String(weekend);
            }}

            function saveState() {{
                const cells = {{}};
                document.querySelectorAll('.gcell.edited[data-date]').forEach(cell => {{
                    cells[cellKey(cell)] = {{
                        shift: cell.dataset.shift || null,
                        cls: cell.dataset.shiftClass || null,
                    }};
                }});
                localStorage.setItem(STORAGE_KEY, JSON.stringify({{
                    fingerprint: FINGERPRINT, cells,
                }}));
                updateEditsBar();
            }}

            function loadState() {{
                let raw;
                try {{ raw = localStorage.getItem(STORAGE_KEY); }} catch (e) {{ return; }}
                if (!raw) return;
                let parsed;
                try {{ parsed = JSON.parse(raw); }} catch (e) {{ return; }}
                if (!parsed || parsed.fingerprint !== FINGERPRINT) {{
                    try {{ localStorage.removeItem(STORAGE_KEY); }} catch (e) {{}}
                    return;
                }}
                const touchedCids = new Set();
                Object.entries(parsed.cells || {{}}).forEach(([key, val]) => {{
                    const [date, cid] = key.split('|');
                    const cell = document.querySelector(
                        '.gcell[data-date="' + date + '"][data-cid="' + cid + '"]'
                    );
                    if (cell && cell.dataset.locked !== 'true') {{
                        applyCellState(cell, val.shift || '', val.cls || '');
                        refreshEditedFlag(cell);
                        touchedCids.add(cid);
                    }}
                }});
                touchedCids.forEach(recomputeTotals);
                updateEditsBar();
            }}

            function updateEditsBar() {{
                const bar = document.getElementById('edits-bar');
                const count = document.getElementById('edits-count');
                const edited = document.querySelectorAll('.gcell.edited').length;
                if (edited > 0) {{
                    bar.classList.add('visible');
                    count.textContent = edited + (edited === 1 ? ' edited cell' : ' edited cells');
                    document.body.classList.add('has-edits');
                }} else {{
                    bar.classList.remove('visible');
                    document.body.classList.remove('has-edits');
                }}
            }}

            function resetEdits() {{
                try {{ localStorage.removeItem(STORAGE_KEY); }} catch (e) {{}}
                location.reload();
            }}

            // Export the current grid (including any drag-and-drop edits) as
            // a CSV matching the solver's schema: date,shift_type,clinician_id,name.
            // Multi-shift cells expand to one row per shift.
            function exportCsv() {{
                const nameByCid = {{}};
                document.querySelectorAll('.gname[data-cid]').forEach(g => {{
                    nameByCid[g.dataset.cid] = g.textContent.trim();
                }});
                const rows = [['date', 'shift_type', 'clinician_id', 'name']];
                const cells = Array.from(document.querySelectorAll('.gcell[data-shift]'));
                cells.sort((a, b) => {{
                    const dc = a.dataset.date.localeCompare(b.dataset.date);
                    if (dc !== 0) return dc;
                    return a.dataset.shift.localeCompare(b.dataset.shift);
                }});
                for (const cell of cells) {{
                    const shifts = cell.dataset.shift.split(' / ');
                    for (const s of shifts) {{
                        rows.push([cell.dataset.date, s, cell.dataset.cid, nameByCid[cell.dataset.cid] || cell.dataset.cid]);
                    }}
                }}
                const csv = rows.map(r => r.map(v => {{
                    const s = String(v);
                    return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
                }}).join(',')).join('\\n');
                const blob = new Blob([csv], {{ type: 'text/csv;charset=utf-8' }});
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'schedule-' + PERIOD_SLUG + '.csv';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }}

            let dragSource = null;
            document.addEventListener('dragstart', e => {{
                const cell = e.target.closest && e.target.closest('.gcell[draggable="true"]');
                if (!cell) return;
                dragSource = cell;
                cell.classList.add('dragging');
                if (e.dataTransfer) {{
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/plain', cell.dataset.shift || '');
                }}
            }});
            document.addEventListener('dragend', () => {{
                if (dragSource) dragSource.classList.remove('dragging');
                document.querySelectorAll('.gcell.drop-target').forEach(c =>
                    c.classList.remove('drop-target'));
                dragSource = null;
            }});
            function validTarget(cell) {{
                if (!dragSource || !cell || cell === dragSource) return false;
                if (!cell.dataset.date) return false;
                if (cell.dataset.date !== dragSource.dataset.date) return false;
                if (cell.dataset.locked === 'true') return false;
                return true;
            }}
            document.addEventListener('dragover', e => {{
                const cell = e.target.closest && e.target.closest('.gcell[data-date]');
                if (!validTarget(cell)) return;
                e.preventDefault();
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
                document.querySelectorAll('.gcell.drop-target').forEach(c =>
                    c.classList.remove('drop-target'));
                cell.classList.add('drop-target');
            }});
            document.addEventListener('drop', e => {{
                const cell = e.target.closest && e.target.closest('.gcell[data-date]');
                if (!validTarget(cell)) return;
                e.preventDefault();
                swapCells(dragSource, cell);
            }});

            document.addEventListener('DOMContentLoaded', loadState);
        </script>
        <header>
            <div class="header-copy">
                <h1>Call Schedule</h1>
                <div class="subtitle">{all_dates[0].strftime('%B %-d')} &ndash; {all_dates[-1].strftime('%B %-d, %Y')}</div>
            </div>
        </header>

        <div class="grid-frame">
            <div class="grid-frame-scroll">
                <div class="grid-schedule" style="--n-days: {num_days};">
                    {header_html}
                    {rows_html}
                </div>
            </div>
            <div class="grid-watermark">
                <span>{all_dates[0].strftime('%B %-d')} &ndash; {all_dates[-1].strftime('%B %-d, %Y')}</span>
                <span>Built with <a href="https://postcallmd.com">PostCallMD</a></span>
            </div>
        </div>

        <div class="grid-legend">
            {legend_items}
        </div>

        {summary_html}
    </div>
</body>
</html>
"""
    with output_path.open("w") as f:
        f.write(html)

#!/usr/bin/env python3
"""Run the configured schedule and open the printable HTML output."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def open_html(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="config/my_rules.json",
        help="Configured rules file. Defaults to config/my_rules.json.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Run the solve without opening the HTML output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print solver diagnostics such as the objective score.",
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip the data check before the solve and the schedule check after it.",
    )
    args = parser.parse_args()

    config_path = resolve_path(root, args.config)
    if not config_path.exists():
        print(f"Missing {display_path(root, config_path)}.")
        print("Create it first:")
        print("  cp -R data/template data/my_data")
        print("  cp config/my_rules.template.json config/my_rules.json")
        return 2

    with config_path.open() as f:
        config = json.load(f)

    if not args.skip_check:
        check_result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "check_my_data.py"),
                "--config",
                str(config_path),
            ],
            check=False,
        )
        if check_result.returncode != 0:
            return check_result.returncode

    solve_cmd = [sys.executable, str(root / "solver.py"), "--config", str(config_path)]
    if args.verbose:
        solve_cmd.append("--verbose")

    result = subprocess.run(solve_cmd, check=False)
    if result.returncode != 0:
        return result.returncode

    if not args.skip_check:
        # Check every hard rule again on the output, from the input files.
        schedule_check = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "check_schedule.py"),
                "--config",
                str(config_path),
            ],
            check=False,
        )
        if schedule_check.returncode != 0:
            return schedule_check.returncode

    base_dir = config_path.parent.parent
    output_csv = resolve_path(base_dir, config.get("output_csv", "output/schedule.csv"))
    html_path = output_csv.with_suffix(".html")
    if not html_path.exists():
        print(f"Solve finished, but no HTML output was found at {html_path}.")
        return 0

    if args.no_open:
        print(f"Printable schedule: {html_path}")
    else:
        open_html(html_path)
        print(f"Opened printable schedule: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

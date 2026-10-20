#!/usr/bin/env bash
# One-shot for macOS and Linux: set up the environment, solve the built-in
# sample, and open the result. The work is in scripts/quickstart.py, which
# also runs on Windows (py -3 scripts/quickstart.py). Safe to re-run; the
# install is skipped when it is already in place.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/quickstart.py "$@"

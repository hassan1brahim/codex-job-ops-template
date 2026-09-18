#!/bin/bash
# Hourly discovery, including a capped and throttled LinkedIn pass.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="${JOBBOARD_PYTHON:-/usr/bin/python3}"
cd "$REPO" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') jobboard hourly ==="
"$PY" scripts/jobboard.py refresh --sources ats simplify vansh linkedin --linkedin-pages 2 || echo "refresh failed"
"$PY" scripts/jobboard.py autoqueue --limit 5 --min-score 78 --tier 3 --max-age-hours 2 || echo "autoqueue failed"
# Process at most one genuinely recent role; never drain the older queue hourly.
"$PY" scripts/jobboard.py process --limit 1 --prefer-new-hours 2 --max-age-hours 2 || echo "fresh Codex processing paused or failed"
"$PY" scripts/jobboard.py render || echo "render failed"
"$PY" scripts/jobboard.py notify --tier 3 || echo "notify failed"
echo "=== done $(date '+%H:%M:%S') ==="

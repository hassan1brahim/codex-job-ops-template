#!/bin/bash
# Daily board run: pull sources, fetch descriptions for the closest jobs, notify.
# Kept as a shell script rather than three launchd jobs so the order is guaranteed
# and a failure in one step does not silently skip the notification.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="${JOBBOARD_PYTHON:-/usr/bin/python3}"
cd "$REPO" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') jobboard daily ==="

"$PY" scripts/jobboard.py refresh || echo "refresh failed"

# Descriptions only for the configured preferred/remote tiers. Rate limited,
# so this is the slow step: roughly one job every 2-4 seconds.
"$PY" scripts/jobboard.py describe --limit 40 --tier 2 || echo "describe failed"

# Automatically hand only the strongest new roles to the existing Codex queue.
"$PY" scripts/jobboard.py autoqueue --limit 8 --min-score 75 --tier 3 --max-age-hours 14 || echo "autoqueue failed"
"$PY" scripts/jobboard.py check-urls --limit 30 --older-than 24 || echo "URL check failed"

# One isolated Codex worker evaluates the top queued role. It never submits an application.
"$PY" scripts/jobboard.py process --limit 1 || echo "Codex processing paused or failed"

"$PY" scripts/jobboard.py render || echo "render failed"

# Notify last, so the banner only fires once the board behind it is current.
"$PY" scripts/jobboard.py notify --tier 3 || echo "notify failed"

echo "=== done $(date '+%H:%M:%S') ==="

#!/bin/bash
# Install (or reinstall) hourly discovery and twice-daily deep processing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL_PREFIX="com.$(id -un).codex-job-ops"
LABEL="$LABEL_PREFIX.jobboard"
HOURLY_LABEL="$LABEL_PREFIX.jobboard-hourly"
SERVER_LABEL="$LABEL_PREFIX.board-server"
ARCHIVE_LABEL="$LABEL_PREFIX.resume-archive"

chmod +x "$REPO/scripts/jobboard_daily.sh" "$REPO/scripts/jobboard_hourly.sh"
mkdir -p "$HOME/Library/LaunchAgents"

for job_label in "$LABEL" "$HOURLY_LABEL" "$SERVER_LABEL" "$ARCHIVE_LABEL"; do
    suffix="${job_label#"$LABEL_PREFIX."}"
    src="$REPO/scripts/com.user.codex-job-ops.$suffix.plist"
    dest="$HOME/Library/LaunchAgents/$job_label.plist"
    launchctl bootout "gui/$(id -u)/$job_label" 2>/dev/null || true
    sed -e "s|__REPO_PATH__|$REPO|g" -e "s|__LABEL_PREFIX__|$LABEL_PREFIX|g" "$src" > "$dest"
    launchctl bootstrap "gui/$(id -u)" "$dest"
done

echo "Installed hourly discovery (including capped LinkedIn) plus deep runs at 08:30 and 17:30."
echo "  status:    launchctl print gui/$(id -u)/$LABEL | head -20"
echo "  run now:   launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  logs:      tail -f /tmp/codex-job-ops-jobboard.log"
echo "  hourly:    launchctl print gui/$(id -u)/$HOURLY_LABEL | head -20"
echo "  frontend:  http://localhost:8765"
echo "             launchctl print gui/$(id -u)/$SERVER_LABEL | head -20"
echo "  remove:    launchctl bootout gui/$(id -u)/$LABEL"
echo "             launchctl bootout gui/$(id -u)/$HOURLY_LABEL"
echo "             launchctl bootout gui/$(id -u)/$SERVER_LABEL"
echo
echo "macOS will ask once for permission to send notifications. Approve it, or"
echo "notifications will fail silently from then on."

"""macOS notifications for new high-priority postings.

Only jobs at or above a tier threshold notify, so a Bangalore listing never
interrupts you. Every notified job is marked so the same role is announced once.
"""

from __future__ import annotations

import shutil
import subprocess
import time

from .geo import TIER_LABELS
from .store import mark_notified, unnotified


def _osascript(title: str, subtitle: str, message: str) -> bool:
    def esc(text: str) -> str:
        return (text or "").replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{esc(message)}" '
        f'with title "{esc(title)}" subtitle "{esc(subtitle)}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=True,
                       capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def send(conn, max_tier: int = 3, limit: int = 8, dry_run: bool = False) -> dict:
    """Notify about new jobs at or better than `max_tier`."""
    rows = unnotified(conn, max_tier=max_tier)
    if not rows:
        return {"new": 0, "sent": 0, "keys": []}

    top = rows[:limit]
    if dry_run:
        for row in top:
            print(f'  [{row["priority_score"]}] {row["company"]} - {row["title"]}')
        return {"new": len(rows), "sent": 0, "keys": []}

    sent = 0
    if shutil.which("osascript"):
        # One summary notification, then the top few individually. A macOS
        # notification has no room for a list, and 40 separate banners is worse
        # than useless.
        by_tier: dict[int, int] = {}
        for row in rows:
            by_tier[row["tier"]] = by_tier.get(row["tier"], 0) + 1
        breakdown = ", ".join(
            f"{count} {TIER_LABELS[tier]}" for tier, count in sorted(by_tier.items())
        )
        if _osascript("Job Board", f"{len(rows)} new internships", breakdown):
            sent += 1
        for row in top[:3]:
            stamp = row["date_posted"] or row["first_seen"]
            age_m = max(1, round((time.time() - stamp) / 60))
            age = f"{age_m}m ago" if age_m < 60 else f"{round(age_m / 60)}h ago"
            subtitle = f'{row["priority_score"]} — {row["title"][:52]}'
            message = f'{row["place_label"] or TIER_LABELS[row["tier"]]} · posted {age}'
            if row["status"] in {"EVALUATING", "RESUME_GENERATING"}:
                message += " · resume queued"
            if _osascript(row["company"], subtitle, message):
                sent += 1

    keys = [row["key"] for row in rows]
    mark_notified(conn, keys)
    return {"new": len(rows), "sent": sent, "keys": keys}

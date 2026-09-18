"""Work-authorization compatibility checks driven only by profile.yml facts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "config" / "profile.yml"


def _value(name: str) -> str:
    if not PROFILE.exists():
        return "unknown"
    hit = re.search(rf"^\s*{re.escape(name)}:\s*[\"']?([^\n\"']+)", PROFILE.read_text(), re.M)
    return hit.group(1).strip().lower() if hit else "unknown"


def profile_summary() -> str:
    authorization = _value("work_authorization")
    sponsorship = _value("visa_sponsorship_needed")
    if "unknown" in {authorization, sponsorship} or not authorization or not sponsorship:
        return "Work authorization is unknown; sponsorship compatibility is not affecting rank yet."
    return f"Work authorization: {authorization}; sponsorship needed: {sponsorship}."


def compatibility(job_sponsorship: str | None, description: str | None) -> tuple[bool | None, str | None]:
    authorization = _value("work_authorization")
    needs = _value("visa_sponsorship_needed")
    if "unknown" in {authorization, needs} or not authorization or not needs:
        return None, None
    text = f"{job_sponsorship or ''} {description or ''}".lower()
    refuses = bool(re.search(
        r"does not offer sponsorship|no (?:visa )?sponsorship|without (?:current or future )?sponsorship|"
        r"not (?:eligible|available) for sponsorship",
        text,
    ))
    needs_sponsorship = needs in {"yes", "true", "required", "needed"}
    if needs_sponsorship and refuses:
        return False, "Sponsorship requirements appear incompatible"
    return True, None

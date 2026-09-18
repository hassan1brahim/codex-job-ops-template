"""Deterministic priority scoring for discovered jobs.

The score is intentionally explainable.  It is a triage score, not a fit
evaluation: Codex still owns the evidence-based fit report and resume work.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass


WEIGHTS = {
    "location": 0.35,
    "freshness": 0.25,
    "role": 0.20,
    "skills": 0.10,
    "company": 0.05,
    "friction": 0.05,
}

SKILLS = {
    "Python": r"\bpython\b",
    "Java": r"\bjava\b",
    "TypeScript": r"\btypescript\b",
    "JavaScript": r"\bjavascript\b",
    "React": r"\breact(?:\.js)?\b",
    "Node.js": r"\bnode(?:\.js|js)?\b",
    "SQL": r"\b(?:sql|postgres(?:ql)?)\b",
    "AWS": r"\baws\b|amazon web services",
    "AI/ML": r"\b(?:ai|machine learning|pytorch|scikit-learn|llm|rag)\b",
    "Backend": r"\b(?:backend|back-end|api|rest|graphql)\b",
}

ATS_EASY = ("greenhouse", "lever.co", "ashbyhq")
ATS_HARD = ("myworkdayjobs", "oraclecloud", "successfactors")


@dataclass(frozen=True)
class Score:
    priority: int
    location: int
    freshness: int
    role: int
    skills: int
    company: int
    friction: int
    reasons: list[str]


def freshness_score(date_posted: int | None, first_seen: int, now: int | None = None) -> int:
    """Score source recency; discovery time must not impersonate posting time."""
    if not date_posted:
        return 20
    current = now or int(time.time())
    age_hours = max(0.0, (current - date_posted) / 3600)
    # Smooth decay: 100 now, ~84 at one day, ~49 at four days, ~17 at 10 days.
    return max(5, min(100, round(100 * math.exp(-age_hours / (24 * 5.5)))))


def location_score(tier: int, distance_mi: float | None, place: str) -> int:
    if tier == 0:
        return 100
    if tier == 1:
        return 90
    if tier == 2:
        return 74
    if tier == 3:
        distance = distance_mi if distance_mi is not None else 1500
        return max(30, round(72 - min(distance, 3000) / 75))
    if tier == 4:
        return 8
    return 35


def role_score(title: str) -> int:
    text = (title or "").lower()
    if re.search(r"\b(?:software|backend|back-end|full.?stack)\b", text):
        return 100
    if re.search(r"\b(?:machine learning|artificial intelligence|ai engineer)\b", text):
        return 94
    if re.search(r"\b(?:data engineer|data science|developer|programmer)\b", text):
        return 86
    if re.search(r"\b(?:security|cloud|devops|automation)\b", text):
        return 76
    return 55


def skill_score(title: str, description: str | None) -> tuple[int, list[str]]:
    text = f"{title}\n{description or ''}"
    matches = [name for name, pattern in SKILLS.items() if re.search(pattern, text, re.I)]
    # No JD should be neutral rather than punished: it has not been evaluated yet.
    if not description:
        return 55, matches
    return min(100, 35 + len(matches) * 11), matches


def company_score(company: str, title: str, description: str | None) -> int:
    text = f"{company} {title} {description or ''}".lower()
    preferred = ("developer tool", "education", "edtech", "artificial intelligence", " ai ")
    return 85 if any(term in f" {text} " for term in preferred) else 60


def friction_score(url: str, ats_vendor: str | None = None) -> int:
    text = f"{ats_vendor or ''} {url or ''}".lower()
    if any(term in text for term in ATS_EASY):
        return 100
    if any(term in text for term in ATS_HARD):
        return 35
    if "linkedin.com" in text:
        return 45
    return 65


def score_job(job, now: int | None = None) -> Score:
    """Score either a sqlite Row, dict, or Job-like object."""
    def get(name, default=None):
        if isinstance(job, dict):
            return job.get(name, default)
        try:
            return job[name]
        except (KeyError, IndexError, TypeError):
            return getattr(job, name, default)

    loc = location_score(get("tier", 5), get("distance_mi"), get("place_label", "") or "")
    fresh = freshness_score(get("date_posted"), get("first_seen") or now or int(time.time()), now)
    role = role_score(get("title", ""))
    skills, skill_names = skill_score(get("title", ""), get("description"))
    company = company_score(get("company", ""), get("title", ""), get("description"))
    friction = friction_score(get("url", ""), get("ats_vendor"))
    parts = {
        "location": loc, "freshness": fresh, "role": role,
        "skills": skills, "company": company, "friction": friction,
    }
    total = round(sum(parts[name] * weight for name, weight in WEIGHTS.items()))

    applicants = get("applicant_count")
    if applicants is not None:
        if applicants >= 200:
            total -= 10
        elif applicants >= 100:
            total -= 7
        elif applicants >= 50:
            total -= 4

    reasons = []
    place = get("place_label", "") or ""
    if loc >= 90:
        reasons.append(f"{place or 'Preferred location'}")
    elif get("tier") == 2:
        reasons.append("Remote US")
    if get("date_posted"):
        age_h = max(0, ((now or int(time.time())) - get("date_posted")) / 3600)
        if age_h < 1:
            reasons.append("Posted less than an hour ago")
        elif age_h < 24:
            reasons.append(f"Posted {round(age_h)}h ago")
        elif age_h < 24 * 7:
            reasons.append(f"Posted {round(age_h / 24)}d ago")
    else:
        reasons.append("Posting date unavailable")
    if applicants is not None and applicants >= 50:
        reasons.append(f"{applicants}+ LinkedIn applicants")
    if role >= 86:
        reasons.append("Strong role match")
    if skill_names:
        reasons.append("Skills: " + ", ".join(skill_names[:4]))
    if friction >= 90:
        reasons.append("Low-friction ATS application")

    from .authorization import compatibility
    compatible, auth_reason = compatibility(get("sponsorship"), get("description"))
    if compatible is False:
        total = min(total, 35)
        reasons.insert(0, auth_reason)

    return Score(total, loc, fresh, role, skills, company, friction, reasons[:5])

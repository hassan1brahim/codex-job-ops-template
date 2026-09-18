"""vanshb03 community internship mirror.

An independently maintained list with the same JSON shape as Simplify but a
different contributor pool, so it surfaces roles Simplify has not picked up.
Uses `season` where Simplify uses `terms`, and carries no `category`.
"""

from __future__ import annotations

from ..filters import keep_job
from ..geo import best_place
from ..store import Job
from .simplify import _get_json

# Summer2026-Internships was RENAMED to Summer2027-Internships, and GitHub
# redirects the old raw URL to the new repo. Listing both fetched the identical
# payload twice (same SHA-256), doubling `fetched` while adding no new rows.
REPOS = [
    "vanshb03/Summer2027-Internships",
]
PATH = ".github/scripts/listings.json"


def fetch(cfg) -> "object":
    from . import SourceResult

    seen: dict[str, Job] = {}
    fetched = 0
    errors = []
    for repo in REPOS:
        url = f"https://raw.githubusercontent.com/{repo}/dev/{PATH}"
        try:
            rows = _get_json(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{repo}: {exc}")
            continue
        fetched += len(rows)
        for raw in rows:
            if not raw.get("active") or not raw.get("is_visible", True):
                continue
            locations = raw.get("locations") or []
            place = best_place(locations)
            season = raw.get("season")
            job = Job(
                source="vansh",
                company=raw.get("company_name") or "",
                title=raw.get("title") or "",
                url=raw.get("url") or "",
                locations=locations,
                category=raw.get("category"),
                terms=raw.get("terms") or ([season] if season else []),
                sponsorship=raw.get("sponsorship"),
                date_posted=raw.get("date_posted"),
                tier=place.tier,
                distance_mi=place.distance_mi,
                place_label=place.display(),
            )
            if job.company and job.title and job.url and keep_job(job, cfg):
                seen.setdefault(job.key, job)

    if not seen and errors:
        return SourceResult("vansh", False, [], fetched, "; ".join(errors))
    note = f"{len(REPOS) - len(errors)}/{len(REPOS)} repos"
    if errors:
        note += f"; errors: {'; '.join(errors)}"
    return SourceResult("vansh", True, list(seen.values()), fetched, note)

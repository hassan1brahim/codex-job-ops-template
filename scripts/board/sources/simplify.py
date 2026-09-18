"""SimplifyJobs / Pitt CSC internship repos.

The README tables everyone reads are generated from a structured
`.github/scripts/listings.json`, which is what we consume. Roughly 17k rows of
which ~4k are live at any time.
"""

from __future__ import annotations

import json
import urllib.request

from ..filters import keep_job
from ..geo import best_place, sort_key
from ..store import Job

# Repos follow a season naming convention; several cycles stay live at once.
REPOS = [
    "SimplifyJobs/Summer2027-Internships",
    "SimplifyJobs/Summer2026-Internships",
]
PATH = ".github/scripts/listings.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


def _get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _to_job(raw: dict, source: str) -> Job | None:
    if not raw.get("active") or not raw.get("is_visible", True):
        return None
    locations = raw.get("locations") or []
    place = best_place(locations)
    job = Job(
        source=source,
        company=raw.get("company_name") or "",
        title=raw.get("title") or "",
        url=raw.get("url") or "",
        locations=locations,
        category=raw.get("category"),
        terms=raw.get("terms") or ([raw["season"]] if raw.get("season") else []),
        sponsorship=raw.get("sponsorship"),
        date_posted=raw.get("date_posted"),
        tier=place.tier,
        distance_mi=place.distance_mi,
        place_label=place.display(),
    )
    return job if job.company and job.title and job.url else None


def fetch(cfg) -> "object":
    from . import SourceResult

    seen: dict[str, Job] = {}
    fetched = 0
    errors = []
    for repo in REPOS:
        url = f"https://raw.githubusercontent.com/{repo}/dev/{PATH}"
        try:
            rows = _get_json(url)
        except Exception as exc:  # noqa: BLE001 - one dead repo must not kill the run
            errors.append(f"{repo}: {exc}")
            continue
        fetched += len(rows)
        for raw in rows:
            job = _to_job(raw, "simplify")
            if job and keep_job(job, cfg):
                seen.setdefault(job.key, job)

    if not seen and errors:
        return SourceResult("simplify", False, [], fetched, "; ".join(errors))
    jobs = sorted(seen.values(), key=lambda j: sort_key(best_place(j.locations)))
    note = f"{len(REPOS) - len(errors)}/{len(REPOS)} repos"
    if errors:
        note += f"; errors: {'; '.join(errors)}"
    return SourceResult("simplify", True, jobs, fetched, note)

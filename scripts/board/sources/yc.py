"""Y Combinator internships from the public structured internships page.

The page embeds the listings in an Inertia ``data-page`` payload.  Treat an
HTTP rejection, a missing/changed payload, or a zero-row payload as a failed
source run so the existing source-health UI reports YC as BLOCKED instead of
silently claiming there are no internships.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

from ..filters import keep_job
from ..geo import best_place
from ..store import Job

URL = "https://www.ycombinator.com/internships"
UA = "Mozilla/5.0 (compatible; codex-job-ops/1.0; personal job monitor)"
DATA_PAGE = re.compile(r'<div[^>]+data-page="(.*?)"', re.S)


def _get(url: str = URL, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _payload(markup: str) -> dict:
    match = DATA_PAGE.search(markup)
    if not match:
        raise ValueError("embedded data-page payload missing")
    try:
        page = json.loads(html.unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        raise ValueError("embedded data-page payload is invalid JSON") from exc
    props = page.get("props")
    if not isinstance(props, dict) or not isinstance(props.get("jobPostings"), list):
        raise ValueError("jobPostings schema missing from data-page payload")
    return props


def _locations(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(" / ") if part.strip()]


def _to_job(raw: dict, season: str, cfg) -> Job | None:
    locations = _locations(raw.get("location"))
    place = best_place(locations)
    path = raw.get("url") or ""
    url = urllib.parse.urljoin("https://www.ycombinator.com", path)
    job = Job(
        source="yc",
        company=raw.get("companyName") or "",
        title=raw.get("title") or "",
        url=url,
        locations=locations,
        category=raw.get("prettyRole") or raw.get("roleSpecificType"),
        terms=[season] if season else [],
        sponsorship=raw.get("visa"),
        date_posted=None,
        tier=place.tier,
        distance_mi=place.distance_mi,
        place_label=place.display(),
        ats_vendor="yc",
        ats_board="internships",
        ats_requisition_id=str(raw.get("id") or ""),
    )
    if not job.company or not job.title or not job.url:
        return None
    return job if keep_job(job, cfg) else None


def fetch(cfg):
    from . import SourceResult

    try:
        props = _payload(_get())
    except Exception as exc:  # noqa: BLE001 - source health must retain the reason
        return SourceResult("yc", False, [], 0, f"access/schema failure: {exc}")

    rows = props["jobPostings"]
    if not rows:
        return SourceResult(
            "yc", False, [], 0,
            "public page returned zero postings; possible access block or schema change",
        )

    season = str(props.get("season") or "").strip()
    jobs = [job for raw in rows if (job := _to_job(raw, season, cfg))]
    note = f"public data-page; {len(rows)} postings"
    if season:
        note += f"; season {season}"
    return SourceResult("yc", True, jobs, len(rows), note)

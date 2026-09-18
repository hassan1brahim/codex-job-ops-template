"""LinkedIn via the public guest job-search endpoint.

The guest endpoint serves job cards as HTML without a login. It is rate limited
and will start returning 429 under load, so this source is deliberately slow and
capped: it is a supplement to the GitHub feeds, not the backbone.

`f_E=1` restricts to internship experience level, which is what keeps full-time
roles out of the board.
"""

from __future__ import annotations

import html
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from ..filters import keep_job
from ..config import board_config
from ..geo import best_place
from ..store import Job

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# Searched in order; earlier geographies matter more, matching board ranking.
DEFAULT_QUERIES = [tuple(query) for query in board_config()["linkedin_queries"]]

CARD_RE = re.compile(r'<li>\s*<div class="base-card[^"]*"(.*?)</li>', re.S)
URN_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
TITLE_RE = re.compile(r'class="base-search-card__title"[^>]*>(.*?)</h3>', re.S)
COMPANY_RE = re.compile(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>.*?>(.*?)</a>', re.S)
LOCATION_RE = re.compile(r'class="job-search-card__location"[^>]*>(.*?)</span>', re.S)
LINK_RE = re.compile(r'class="base-card__full-link"[^>]*href="([^"?]+)')
DESC_RE = re.compile(r'show-more-less-html__markup[^>]*>(.*?)</div>', re.S)
DATE_RE = re.compile(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"')
POSTED_RE = re.compile(r'posted-time-ago__text[^>]*>(.*?)</span>', re.S)
APPLICANTS_RE = re.compile(r'num-applicants__caption[^>]*>(.*?)</figcaption>', re.S)


@dataclass(frozen=True)
class LinkedInDetail:
    description: str | None
    date_posted: int | None
    applicant_count: int | None


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", raw or "")).strip()


def _get(url: str, timeout: int = 25) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("rate limited (429)") from exc
        return None
    except Exception:  # noqa: BLE001
        return None


def _date_timestamp(value: str) -> int | None:
    try:
        return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _relative_timestamp(value: str, now: int | None = None) -> int | None:
    text = _clean(value).lower()
    match = re.search(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", text)
    if not match:
        return None
    amount = int(match.group(1))
    seconds = {"minute": 60, "hour": 3600, "day": 86400,
               "week": 604800, "month": 2592000}[match.group(2)]
    return (now or int(time.time())) - amount * seconds


def _parse_cards(body: str) -> list[Job]:
    jobs: list[Job] = []
    for chunk in CARD_RE.findall(body):
        urn = URN_RE.search(chunk)
        title = TITLE_RE.search(chunk)
        company = COMPANY_RE.search(chunk)
        location = LOCATION_RE.search(chunk)
        link = LINK_RE.search(chunk)
        posted = DATE_RE.search(chunk)
        if not (title and company):
            continue
        loc = _clean(location.group(1)) if location else ""
        place = best_place([loc] if loc else [])
        url = html.unescape(link.group(1)) if link else ""
        if not url and urn:
            url = f"https://www.linkedin.com/jobs/view/{urn.group(1)}"
        jobs.append(
            Job(
                source="linkedin",
                company=_clean(company.group(1)),
                title=_clean(title.group(1)),
                url=url,
                locations=[loc] if loc else [],
                category=None,
                terms=[],
                sponsorship=None,
                date_posted=_date_timestamp(posted.group(1)) if posted else None,
                tier=place.tier,
                distance_mi=place.distance_mi,
                place_label=place.display(),
            )
        )
    return jobs


def fetch_detail(job_url: str) -> LinkedInDetail:
    """Pull JD text plus visible age/applicant metadata from a LinkedIn posting."""
    match = re.search(r"/jobs/view/(?:.*-)?(\d{6,})", job_url or "")
    if not match:
        return LinkedInDetail(None, None, None)
    body = _get(JOB.format(job_id=match.group(1)))
    if not body:
        return LinkedInDetail(None, None, None)
    hit = DESC_RE.search(body)
    text = _clean(hit.group(1)) if hit else None
    posted = POSTED_RE.search(body)
    applicants = APPLICANTS_RE.search(body)
    applicant_count = None
    if applicants:
        number = re.search(r"([\d,]+)", _clean(applicants.group(1)))
        if number:
            applicant_count = int(number.group(1).replace(",", ""))
    if text:
        text = re.sub(r"\s{2,}", " ", text)
    return LinkedInDetail(
        text or None,
        _relative_timestamp(posted.group(1)) if posted else None,
        applicant_count,
    )


def fetch_description(job_url: str) -> str | None:
    """Backward-compatible description-only LinkedIn fetch."""
    text = fetch_detail(job_url).description
    if not text:
        return None
    text = re.sub(r"\s{2,}", " ", text)
    return text or None


def fetch(cfg) -> "object":
    from . import SourceResult

    queries = getattr(cfg, "linkedin_queries", None) or DEFAULT_QUERIES
    pages = max(1, int(getattr(cfg, "linkedin_pages", 2)))
    seen: dict[str, Job] = {}
    fetched = 0
    rate_limited = False

    for keywords, location in queries:
        for page in range(pages):
            params = urllib.parse.urlencode(
                {
                    "keywords": keywords,
                    "location": location,
                    "f_E": "1",          # internship experience level
                    "f_TPR": "r2592000",  # posted in the last 30 days
                    "start": page * 25,
                }
            )
            try:
                body = _get(f"{SEARCH}?{params}")
            except RuntimeError:
                rate_limited = True
                body = None
            if not body:
                break
            cards = _parse_cards(body)
            fetched += len(cards)
            if not cards:
                break
            for job in cards:
                if keep_job(job, cfg):
                    seen.setdefault(job.key, job)
            # Be a polite guest; this endpoint throttles aggressively.
            time.sleep(random.uniform(1.5, 3.0))
        if rate_limited:
            break

    note = f"{len(queries)} queries x {pages}p"
    if rate_limited:
        note += "; stopped early (429)"
    ok = bool(seen) or not rate_limited
    return SourceResult("linkedin", ok, list(seen.values()), fetched, note)

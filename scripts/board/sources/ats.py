"""Direct Greenhouse, Lever, and Ashby discovery.

Board identifiers are learned from URLs already in the local database.  This
turns aggregator discoveries into company-level monitors without maintaining a
second, stale company list.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

from ..filters import keep_job
from ..geo import best_place
from ..store import DB_PATH, Job

UA = "codex-job-ops/1.0 (personal job monitor)"


def _json(url: str, timeout: int = 30):
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _timestamp(value) -> int | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        value = value / 1000 if value > 10_000_000_000 else value
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def _plain(markup: str | None) -> str | None:
    if not markup:
        return None
    text = html.unescape(markup)
    text = re.sub(r"<\s*(?:br|/p|/li|/div|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def discover_boards(db_path=DB_PATH) -> dict[tuple[str, str, str], str]:
    """Return {(vendor, region, token): company} from known posting URLs."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT company, url FROM jobs WHERE url <> ''").fetchall()
    conn.close()
    boards: dict[tuple[str, str, str], str] = {}
    for company, url in rows:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if not parts:
            continue
        if "greenhouse.io" in host:
            boards.setdefault(("greenhouse", "eu" if ".eu." in host else "global", parts[0]), company)
        elif host in {"jobs.lever.co", "jobs.eu.lever.co"}:
            boards.setdefault(("lever", "eu" if host.startswith("jobs.eu") else "global", parts[0]), company)
        elif host == "jobs.ashbyhq.com":
            boards.setdefault(("ashby", "global", parts[0]), company)
    return boards


def _greenhouse(token: str, company: str, region: str) -> list[Job]:
    host = "boards-api.greenhouse.io"
    data = _json(f"https://{host}/v1/boards/{urllib.parse.quote(token)}/jobs?content=true")
    jobs = []
    for raw in data.get("jobs", []):
        location = (raw.get("location") or {}).get("name") or ""
        place = best_place([location] if location else [])
        jobs.append(Job(
            source="ats", company=raw.get("company_name") or company,
            title=raw.get("title") or "", url=raw.get("absolute_url") or "",
            locations=[location] if location else [], terms=[],
            date_posted=_timestamp(raw.get("first_published") or raw.get("updated_at")),
            tier=place.tier, distance_mi=place.distance_mi, place_label=place.display(),
            description=_plain(raw.get("content")), ats_vendor="greenhouse",
            ats_board=token, ats_requisition_id=str(raw.get("id") or ""),
        ))
    return jobs


def _lever(token: str, company: str, region: str) -> list[Job]:
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    data = _json(f"https://{host}/v0/postings/{urllib.parse.quote(token)}?mode=json")
    jobs = []
    for raw in data:
        categories = raw.get("categories") or {}
        locations = categories.get("allLocations") or []
        if not locations and categories.get("location"):
            locations = [categories["location"]]
        place = best_place(locations)
        jobs.append(Job(
            source="ats", company=company, title=raw.get("text") or "",
            url=raw.get("hostedUrl") or raw.get("applyUrl") or "", locations=locations,
            category=categories.get("team"), terms=[categories.get("commitment") or ""],
            date_posted=None, tier=place.tier, distance_mi=place.distance_mi,
            place_label=place.display(), description=(raw.get("descriptionPlain") or "").strip() or None,
            ats_vendor="lever", ats_board=token, ats_requisition_id=str(raw.get("id") or ""),
        ))
    return jobs


def _ashby(token: str, company: str, region: str) -> list[Job]:
    data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(token)}")
    jobs = []
    for raw in data.get("jobs", []):
        if raw.get("isListed") is False:
            continue
        locations = [raw.get("location") or ""]
        locations += [x.get("location") or "" for x in raw.get("secondaryLocations") or []]
        locations = [x for x in locations if x]
        if raw.get("isRemote") and not any("remote" in x.lower() for x in locations):
            locations.insert(0, "Remote (US)")
        place = best_place(locations)
        jobs.append(Job(
            source="ats", company=company, title=raw.get("title") or "",
            url=raw.get("jobUrl") or raw.get("applyUrl") or "", locations=locations,
            category=raw.get("department") or raw.get("team"),
            terms=[raw.get("employmentType") or ""], date_posted=_timestamp(raw.get("publishedAt")),
            tier=place.tier, distance_mi=place.distance_mi, place_label=place.display(),
            description=(raw.get("descriptionPlain") or "").strip() or _plain(raw.get("descriptionHtml")),
            ats_vendor="ashby", ats_board=token, ats_requisition_id=str(raw.get("id") or ""),
        ))
    return jobs


FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def fetch(cfg) -> "object":
    from . import SourceResult

    boards = discover_boards()
    seen: dict[str, Job] = {}
    errors = []
    successful_boards: set[tuple[str, str]] = set()
    fetched = 0
    for (vendor, region, token), company in sorted(boards.items()):
        try:
            rows = FETCHERS[vendor](token, company, region)
        except Exception as exc:  # one tenant must not stop all direct feeds
            errors.append(f"{vendor}:{token}: {exc}")
            continue
        successful_boards.add((vendor, token))
        fetched += len(rows)
        for job in rows:
            if job.company and job.title and job.url and keep_job(job, cfg):
                seen.setdefault(job.key, job)
    ok = bool(seen) or not errors
    note = f"{len(boards) - len(errors)}/{len(boards)} boards"
    if errors:
        note += f"; {len(errors)} failed; first: {errors[0][:120]}"
    return SourceResult("ats", ok, list(seen.values()), fetched, note, successful_boards)

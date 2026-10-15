"""Direct company job-board discovery.

Board identifiers are learned from URLs already in the local database.  This
turns aggregator discoveries into company-level monitors; `config/companies.json`
adds big employers no aggregator links to (see career_sites.py).
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ..filters import keep_job
from ..geo import best_place
from ..store import DB_PATH, Job, link_id
from .career_sites import FETCHERS as CAREER_SITE_FETCHERS, load_companies

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
    """Return boards learned from known US posting URLs.

    A board is enrolled only after an aggregator has shown a US posting from it
    (tier 0-3). This broadens first-party coverage without crawling
    international-only boards. Workday tokens encode host/tenant/site.
    """
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    where = "WHERE url <> '' AND tier <= 3" if "tier" in columns else "WHERE url <> ''"
    rows = conn.execute(f"SELECT company, url FROM jobs {where}").fetchall()
    conn.close()
    boards: dict[tuple[str, str, str], str] = {}
    for company, url in rows:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if not parts:
            continue
        if "greenhouse.io" in host:
            token = parts[0]
            if token == "embed":
                # boards.greenhouse.io/embed/job_app is a shared application widget path
                # used by every Greenhouse tenant; the board slug rides in ?for=, not the
                # path. Without it (older widget links carry only a numeric job ?token=)
                # there is no board to learn from this URL.
                slug = urllib.parse.parse_qs(parsed.query).get("for", [None])[0]
                if not slug:
                    continue
                token = slug
            boards.setdefault(("greenhouse", "eu" if ".eu." in host else "global", token), company)
        elif host in {"jobs.lever.co", "jobs.eu.lever.co"}:
            boards.setdefault(("lever", "eu" if host.startswith("jobs.eu") else "global", parts[0]), company)
        elif host == "jobs.ashbyhq.com":
            boards.setdefault(("ashby", "global", parts[0]), company)
        elif re.search(r"\.(?:wd\d+\.myworkdayjobs|myworkdaysite)\.com$", host):
            if "myworkdaysite.com" in host:
                if len(parts) < 3 or parts[0].lower() != "recruiting":
                    continue
                tenant, site = parts[1], parts[2]
            else:
                tenant = host.split(".", 1)[0]
                clean = [
                    p for p in parts
                    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", p, flags=re.I)
                ]
                if not clean:
                    continue
                site = clean[0]
            token = "|".join((host, tenant, site))
            boards.setdefault(("workday", "us", token), company)
        elif host == "jobs.smartrecruiters.com" and parts:
            boards.setdefault(("smartrecruiters", "us", parts[0]), company)
        elif host.endswith(".oraclecloud.com") and "sites" in parts[:-1]:
            site = parts[parts.index("sites") + 1]
            boards.setdefault(("oracle", "us", f"{host}|{site}"), company)
    return boards


def _relative_posted(value: str | None) -> int | None:
    """Translate Workday's human posting age into a conservative timestamp."""
    if not value:
        return None
    now = datetime.now(timezone.utc)
    text = value.strip()
    if re.search(r"posted\s+today", text, re.I):
        return int(now.timestamp())
    match = re.search(r"posted\s+(\d+)\+?\s+days?\s+ago", text, re.I)
    if match:
        return int((now - timedelta(days=int(match.group(1)))).timestamp())
    for fmt in ("Posted %B %d, %Y", "Posted %b %d, %Y"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


def _post_json(url: str, payload: dict, timeout: int = 30):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


def _workday(token: str, company: str, region: str) -> list[Job]:
    host, tenant, site = token.split("|", 2)
    endpoint = f"https://{host}/wday/cxs/{urllib.parse.quote(tenant)}/{urllib.parse.quote(site)}/jobs"
    jobs: list[Job] = []
    offset = 0
    while offset < 1000:
        data = _post_json(endpoint, {
            "appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "intern",
        })
        rows = data.get("jobPostings") or []
        if not rows:
            break
        for raw in rows:
            path = raw.get("externalPath") or ""
            location = raw.get("locationsText") or ""
            place = best_place([location] if location else [])
            jobs.append(Job(
                source="ats", company=company, title=raw.get("title") or "",
                url=f"https://{host}/{urllib.parse.quote(site)}{path}",
                locations=[location] if location else [], terms=[],
                date_posted=_relative_posted(raw.get("postedOn")),
                tier=place.tier, distance_mi=place.distance_mi, place_label=place.display(),
                ats_vendor="workday", ats_board=token,
                ats_requisition_id=str((raw.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0]),
            ))
        offset += len(rows)
        if offset >= int(data.get("total") or 0):
            break
    return jobs


def _smartrecruiters(token: str, company: str, region: str) -> list[Job]:
    jobs: list[Job] = []
    offset = 0
    while offset < 1000:
        query = urllib.parse.urlencode({"q": "intern", "limit": 100, "offset": offset})
        data = _json(f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(token)}/postings?{query}")
        rows = data.get("content") or []
        if not rows:
            break
        for raw in rows:
            location = raw.get("location") or {}
            if str(location.get("country") or "").lower() not in {"us", "usa", "united states"}:
                continue
            location_text = location.get("fullLocation") or ", ".join(
                x for x in (location.get("city"), location.get("region")) if x
            )
            if location.get("remote"):
                location_text = f"Remote (US); {location_text}" if location_text else "Remote (US)"
            place = best_place([location_text] if location_text else [])
            identifier = str(raw.get("id") or raw.get("uuid") or "")
            jobs.append(Job(
                source="ats", company=(raw.get("company") or {}).get("name") or company,
                title=raw.get("name") or "",
                url=f"https://jobs.smartrecruiters.com/{token}/{identifier}",
                locations=[location_text] if location_text else [],
                category=(raw.get("department") or {}).get("label"),
                terms=[(raw.get("typeOfEmployment") or {}).get("label") or ""],
                date_posted=_timestamp(raw.get("releasedDate")),
                tier=place.tier, distance_mi=place.distance_mi, place_label=place.display(),
                ats_vendor="smartrecruiters", ats_board=token, ats_requisition_id=identifier,
            ))
        offset += len(rows)
        if offset >= int(data.get("totalFound") or 0):
            break
    return jobs


FETCHERS = {
    "greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
    "workday": _workday, "smartrecruiters": _smartrecruiters,
    **CAREER_SITE_FETCHERS,
}


# Three tries per board within a run; whatever still fails waits for the next run.
RETRY_WAITS = (3, 10, None)


def _transient(exc: Exception) -> bool:
    """Network blips, rate limits and 5xx are worth retrying; a 403/404 will not change."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def _fetch_vendors(cfg, vendors: set[str], name: str) -> "object":
    from . import SourceResult

    boards = discover_boards()
    # Companies listed by hand are watched even before any aggregator shows them.
    for entry in load_companies():
        region = "global" if entry["vendor"] in {"greenhouse", "lever", "ashby"} else "us"
        boards.setdefault((entry["vendor"], region, entry["board"]), entry["company"])
    boards = {key: company for key, company in boards.items() if key[0] in vendors}
    seen: dict[str, Job] = {}
    errors = []
    successful_boards: set[tuple[str, str]] = set()
    fetched = 0

    def fetch_one(item):
        (vendor, region, token), company = item
        for attempt, wait in enumerate(RETRY_WAITS):
            try:
                return vendor, token, FETCHERS[vendor](token, company, region), None
            except Exception as exc:  # one tenant must not stop all direct feeds
                if wait is None or not _transient(exc):
                    return vendor, token, None, exc
                time.sleep(wait)

    items = sorted(boards.items())
    # Different companies are independent. A small pool keeps the daily sweep
    # practical without sending concurrent requests to any single tenant.
    workers = 8 if name == "career_pages" else 4
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(fetch_one, items)
        for vendor, token, rows, error in results:
            if error is not None:
                errors.append(f"{vendor}:{token}: {error}")
                continue
            assert rows is not None
            successful_boards.add((vendor, token))
            fetched += len(rows)
            for job in rows:
                if job.company and job.title and job.url and keep_job(job, cfg):
                    seen.setdefault(link_id(job.url), job)
    ok = bool(seen) or not errors
    note = f"{len(boards) - len(errors)}/{len(boards)} boards"
    if errors:
        note += f"; {len(errors)} failed; first: {errors[0][:120]}"
    return SourceResult(name, ok, list(seen.values()), fetched, note, successful_boards)


def fetch(cfg) -> "object":
    """Fast direct feeds, safe to poll with the hourly discovery loop."""
    return _fetch_vendors(cfg, {"greenhouse", "lever", "ashby"}, "ats")


def fetch_career_pages(cfg) -> "object":
    """Broader company career pages, intentionally scheduled only once daily."""
    return _fetch_vendors(cfg, {"workday", "smartrecruiters", *CAREER_SITE_FETCHERS}, "career_pages")

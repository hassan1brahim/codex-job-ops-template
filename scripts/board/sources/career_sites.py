"""Scanners for big-company career sites the classic ATS feeds miss.

Banks and large tech firms mostly sit on Oracle Recruiting Cloud, Eightfold,
Phenom or iCIMS Jibe, or run their own site (Amazon, Bank of America, Goldman,
Google, Apple, Meta). Boards
are listed in `config/companies.json`; each scanner searches for internships
and leaves the role/location decision to `keep_job`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..filters import INTERN_WORDS
from ..geo import best_place
from ..store import DB_PATH, Job

ROOT = Path(__file__).resolve().parents[3]
COMPANIES_PATH = ROOT / "config" / "companies.json"
COMPANIES_EXAMPLE = ROOT / "config" / "companies.example.json"
# These sites reject the plain monitor UA that the open ATS APIs accept.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126 Safari/537.36")
MAX_ROWS = 600


def _request(url: str, payload: dict | None = None, timeout: int = 30, *,
             page: bool = False, limit: int | None = None) -> str:
    """GET (or POST JSON). `page` sends browser navigation headers, which Meta
    requires; `limit` stops reading after that many bytes."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/html;q=0.9"}
    if page:
        headers.update({"Accept": "text/html,application/xml", "Accept-Language": "en-US,en;q=0.9",
                        "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
                        "Sec-Fetch-Dest": "document"})
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    for wait in (5, 20, None):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(limit).decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or wait is None:
                raise
            time.sleep(wait)
    raise AssertionError("unreachable")


def _json(url: str, payload: dict | None = None):
    return json.loads(_request(url, payload))


def _iso(value) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00").replace("+0000", "+00:00"))
                   .timestamp())
    except ValueError:
        return None


def _job(vendor: str, board: str, company: str, title: str, url: str, locations: list[str],
         posted: int | None, req_id, category: str | None = None) -> Job:
    locations = [x for x in locations if x]
    place = best_place(locations)
    return Job(
        source="ats", company=company, title=(title or "").strip(), url=url,
        locations=locations, category=category, terms=[], date_posted=posted,
        tier=place.tier, distance_mi=place.distance_mi, place_label=place.display(),
        ats_vendor=vendor, ats_board=board, ats_requisition_id=str(req_id or ""),
    )


def load_companies(path: Path = COMPANIES_PATH) -> list[dict]:
    if not path.exists() and path == COMPANIES_PATH:
        path = COMPANIES_EXAMPLE
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("companies", [])


def _oracle(token: str, company: str, region: str) -> list[Job]:
    """Oracle Recruiting Cloud. Token: `host|siteNumber`."""
    host, site = token.split("|", 1)
    seen: dict[str, Job] = {}
    # "intern" stems to "internal" and matches everything; banks say "summer analyst".
    for keyword in ("internship", "summer analyst"):
        offset = 0
        while offset < MAX_ROWS:
            finder = (f'findReqs;siteNumber={site},keyword="{keyword}",limit=100,offset={offset},'
                      "sortBy=POSTING_DATES_DESC")
            url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                   f"?onlyData=true&expand=requisitionList.secondaryLocations"
                   f"&finder={urllib.parse.quote(finder, safe=';=,')}")
            items = _json(url).get("items") or [{}]
            rows = items[0].get("requisitionList") or []
            for raw in rows:
                locations = [raw.get("PrimaryLocation") or ""]
                locations += [x.get("Name") or "" for x in raw.get("secondaryLocations") or []]
                seen.setdefault(str(raw.get("Id")), _job(
                    "oracle", token, company, raw.get("Title"),
                    f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{raw.get('Id')}",
                    locations, _iso(raw.get("PostedDate")), raw.get("Id"),
                    raw.get("JobFamily"),
                ))
            offset += len(rows)
            if not rows or offset >= int(items[0].get("TotalJobsCount") or 0):
                break
    return list(seen.values())


def _eightfold(token: str, company: str, region: str) -> list[Job]:
    """Eightfold search. Token: `host|domain`. Older tenants (Netflix) only serve
    the v2 API, so fall back to it when the PCSX search is refused."""
    host, domain = token.split("|", 1)
    try:
        return _eightfold_pages(host, domain, token, company, "pcsx")
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
        return _eightfold_pages(host, domain, token, company, "v2")


def _eightfold_location(text: str) -> str:
    # Northrop writes "United States-California-Manhattan Beach".
    parts = text.split("-")
    if len(parts) >= 3 and parts[0].strip() in {"United States", "USA", "US"}:
        return f"{'-'.join(parts[2:]).strip()}, {parts[1].strip()}"
    return text


def _eightfold_pages(host: str, domain: str, token: str, company: str, api: str) -> list[Job]:
    jobs: list[Job] = []
    start = 0
    while start < MAX_ROWS:
        if api == "pcsx":
            query = urllib.parse.urlencode({"domain": domain, "query": "intern", "location": "",
                                            "start": start, "sort_by": "timestamp"})
            data = _json(f"https://{host}/api/pcsx/search?{query}").get("data") or {}
        else:
            query = urllib.parse.urlencode({"domain": domain, "query": "intern", "start": start,
                                            "num": 100, "sort_by": "timestamp"})
            data = _json(f"https://{host}/api/apply/v2/jobs?{query}")
        rows = data.get("positions") or []
        for raw in rows:
            path = (raw.get("positionUrl") or raw.get("canonicalPositionUrl")
                    or f"/careers/job/{raw.get('id')}")
            locations = list(raw.get("locations") or [raw.get("location") or ""])
            jobs.append(_job(
                "eightfold", token, company, raw.get("name"),
                path if path.startswith("http") else f"https://{host}{path}",
                [_eightfold_location(x) for x in locations],
                raw.get("postedTs") or raw.get("t_create"), raw.get("id"),
                raw.get("department"),
            ))
        start += len(rows)
        if not rows or start >= int(data.get("count") or 0):
            break
        time.sleep(0.5)  # Microsoft answers bursts with 429
    return jobs


def _phenom(token: str, company: str, region: str) -> list[Job]:
    """Phenom People career sites. Token: `host|/locale/path`."""
    host, path = token.split("|", 1)
    page = _request(f"https://{host}{path}/search-results?keywords=intern")
    ref = re.search(r'"refNum"\s*:\s*"([^"]+)"', page)
    locale = re.search(r'"locale"\s*:\s*"([^"]+)"', page)
    if not ref:
        raise ValueError(f"no Phenom refNum on {host}")
    jobs: list[Job] = []
    start = 0
    while start < MAX_ROWS:
        body = {
            "lang": locale.group(1) if locale else "en_us", "deviceType": "desktop",
            "country": "us", "pageName": "search-results", "ddoKey": "refineSearch",
            "sortBy": "Most recent", "subsearch": "", "from": start, "jobs": True,
            "counts": False, "all_fields": [], "size": 50, "clearAll": False,
            "jdsource": "facets", "isSliderEnable": False, "pageId": "page20",
            "siteType": "external", "keywords": "intern", "global": True,
            "selected_fields": {}, "locationData": {}, "refNum": ref.group(1),
        }
        data = (_json(f"https://{host}/widgets", body).get("refineSearch") or {})
        rows = (data.get("data") or {}).get("jobs") or []
        for raw in rows:
            locations = raw.get("multi_location") or [raw.get("location") or ""]
            jobs.append(_job(
                "phenom", token, company, raw.get("title"),
                f"https://{host}{path}/job/{raw.get('jobId')}",
                locations, _iso(raw.get("postedDate")), raw.get("jobId") or raw.get("reqId"),
                raw.get("category"),
            ))
        start += len(rows)
        if not rows or start >= int(data.get("totalHits") or 0):
            break
    return jobs


def _jibe(token: str, company: str, region: str) -> list[Job]:
    """iCIMS Jibe front ends (`/api/jobs`). Token: the career-site host."""
    jobs: list[Job] = []
    page = 1
    while len(jobs) < MAX_ROWS:
        query = urllib.parse.urlencode({"keywords": "intern", "page": page, "limit": 100})
        data = _json(f"https://{token}/api/jobs?{query}")
        rows = data.get("jobs") or []
        for raw in rows:
            raw = raw.get("data", raw)
            location = ", ".join(x for x in (raw.get("city"), raw.get("state"),
                                             raw.get("country")) if x)
            jobs.append(_job(
                "jibe", token, company, raw.get("title"),
                raw.get("apply_url") if not raw.get("slug") else f"https://{token}/jobs/{raw['slug']}",
                [location], _iso(raw.get("posted_date") or raw.get("create_date")),
                raw.get("req_id") or raw.get("slug"),
            ))
        if not rows or len(jobs) >= int(data.get("totalCount") or 0):
            break
        page += 1
    return jobs


def _amazon(token: str, company: str, region: str) -> list[Job]:
    """amazon.jobs search. Token: unused (`amazon`)."""
    jobs: list[Job] = []
    offset = 0
    while offset < MAX_ROWS:
        query = urllib.parse.urlencode({"base_query": "intern", "country": "USA", "offset": offset,
                                        "result_limit": 100, "sort": "recent"})
        data = _json(f"https://www.amazon.jobs/en/search.json?{query}")
        rows = data.get("jobs") or []
        for raw in rows:
            jobs.append(_job(
                "amazon", token, company, raw.get("title"),
                f"https://www.amazon.jobs{raw.get('job_path')}",
                [raw.get("normalized_location") or raw.get("location") or ""],
                _amazon_date(raw.get("posted_date")), raw.get("id_icims") or raw.get("id"),
                raw.get("job_category"),
            ))
        offset += len(rows)
        if not rows or offset >= int(data.get("hits") or 0):
            break
    return jobs


def _amazon_date(value) -> int | None:
    try:
        return int(datetime.strptime(value, "%B %d, %Y").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _bofa(token: str, company: str, region: str) -> list[Job]:
    """careers.bankofamerica.com. Student programs sit on their own servlet, and
    keyword filters are ignored, so read both lists whole (`start` is a page index)."""
    jobs: list[Job] = []
    for servlet in ("campusjobssearchservlet", "jobssearchservlet"):
        data = _json(f"https://careers.bankofamerica.com/services/{servlet}?"
                     "search=getAllJobs&start=0&rows=5000")
        for raw in data.get("jobsList") or []:
            locations = (raw.get("additionalLocationsList") or raw.get("location") or "").split("; ")
            jobs.append(_job(
                "bofa", token, company, raw.get("postingTitle"),
                f"https://careers.bankofamerica.com{raw.get('jcrURL')}",
                locations, _iso(raw.get("postedDate")), raw.get("jobRequisitionId"),
                raw.get("family"),
            ))
    return jobs


GOLDMAN_QUERY = """query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items { roleId jobTitle jobFunction locations { primary city state country } }
  }
}"""


def _goldman(token: str, company: str, region: str) -> list[Job]:
    """higher.gs.com campus programs (Summer Analyst etc.). No posting dates."""
    jobs: list[Job] = []
    page = 0
    while len(jobs) < MAX_ROWS:
        variables = {"searchQueryInput": {
            "page": {"pageSize": 100, "pageNumber": page}, "filters": [],
            "experiences": ["CAMPUS"], "searchTerm": "",
        }}
        data = _json("https://api-higher.gs.com/gateway/api/v1/graphql",
                     {"operationName": "GetRoles", "query": GOLDMAN_QUERY, "variables": variables})
        search = (data.get("data") or {}).get("roleSearch") or {}
        rows = search.get("items") or []
        for raw in rows:
            locations = [", ".join(x for x in (loc.get("city"), loc.get("state"), loc.get("country")) if x)
                         for loc in raw.get("locations") or []]
            # "2027 | Americas | New York | Engineering | Summer Analyst": the division
            # is the only role signal, and Goldman's "Engineering" is software.
            parts = [x.strip() for x in (raw.get("jobTitle") or "").split("|")]
            division = parts[3].lower() if len(parts) >= 5 else raw.get("jobFunction")
            jobs.append(_job(
                "goldman", token, company, raw.get("jobTitle"),
                f"https://higher.gs.com/roles/{raw.get('roleId')}", locations, None,
                raw.get("roleId"), division,
            ))
        page += 1
        if not rows or len(jobs) >= int(search.get("totalCount") or 0):
            break
    return jobs


def _google(token: str, company: str, region: str) -> list[Job]:
    """Google careers. Results are embedded in the page as the `ds:1` data blob."""
    jobs: list[Job] = []
    for page in range(1, 11):
        query = urllib.parse.urlencode({"q": "intern", "location": "United States",
                                        "target_level": "INTERN_AND_APPRENTICE", "page": page})
        html_text = _request(f"https://www.google.com/about/careers/applications/jobs/results?{query}")
        match = re.search(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);",
                          html_text, re.S)
        if not match:
            raise ValueError("Google careers page carried no ds:1 results")
        data = json.loads(match.group(1))
        rows = data[0] or []
        for raw in rows:
            locations = [loc[0] for loc in raw[9] or [] if loc]
            created = raw[12][0] if raw[12] else None
            jobs.append(_job(
                "google", token, company, raw[1],
                f"https://www.google.com/about/careers/applications/jobs/results/{raw[0]}",
                locations, created, raw[0],
            ))
        total = data[2] or 0
        if not rows or len(jobs) >= total:
            break
    return jobs


def _apple(token: str, company: str, region: str) -> list[Job]:
    """jobs.apple.com Students team, US. Results ride in the router hydration data."""
    jobs: list[Job] = []
    for page in range(1, 11):
        html_text = _request("https://jobs.apple.com/en-us/search?location=united-states-USA"
                             f"&team=internships-STDNT-INTRN&page={page}")
        match = re.search(r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\((\".*?\")\);",
                          html_text, re.S)
        if not match:
            raise ValueError("Apple jobs page carried no hydration data")
        search = json.loads(json.loads(match.group(1)))["loaderData"]["search"]
        rows = search.get("searchResults") or []
        for raw in rows:
            locations = [", ".join(x for x in (loc.get("city"), loc.get("stateProvince"),
                                               loc.get("countryName")) if x)
                         for loc in raw.get("locations") or []]
            jobs.append(_job(
                "apple", token, company, raw.get("postingTitle"),
                f"https://jobs.apple.com/en-us/details/{raw.get('positionId')}/"
                f"{raw.get('transformedPostingTitle')}",
                locations, _iso(raw.get("postDateInGMT")), raw.get("id"),
            ))
        if not rows or len(jobs) >= int(search.get("totalRecords") or 0):
            break
    return jobs


META_CACHE = DB_PATH.parent / "meta_titles.json"


def _meta(token: str, company: str, region: str) -> list[Job]:
    """metacareers.com. The search API needs a session, so read the jobs sitemap
    and each job page's title once (cached); only internships get a full read."""
    sitemap = _request("https://www.metacareers.com/jobsearch/sitemap.xml", page=True)
    ids = list(dict.fromkeys(re.findall(r"/profile/job_details/(\d+)/", sitemap)))
    try:
        cache = json.loads(META_CACHE.read_text())
    except (OSError, ValueError):
        cache = {}

    def url(job_id: str) -> str:
        return f"https://www.metacareers.com/profile/job_details/{job_id}/"

    def title_of(job_id: str) -> tuple[str, str]:
        head = _request(url(job_id), page=True, limit=4096)
        found = re.search(r'og:title" content="([^"]+)"', head)
        return job_id, html_unescape(found.group(1)) if found else ""

    def locations_of(job_id: str) -> tuple[str, list[str]]:
        found = re.search(r'"title":"[^"]*","locations":(\[[^\]]*\])',
                          _request(url(job_id), page=True))
        return job_id, json.loads(found.group(1)) if found else []

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for job_id, title in pool.map(title_of, [i for i in ids if i not in cache]):
                cache[job_id] = {"title": title}
            wanted = [i for i in ids if INTERN_WORDS.search(cache[i]["title"])
                      and "locations" not in cache[i]]
            for job_id, locations in pool.map(locations_of, wanted):
                cache[job_id]["locations"] = locations
    finally:
        live = set(ids)
        META_CACHE.write_text(json.dumps({k: v for k, v in cache.items() if k in live}))
    return [
        _job("meta", token, company, cache[i]["title"], url(i), cache[i]["locations"], None, i)
        for i in ids if INTERN_WORDS.search(cache[i]["title"])
    ]


def _morganstanley(token: str, company: str, region: str) -> list[Job]:
    """morganstanley.com Students & Graduates feed. Links go to its tal.net
    application site; the feed carries deadlines, not posting dates."""
    data = _json("https://www.morganstanley.com/web/career_services/webapp/service/"
                 "careerservice/resultset.json?opportunity=sg")
    jobs = []
    for raw in data.get("resultSet") or []:
        location = ", ".join(x for x in (raw.get("city"), raw.get("allState"), raw.get("country")) if x)
        jobs.append(_job(
            "morganstanley", token, company,
            html_unescape(html_unescape(raw.get("jobTitle") or "").replace("amp;", "&")),
            raw.get("url") or "", [location or raw.get("location") or ""], None,
            raw.get("jobNumber"),
        ))
    return jobs


def _deshaw(token: str, company: str, region: str) -> list[Job]:
    """deshaw.com. The Next.js page embeds every internship and regular job."""
    page = _request("https://www.deshaw.com/careers/choose-your-path", page=True)
    found = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    if not found:
        raise ValueError("D. E. Shaw careers page carried no __NEXT_DATA__")
    props = json.loads(found.group(1))["props"]["pageProps"]
    jobs = []
    for item in (props.get("internships") or []) + (props.get("regularJobs") or []):
        raw = item.get("data") or item
        offices = [x.get("name") or "" for x in raw.get("office") or item.get("office") or []]
        title = raw.get("displayName") or ""
        if not offices:  # "Software Developer Intern (New York) - Summer 2027"
            offices = re.findall(r"\(([^)]+)\)", title)[:1]
        jobs.append(_job(
            "deshaw", token, company, title,
            f"https://www.deshaw.com/careers/{raw.get('jobUrl')}", offices, None, raw.get("id"),
            ((raw.get("department") or {}).get("name")),
        ))
    return jobs


def _avature(token: str, company: str, region: str) -> list[Job]:
    """Avature career sites (Bloomberg, Two Sigma). Token: `host|listing path`.
    Server-rendered `<article>` cards; the page size is fixed by the site."""
    host, path = token.split("|", 1)
    jobs: list[Job] = []
    seen: set[str] = set()
    offset = 0
    while offset < MAX_ROWS:
        page = _request(f"https://{host}{path}?jobOffset={offset}", page=True)
        cards = page.split("<article")[1:]
        fresh = 0
        for card in cards:
            link = re.search(r'href="(https://[^"]+/JobDetail/[^"]*?/(\d+))"[^>]*>\s*([^<]+?)\s*</a>', card)
            if not link or link.group(2) in seen:
                continue
            seen.add(link.group(2))
            fresh += 1
            # Bloomberg tags the location; Two Sigma puts it in the first plain span.
            place = re.search(r'(?:list-item-location|paragraph_inner-span)">\s*([^<]+?)\s*<', card)
            location = html_unescape(place.group(1)) if place else ""
            # Two Sigma writes "United States - NY New York".
            us = re.fullmatch(r"United States\s*-\s*([A-Z]{2})\s+(.+)", location)
            if us:
                location = f"{us.group(2)}, {us.group(1)}"
            jobs.append(_job("avature", token, company, html_unescape(link.group(3)), link.group(1),
                             [location], None, link.group(2)))
        if not fresh:
            break
        offset += len(cards)
    return jobs


def _citi(token: str, company: str, region: str) -> list[Job]:
    """jobs.citi.com (Radancy). The results endpoint returns every match in one call."""
    query = urllib.parse.urlencode({
        "ActiveFacetID": 0, "CurrentPage": 1, "RecordsPerPage": 500, "Keywords": "intern",
        "Location": "", "IsPagination": "False", "SearchResultsModuleName": "Search Results",
        "SearchFiltersModuleName": "Search Filters", "SortCriterion": 0, "SortDirection": 0,
        "SearchType": 5,
    })
    page = json.loads(_request(f"https://jobs.citi.com/search-jobs/results?{query}")).get("results", "")
    cards = re.findall(
        r'class="sr-job-item__link" href="([^"]+)"[^>]*data-job-id="(\d+)"[^>]*>\s*([^<]+?)\s*</a>'
        r'.*?sr-job-location">([^<]*)<', page, re.S)
    return [
        _job("citi", token, company, html_unescape(title), f"https://jobs.citi.com{path}",
             [html_unescape(location)], None, req_id)
        for path, req_id, title, location in cards
    ]


def html_unescape(text: str) -> str:
    import html
    return html.unescape(text)


FETCHERS = {
    "oracle": _oracle, "eightfold": _eightfold, "phenom": _phenom, "jibe": _jibe,
    "amazon": _amazon, "bofa": _bofa, "goldman": _goldman, "google": _google,
    "apple": _apple, "meta": _meta, "morganstanley": _morganstanley,
    "deshaw": _deshaw, "avature": _avature, "citi": _citi,
}

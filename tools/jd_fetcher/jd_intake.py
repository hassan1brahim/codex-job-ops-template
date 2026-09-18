"""Job-description intake and lightweight URL fetching.

This module normalizes fetched JD sources into the same `JobDescription` contract:
- ATS APIs
- HTML pages
- structured page metadata

The first version intentionally uses the Python standard library. Heavier ATS-
specific scraping can be added behind this boundary without changing callers.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from .models import JobDescription


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 JobAppAgent/0.1"
)
MIN_DESCRIPTION_CHARS = 80

# LinkedIn serves the real job description as static HTML only on the
# canonical /jobs/view/{id} URL (tracking query params can trigger a
# different response) and only with browser-like headers.
LINKEDIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_LINKEDIN_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*LinkedIn\s*$", re.I)
_LINKEDIN_HIRING_RE = re.compile(r"^(.*?)\s+hiring\s+(.*)$", re.I)

_KEYWORD_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "based",
    "but",
    "can",
    "candidate",
    "company",
    "for",
    "from",
    "has",
    "have",
    "job",
    "our",
    "role",
    "that",
    "the",
    "their",
    "this",
    "with",
    "will",
    "work",
    "you",
    "your",
}


class JobDescriptionFetchError(RuntimeError):
    """Raised when a URL cannot be fetched or parsed into useful JD text."""


class _ReadableHTMLParser(HTMLParser):
    """Extract visible text and the page title from basic HTML."""

    _BLOCK_TAGS = {
        "article",
        "br",
        "dd",
        "div",
        "dt",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "section",
        "td",
        "th",
        "tr",
        "ul",
        "ol",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self._parts))

    @property
    def title(self) -> str:
        return normalize_text(" ".join(self._title_parts))

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = data.strip()
        if not cleaned:
            return
        if self._in_title:
            self._title_parts.append(cleaned)
        else:
            self._parts.append(cleaned)


class _LinkedInDescriptionParser(HTMLParser):
    """Extract text from LinkedIn's job-description markup div."""

    _TARGET_MARKER = "show-more-less-html__markup"
    _BREAK_TAGS = {"br", "p", "li", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._depth = 0

    @property
    def text(self) -> str:
        return normalize_text(" ".join(self._parts))

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if self._depth == 0:
            if self._TARGET_MARKER in attrs_dict.get("class", ""):
                self._depth = 1
            return
        self._depth += 1
        if tag.lower() in self._BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._depth > 0:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth > 0:
            cleaned = data.strip()
            if cleaned:
                self._parts.append(cleaned)


def _linkedin_job_id(url: str) -> str | None:
    """Pull the numeric job id out of a /jobs/view/ URL or a currentJobId param."""

    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if "view" in path_parts:
        candidate = path_parts[path_parts.index("view") + 1 :]
        if candidate:
            match = re.match(r"\d+", candidate[0])
            if match:
                return match.group()
    query_job_id = parse_qs(parsed.query).get("currentJobId")
    if query_job_id:
        return query_job_id[0]
    return None


def _simplify_canonical_url(url: str) -> str:
    """Resolve a Simplify search/listing URL with a jobId param to its /p/{id} job page."""

    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if path_parts and path_parts[0] == "p":
        return url
    job_id = parse_qs(parsed.query).get("jobId")
    if job_id:
        return f"https://simplify.jobs/p/{job_id[0]}"
    return url


def _linkedin_title_company(page_title: str) -> tuple[str, str]:
    """Best-effort split of LinkedIn's '<Company> hiring <Title> | LinkedIn' page title."""

    cleaned = _LINKEDIN_TITLE_SUFFIX_RE.sub("", page_title).strip()
    match = _LINKEDIN_HIRING_RE.match(cleaned)
    if match:
        return match.group(2).strip(), match.group(1).strip()
    return cleaned, ""


class _StructuredDataParser(HTMLParser):
    """Collect JSON-LD scripts and meta tags commonly used on job pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.json_ld_blocks: list[str] = []
        self.next_data_blocks: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_json_ld = False
        self._in_next_data = False
        self._json_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "script" and attrs_dict.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_parts = []
            return
        if tag.lower() == "script" and attrs_dict.get("id") == "__NEXT_DATA__":
            self._in_next_data = True
            self._json_parts = []
            return
        if tag.lower() != "meta":
            return

        key = (
            attrs_dict.get("property")
            or attrs_dict.get("name")
            or attrs_dict.get("itemprop")
            or ""
        ).lower()
        content = attrs_dict.get("content", "")
        if key and content and key not in self.meta:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_json_ld:
            self._in_json_ld = False
            block = "".join(self._json_parts).strip()
            if block:
                self.json_ld_blocks.append(block)
        if tag.lower() == "script" and self._in_next_data:
            self._in_next_data = False
            block = "".join(self._json_parts).strip()
            if block:
                self.next_data_blocks.append(block)

    def handle_data(self, data: str) -> None:
        if self._in_json_ld or self._in_next_data:
            self._json_parts.append(data)


def normalize_text(text: str) -> str:
    """Collapse noisy whitespace while preserving paragraph breaks."""

    text = html.unescape(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_keywords(text: str, *, limit: int = 30) -> list[str]:
    """Return simple frequency-based keywords for early matching work."""

    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", text.lower())
    counts: dict[str, int] = {}
    for word in words:
        if word in _KEYWORD_STOPWORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def _plain_text_from_html_fragment(value: Any) -> str:
    if value is None:
        return ""
    parser = _ReadableHTMLParser()
    parser.feed(str(value))
    return parser.text or normalize_text(str(value))


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, (int, float, bool)):
        return normalize_text(str(value))
    if isinstance(value, dict):
        for key in ("value", "name", "title", "text", "label"):
            if value.get(key):
                return _string_value(value[key])
        return ""
    return ""


def _location_from_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(part for part in (_location_from_value(item) for item in value) if part)
    if isinstance(value, str):
        return normalize_text(value)
    if not isinstance(value, dict):
        return ""

    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        joined = normalize_text(", ".join(str(part) for part in parts if part))
        if joined:
            return joined
    return _string_value(value)


def _iter_json_objects(data: Any):
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_json_objects(item)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_json_objects(item)


def _iter_all_json_objects(data: Any):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _iter_all_json_objects(value)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_all_json_objects(item)


def _jobposting_from_json_ld(html_content: str) -> JobDescription | None:
    parser = _StructuredDataParser()
    parser.feed(html_content)

    for block in parser.json_ld_blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue

        for obj in _iter_json_objects(data):
            types = obj.get("@type", "")
            if isinstance(types, str):
                type_values = {types.lower()}
            else:
                type_values = {str(value).lower() for value in types}
            if "jobposting" not in type_values:
                continue

            org = obj.get("hiringOrganization") or {}
            company = org.get("name", "") if isinstance(org, dict) else str(org)
            title = normalize_text(str(obj.get("title") or ""))
            location = _location_from_value(obj.get("jobLocation"))
            description = _plain_text_from_html_fragment(obj.get("description"))
            if not description:
                continue
            return from_text(description, title=title, company=company, location=location)

    return None


def _jobposting_from_next_data(html_content: str) -> JobDescription | None:
    """Extract a likely job object from Next.js page data, used by Simplify-style pages."""

    parser = _StructuredDataParser()
    parser.feed(html_content)
    for block in parser.next_data_blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for obj in _iter_all_json_objects(data):
            if not isinstance(obj, dict):
                continue
            raw_description = (
                obj.get("description")
                or obj.get("descriptionHtml")
                or obj.get("jobDescription")
                or obj.get("jobDescriptionHtml")
                or obj.get("body")
            )
            description = _plain_text_from_html_fragment(raw_description)
            if len(description) < MIN_DESCRIPTION_CHARS:
                continue
            company = obj.get("companyName") or obj.get("company") or obj.get("organization")
            location = obj.get("location") or obj.get("locations")
            return from_text(
                description,
                title=_string_value(obj.get("title") or obj.get("name")),
                company=_string_value(company),
                location=_location_from_value(location),
            )
    return None


def _jobposting_from_meta(html_content: str) -> JobDescription | None:
    parser = _StructuredDataParser()
    parser.feed(html_content)
    meta = parser.meta

    title = (
        meta.get("title")
        or meta.get("og:title")
        or meta.get("twitter:title")
        or ""
    )
    company = (
        meta.get("hiringorganization")
        or meta.get("company")
        or meta.get("linkedin:company")
        or ""
    )
    description = (
        meta.get("description")
        or meta.get("og:description")
        or meta.get("twitter:description")
        or ""
    )
    if not description:
        return None
    try:
        return from_text(description, title=title, company=company)
    except ValueError:
        return None


def parse_html_job_description(
    html_content: str,
    *,
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    source: str = "generic",
) -> JobDescription:
    """Extract readable text from HTML and return a normalized JD."""

    structured_jd = (
        _jobposting_from_json_ld(html_content)
        or _jobposting_from_next_data(html_content)
        or _jobposting_from_meta(html_content)
    )
    if structured_jd:
        return JobDescription(
            url=url.strip(),
            title=title.strip() or structured_jd.title,
            company=company.strip() or structured_jd.company,
            location=location.strip() or structured_jd.location,
            description=structured_jd.description,
            keywords=structured_jd.keywords,
            source=source,
        )

    parser = _ReadableHTMLParser()
    parser.feed(html_content)
    description = parser.text
    resolved_title = title.strip() or parser.title
    return from_text(
        description,
        url=url,
        title=resolved_title,
        company=company,
        location=location,
        source=source,
    )


def _detect_source(url: str) -> str:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "myworkdayjobs.com" in host:
        return "workday"
    if "simplify.jobs" in host or "simplify" in host and "/jobs" in path:
        return "simplify"
    if "linkedin.com" in host and "/jobs" in path:
        return "linkedin"
    if "ashbyhq.com" in host:
        return "ashby"
    if "smartrecruiters.com" in host:
        return "smartrecruiters"
    return "generic"


def _read_url(
    url: str, *, timeout: int = 20, headers: dict[str, str] | None = None
) -> tuple[str, str, bytes, str]:
    request = Request(url, headers=headers or {"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read()
            return content_type, charset, body, response.geturl()
    except HTTPError as exc:
        raise JobDescriptionFetchError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise JobDescriptionFetchError(f"Could not fetch {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise JobDescriptionFetchError(f"Timed out fetching {url}") from exc


def _read_text_url(url: str, *, timeout: int = 20) -> str:
    content_type, charset, body, _ = _read_url(url, timeout=timeout)
    if "html" not in content_type.lower() and "text" not in content_type.lower():
        raise JobDescriptionFetchError(
            f"Unsupported content type for {url}: {content_type or 'unknown'}"
        )
    return body.decode(charset, errors="replace")


def _read_json_url(url: str, *, timeout: int = 20) -> Any:
    content_type, charset, body, _ = _read_url(url, timeout=timeout)
    decoded = body.decode(charset, errors="replace")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise JobDescriptionFetchError(f"Invalid JSON while fetching {url}") from exc


def _fetch_generic_url(
    url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    source: str = "generic",
    timeout: int = 20,
) -> JobDescription:
    decoded = _read_text_url(url, timeout=timeout)
    if re.search(r"<html|<body|<article|<script|<main", decoded, re.I):
        return parse_html_job_description(
            decoded,
            url=url,
            title=title,
            company=company,
            location=location,
            source=source,
        )
    return from_text(
        decoded,
        url=url,
        title=title,
        company=company,
        location=location,
        source=source,
    )


def _greenhouse_parts(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(parts) >= 5 and parts[:2] == ["v1", "boards"] and parts[3] == "jobs":
        return parts[2], parts[4]
    if len(parts) >= 3 and parts[1] == "jobs":
        return parts[0], parts[2]
    return None


def _fetch_greenhouse_url(
    url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    timeout: int = 20,
) -> JobDescription:
    parts = _greenhouse_parts(url)
    if not parts:
        return _fetch_generic_url(url, title=title, company=company, location=location,
                                  source="greenhouse", timeout=timeout)
    board, job_id = parts
    data = _read_json_url(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise JobDescriptionFetchError(f"Unexpected Greenhouse response for {url}")
    resolved_location = location or _string_value(data.get("location"))
    return from_text(
        _plain_text_from_html_fragment(data.get("content")),
        url=url,
        title=title or _string_value(data.get("title")),
        company=company,
        location=resolved_location,
        source="greenhouse",
    )


def _lever_parts(url: str) -> tuple[str, str] | None:
    parts = [unquote(part) for part in urlparse(url).path.strip("/").split("/") if part]
    if len(parts) >= 4 and parts[:2] == ["v0", "postings"]:
        return parts[2], parts[3]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def _lever_description(data: dict) -> str:
    parts = [
        data.get("descriptionPlain"),
        _plain_text_from_html_fragment(data.get("description")),
    ]
    for item in data.get("lists", []) or []:
        if not isinstance(item, dict):
            continue
        heading = _string_value(item.get("text"))
        content = item.get("content")
        parts.extend([heading, _plain_text_from_html_fragment(content)])
    return normalize_text("\n\n".join(part for part in parts if part))


def _fetch_lever_url(
    url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    timeout: int = 20,
) -> JobDescription:
    parts = _lever_parts(url)
    if not parts:
        return _fetch_generic_url(url, title=title, company=company, location=location,
                                  source="lever", timeout=timeout)
    account, posting_id = parts
    data = _read_json_url(
        f"https://api.lever.co/v0/postings/{account}/{posting_id}",
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise JobDescriptionFetchError(f"Unexpected Lever response for {url}")
    categories = data.get("categories") if isinstance(data.get("categories"), dict) else {}
    return from_text(
        _lever_description(data),
        url=url,
        title=title or _string_value(data.get("text")),
        company=company or account,
        location=location or _string_value(categories.get("location")),
        source="lever",
    )


def _workday_parts(url: str) -> tuple[str, str, str, str] | None:
    parsed = urlparse(url)
    host_parts = parsed.netloc.split(".")
    if len(host_parts) < 3:
        return None
    tenant, pod = host_parts[0], host_parts[1]
    path_parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) >= 6 and path_parts[:2] == ["wday", "cxs"]:
        return path_parts[2], pod, path_parts[3], path_parts[-1]
    if len(path_parts) >= 3:
        return tenant, pod, path_parts[0], path_parts[-1]
    return None


def _fetch_workday_url(
    url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    timeout: int = 20,
) -> JobDescription:
    parts = _workday_parts(url)
    if not parts:
        return _fetch_generic_url(url, title=title, company=company, location=location,
                                  source="workday", timeout=timeout)
    tenant, pod, site, job_id = parts
    base = f"https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    errors: list[str] = []
    data: Any = None
    for api_url in (f"{base}/job/{job_id}", f"{base}/jobs/{job_id}"):
        try:
            data = _read_json_url(api_url, timeout=timeout)
            break
        except JobDescriptionFetchError as exc:
            errors.append(str(exc))
    if not isinstance(data, dict):
        raise JobDescriptionFetchError("; ".join(errors) or f"Unexpected Workday response for {url}")

    info = data.get("jobPostingInfo") if isinstance(data.get("jobPostingInfo"), dict) else data
    description = (
        info.get("jobDescription")
        or info.get("jobDescriptionHtml")
        or info.get("description")
    )
    return from_text(
        _plain_text_from_html_fragment(description),
        url=url,
        title=title or _string_value(info.get("title")),
        company=company or _string_value(info.get("hiringOrganization")),
        location=location or _location_from_value(
            info.get("jobRequisitionLocation") or info.get("locationsText")
        ),
        source="workday",
    )


def _fetch_linkedin_url(
    url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    timeout: int = 20,
) -> JobDescription:
    job_id = _linkedin_job_id(url)
    if not job_id:
        return _fetch_generic_url(
            url, title=title, company=company, location=location,
            source="linkedin", timeout=timeout,
        )

    canonical_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    _, charset, body, final_url = _read_url(
        canonical_url, timeout=timeout, headers=LINKEDIN_HEADERS
    )
    html_content = body.decode(charset, errors="replace")

    if any(marker in final_url for marker in ("/signup", "/login", "/authwall")):
        raise JobDescriptionFetchError(
            "LinkedIn redirected to a login wall; this job description is not publicly accessible."
        )

    description_parser = _LinkedInDescriptionParser()
    description_parser.feed(html_content)
    description = description_parser.text
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise JobDescriptionFetchError(
            "LinkedIn did not return a public job description for this posting."
        )

    page_parser = _ReadableHTMLParser()
    page_parser.feed(html_content)
    parsed_title, parsed_company = _linkedin_title_company(page_parser.title)

    return from_text(
        description,
        url=canonical_url,
        title=title.strip() or parsed_title,
        company=company.strip() or parsed_company,
        location=location,
        source="linkedin",
    )


def from_text(
    text: str,
    *,
    url: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    source: str = "manual",
) -> JobDescription:
    """Normalize extracted JD text."""

    description = normalize_text(text)
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise ValueError(
            f"Job description is too short; expected at least {MIN_DESCRIPTION_CHARS} characters."
        )
    return JobDescription(
        url=url.strip(),
        title=title.strip(),
        company=company.strip(),
        location=location.strip(),
        description=description,
        keywords=extract_keywords(description),
        source=source,
    )


def fetch_from_url(
    url: str,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    timeout: int = 20,
) -> JobDescription:
    """Fetch a job URL and extract readable JD text using a source router."""

    normalized_url = url.strip()
    if not normalized_url:
        raise ValueError("A URL is required.")

    source = _detect_source(normalized_url)
    if source == "greenhouse":
        return _fetch_greenhouse_url(
            normalized_url, title=title, company=company, location=location, timeout=timeout
        )
    if source == "lever":
        return _fetch_lever_url(
            normalized_url, title=title, company=company, location=location, timeout=timeout
        )
    if source == "workday":
        return _fetch_workday_url(
            normalized_url, title=title, company=company, location=location, timeout=timeout
        )
    if source == "linkedin":
        return _fetch_linkedin_url(
            normalized_url, title=title, company=company, location=location, timeout=timeout
        )
    if source == "simplify":
        normalized_url = _simplify_canonical_url(normalized_url)
    return _fetch_generic_url(
        normalized_url,
        title=title,
        company=company,
        location=location,
        source=source,
        timeout=timeout,
    )

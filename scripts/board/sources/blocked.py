"""Sources that cannot currently be scraped, with the reason recorded.

These return ok=False so the board renders them as "blocked" rather than
implying the site has no matching jobs. Each note says what it would take to
turn the source on, so the decision is revisitable instead of forgotten.
"""

from __future__ import annotations

WELLFOUND_NOTE = (
    "Cloudflare Turnstile challenge on every request. Needs a real browser "
    "session (Playwright + stored cookies) to read. Manual fallback: "
    "wellfound.com/role/r/software-engineer-intern"
)
SWELIST_NOTE = (
    "Next.js app renders listings client-side; no API route and no data in the "
    "RSC payload. Needs Playwright. It also aggregates the same GitHub feeds we "
    "already read directly, so marginal value is low."
)
YC_NOTE = (
    "workatastartup.com/jobs returns 406 without a session; the public YC board "
    "loads via Algolia keys buried in JS bundles. Needs either a login cookie or "
    "key extraction. Manual fallback: workatastartup.com (filter Internship)."
)


def _blocked(name: str, note: str):
    from . import SourceResult

    return SourceResult(name, False, [], 0, note)


def wellfound(cfg):
    return _blocked("wellfound", WELLFOUND_NOTE)


def swelist(cfg):
    return _blocked("swelist", SWELIST_NOTE)


def yc(cfg):
    return _blocked("yc", YC_NOTE)

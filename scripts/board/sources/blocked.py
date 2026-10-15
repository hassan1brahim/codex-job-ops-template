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


def _blocked(name: str, note: str):
    from . import SourceResult

    return SourceResult(name, False, [], 0, note)


def wellfound(cfg):
    return _blocked("wellfound", WELLFOUND_NOTE)

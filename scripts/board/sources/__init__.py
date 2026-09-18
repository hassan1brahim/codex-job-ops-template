"""Board source plugins.

Every source exposes `fetch(cfg) -> SourceResult`. A source that cannot be
scraped returns ok=False with a reason rather than an empty list, so the board
can show "blocked" instead of silently implying there are no jobs there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..store import Job


@dataclass
class SourceResult:
    name: str
    ok: bool
    jobs: list[Job] = field(default_factory=list)
    fetched: int = 0
    note: str = ""
    successful_boards: set[tuple[str, str]] = field(default_factory=set)


from . import ats, linkedin, simplify, vansh, blocked  # noqa: E402

REGISTRY = {
    "simplify": simplify.fetch,
    "vansh": vansh.fetch,
    "linkedin": linkedin.fetch,
    "ats": ats.fetch,
    "wellfound": blocked.wellfound,
    "swelist": blocked.swelist,
    "yc": blocked.yc,
}

DEFAULT_SOURCES = ["ats", "simplify", "vansh", "linkedin"]

__all__ = ["SourceResult", "REGISTRY", "DEFAULT_SOURCES", "Job"]

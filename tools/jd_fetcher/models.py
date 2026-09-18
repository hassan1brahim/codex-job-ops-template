"""Typed contracts for the job scrape/normalize workflow."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class JobDescription:
    """Normalized job description content."""

    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    source: str = "manual"

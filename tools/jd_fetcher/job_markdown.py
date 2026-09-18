"""Markdown storage for scraped job descriptions.

The scraper returns a normalized `JobDescription`; this module decides how that
object is persisted as an editable Markdown document. Keeping this boundary
separate prevents fetch/parsing code from owning app database state.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import JobDescription


FRONTMATTER_BOUNDARY = "---"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, *, fallback: str = "job") -> str:
    """Return a filesystem-safe lowercase slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def default_job_filename(jd: JobDescription, *, created_at: str | None = None) -> str:
    """Build a readable stable-enough filename for a job Markdown document."""

    stamp = (created_at or now_iso())[:10]
    label = " ".join(part for part in [jd.company, jd.title] if part).strip()
    return f"{stamp}-{slugify(label)}.md"


def _frontmatter_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=True)
    return json.dumps("" if value is None else str(value), ensure_ascii=True)


def build_markdown(
    jd: JobDescription,
    *,
    status: str = "jd_ready",
    fetched_at: str | None = None,
) -> str:
    """Serialize a job description to Markdown with simple JSON-safe frontmatter."""

    timestamp = fetched_at or now_iso()
    metadata = {
        "url": jd.url,
        "company": jd.company,
        "title": jd.title,
        "location": jd.location,
        "source": jd.source,
        "status": status,
        "fetched_at": timestamp,
        "keywords": jd.keywords,
    }
    lines = [FRONTMATTER_BOUNDARY]
    lines.extend(f"{key}: {_frontmatter_value(value)}" for key, value in metadata.items())
    lines.extend([FRONTMATTER_BOUNDARY, "", jd.description.strip(), ""])
    return "\n".join(lines)


def write_job_markdown(
    jd: JobDescription,
    jobs_dir: str | Path,
    *,
    filename: str | None = None,
    status: str = "jd_ready",
    overwrite: bool = False,
) -> Path:
    """Write a `JobDescription` as Markdown and return the created path."""

    target_dir = Path(jobs_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / (filename or default_job_filename(jd))
    if path.exists() and not overwrite:
        stem = path.stem
        suffix = path.suffix
        counter = 2
        while path.exists():
            path = target_dir / f"{stem}-{counter}{suffix}"
            counter += 1
    path.write_text(build_markdown(jd, status=status), encoding="utf-8")
    return path

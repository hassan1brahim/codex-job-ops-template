"""Job-description fetching.

Descriptions are pulled lazily, highest-ranked jobs first, because fetching all
of them would be thousands of requests for roles you will never open. LinkedIn
has a dedicated guest endpoint; everything else goes through the same
`tools.jd_fetcher` the resume pipeline already uses, so a JD fetched here is
identical to one fetched by `jobops fetch`.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from .sources.linkedin import fetch_description as linkedin_description, fetch_detail  # noqa: E402
from .store import save_description, save_listing_metadata  # noqa: E402

MIN_USEFUL_CHARS = 200


def fetch_one(url: str) -> str | None:
    """Best-effort description text for any job URL."""
    if not url:
        return None
    if "linkedin.com" in url:
        text = linkedin_description(url)
        if text and len(text) >= MIN_USEFUL_CHARS:
            return text
        return None
    try:
        from tools.jd_fetcher import jd_intake

        jd = jd_intake.fetch_from_url(url)
    except Exception:  # noqa: BLE001 - a dead careers page must not stop the batch
        return None
    text = (getattr(jd, "description", "") or "").strip()
    return text if len(text) >= MIN_USEFUL_CHARS else None


def backfill(conn, rows, delay: tuple[float, float] = (1.0, 2.5), verbose: bool = True) -> dict:
    """Fetch descriptions for `rows`, saving as we go so a crash keeps progress."""
    got = failed = 0
    for i, row in enumerate(rows, 1):
        if "linkedin.com" in row["url"]:
            detail = fetch_detail(row["url"])
            text = detail.description
            save_listing_metadata(
                conn, row["key"], date_posted=detail.date_posted,
                applicant_count=detail.applicant_count,
            )
        else:
            text = fetch_one(row["url"])
        if text:
            save_description(conn, row["key"], text)
            got += 1
            status = f"{len(text)} chars"
        else:
            failed += 1
            status = "no description"
        if verbose:
            label = f'{row["company"]} - {row["title"]}'[:58]
            print(f"  [{i}/{len(rows)}] {label:60} {status}")
        if i < len(rows):
            time.sleep(random.uniform(*delay))
    return {"fetched": got, "failed": failed}

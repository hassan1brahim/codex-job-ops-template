"""Keep a Downloads folder in sync with ready-to-apply resume PDFs."""

from __future__ import annotations

import filecmp
import json
import re
import shutil
from pathlib import Path

from .config import profile_name


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "resumes"
MANIFEST_NAME = ".jobboard-resumes.json"


def _download_name(company: str, title: str) -> str:
    """Return a readable upload filename beginning with the company name."""

    def clean(value: str) -> str:
        return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", value)).strip("_")

    company_part = clean(company)[:60].rstrip("_") or "Company"
    title_part = clean(title)[:90].rstrip("_") or "Role"
    name_part = clean(profile_name())[:60].rstrip("_") or "Candidate"
    return f"{company_part}_{title_part}_{name_part}_Resume.pdf"


def _source_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def sync_ready_resumes(conn, download_dir: Path | None = None) -> dict[str, int | str]:
    """Copy ready PDFs to Downloads and remove only previously managed stale copies."""

    target = (download_dir or DEFAULT_DOWNLOAD_DIR).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / MANIFEST_NAME
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}

    desired: dict[str, str] = {}
    seen_sources: set[str] = set()
    rows = conn.execute(
        """SELECT company,title,resume_pdf_path FROM jobs
           WHERE status='READY_TO_APPLY' AND resume_pdf_path IS NOT NULL
           ORDER BY priority_score DESC"""
    ).fetchall()
    missing = 0
    for row in rows:
        source = _source_path(row["resume_pdf_path"])
        if source.suffix.lower() != ".pdf" or not source.is_file():
            missing += 1
            continue
        relative = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else str(source)
        if relative in seen_sources:
            continue
        seen_sources.add(relative)
        filename = _download_name(row["company"], row["title"])
        existing = desired.get(filename)
        if existing and existing != relative:
            raise ValueError(f"Resume filename collision: {filename}")
        desired[filename] = relative

    copied = 0
    errors = 0
    for filename, source_value in desired.items():
        source = _source_path(source_value)
        destination = target / filename
        try:
            if not destination.exists() or not filecmp.cmp(source, destination, shallow=False):
                shutil.copy2(source, destination)
                copied += 1
        except OSError:
            # A background launchd process may lack macOS privacy permission for
            # Downloads. Tracker/status writes must still succeed; a later manual
            # `sync-resumes` run can reconcile upload copies from the terminal.
            errors += 1

    removed = 0
    for filename in set(previous) - set(desired):
        path = target / filename
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            errors += 1

    try:
        manifest_path.write_text(
            json.dumps({"version": 1, "files": desired}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        errors += 1
    return {
        "ready": len(desired),
        "copied": copied,
        "removed": removed,
        "missing": missing,
        "errors": errors,
        "directory": str(target),
    }

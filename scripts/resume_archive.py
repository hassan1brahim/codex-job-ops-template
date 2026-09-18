#!/usr/bin/env python3
"""Archive generated resumes after their active review window expires."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
ARCHIVE_DIR = OUTPUT_DIR / "open-resumes"
COMPLETED_DIR = OUTPUT_DIR / "completed-resumes"
TRACKER = ROOT / "data" / "applications.md"
RESUME_SUFFIXES = {".docx", ".pdf", ".tex"}


def _resume_files(
    output_dir: Path,
    archive_dir: Path,
    completed_dir: Path,
) -> list[Path]:
    if not output_dir.exists():
        return []

    files: list[Path] = []
    for path in output_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in RESUME_SUFFIXES:
            continue
        if (
            archive_dir == path.parent
            or archive_dir in path.parents
            or completed_dir == path.parent
            or completed_dir in path.parents
        ):
            continue
        files.append(path)
    return sorted(files)


def _available_destination(path: Path, archive_dir: Path) -> Path:
    destination = archive_dir / path.name
    if not destination.exists():
        return destination

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(path.stat().st_mtime))
    candidate = archive_dir / f"{path.stem}__{timestamp}{path.suffix}"
    counter = 2
    while candidate.exists():
        candidate = archive_dir / f"{path.stem}__{timestamp}-{counter}{path.suffix}"
        counter += 1
    return candidate


def _update_tracker_links(moved: list[tuple[Path, Path]]) -> None:
    if not moved or not TRACKER.exists():
        return

    content = TRACKER.read_text(encoding="utf-8")
    updated = content
    for source, destination in moved:
        old_link = f"../{source.relative_to(ROOT).as_posix()}"
        new_link = f"../{destination.relative_to(ROOT).as_posix()}"
        updated = updated.replace(old_link, new_link)
    if updated != content:
        TRACKER.write_text(updated, encoding="utf-8")


def archive_resumes(
    *,
    older_than_hours: float = 24,
    archive_all: bool = False,
    now: float | None = None,
) -> list[tuple[Path, Path]]:
    """Move expired, unsubmitted resume artifacts into ``output/open-resumes``."""

    current_time = time.time() if now is None else now
    cutoff = current_time - (older_than_hours * 60 * 60)
    candidates = _resume_files(OUTPUT_DIR, ARCHIVE_DIR, COMPLETED_DIR)
    expired = [path for path in candidates if archive_all or path.stat().st_mtime <= cutoff]
    if not expired:
        return []

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    moved: list[tuple[Path, Path]] = []
    for source in expired:
        destination = _available_destination(source, ARCHIVE_DIR)
        shutil.move(str(source), str(destination))
        moved.append((source, destination))

    for directory in sorted(OUTPUT_DIR.rglob("*"), reverse=True):
        if directory.is_dir() and directory not in {ARCHIVE_DIR, COMPLETED_DIR}:
            try:
                directory.rmdir()
            except OSError:
                pass
    _update_tracker_links(moved)
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive generated resume files.")
    parser.add_argument(
        "--older-than-hours",
        type=float,
        default=24,
        help="Archive files at least this old (default: 24 hours).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Archive every current resume regardless of age.",
    )
    args = parser.parse_args()

    moved = archive_resumes(
        older_than_hours=args.older_than_hours,
        archive_all=args.all,
    )
    if not moved:
        print("No resumes were old enough to archive.")
        return 0

    for source, destination in moved:
        print(f"{source.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")
    print(f"Archived {len(moved)} resume(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

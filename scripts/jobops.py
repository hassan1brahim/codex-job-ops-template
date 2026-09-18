#!/usr/bin/env python3
"""Local helper CLI for the Codex job-ops workspace."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from resume_archive import archive_resumes

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JDS_DIR = ROOT / "jds"
PIPELINE = ROOT / "data" / "pipeline.md"
TRACKER = ROOT / "data" / "applications.md"
REQUIRED = [
    ROOT / "AGENTS.md",
    ROOT / "CLAUDE.md",
    ROOT / "DATA_CONTRACT.md",
    ROOT / "docs" / "MAINTENANCE.md",
    ROOT / "modes" / "resume.md",
    ROOT / "templates" / "resume.tex",
    ROOT / "scripts" / "resume_check.py",
    ROOT / "config" / "profile.yml",
    ROOT / "profile" / "master.md",
    PIPELINE,
    TRACKER,
    ROOT / "data" / "reminders.md",
]
REQUIRED_RESUME_TOOLS = ["tectonic", "pdfinfo", "pdftotext"]


def cmd_doctor(_: argparse.Namespace) -> int:
    missing = [path.relative_to(ROOT).as_posix() for path in REQUIRED if not path.exists()]
    missing_tools = [tool for tool in REQUIRED_RESUME_TOOLS if shutil.which(tool) is None]
    for directory in [JDS_DIR, ROOT / "reports", ROOT / "output"]:
        directory.mkdir(parents=True, exist_ok=True)

    if missing or missing_tools:
        if missing:
            print("Missing required files:")
            for path in missing:
                print(f"- {path}")
        if missing_tools:
            print("Missing resume validation tools:")
            for tool in missing_tools:
                print(f"- {tool}")
        return 1

    print("job-ops workspace OK")
    return 0


def cmd_add_url(args: argparse.Namespace) -> int:
    PIPELINE.parent.mkdir(parents=True, exist_ok=True)
    if not PIPELINE.exists():
        PIPELINE.write_text("# Job Pipeline\n\n", encoding="utf-8")
    with PIPELINE.open("a", encoding="utf-8") as handle:
        label = args.label or "New role"
        handle.write(f"- [ ] {label} | {args.url}\n")
    print(f"Added to {PIPELINE.relative_to(ROOT)}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from tools.jd_fetcher import jd_intake
    from tools.jd_fetcher.jd_intake import JobDescriptionFetchError
    from tools.jd_fetcher.job_markdown import write_job_markdown

    try:
        jd = jd_intake.fetch_from_url(
            args.url,
            title=args.title or "",
            company=args.company or "",
            location=args.location or "",
        )
    except (JobDescriptionFetchError, ValueError) as exc:
        print(f"Could not fetch JD: {exc}", file=sys.stderr)
        return 1

    path = write_job_markdown(
        jd,
        JDS_DIR,
        filename=args.filename,
        overwrite=args.overwrite,
    )
    print(path.relative_to(ROOT))
    print(f"{jd.company or '(unknown company)'} - {jd.title or '(untitled)'}")
    print(f"{len(jd.description)} chars, {len(jd.keywords)} keywords")
    return 0


def cmd_tracker(_: argparse.Namespace) -> int:
    if not TRACKER.exists():
        print("No tracker yet.")
        return 1
    lines = TRACKER.read_text(encoding="utf-8").splitlines()
    rows = [line for line in lines if line.startswith("|") and not re.match(r"^\|[-:| ]+\|$", line)]
    print("\n".join(rows[:1] + rows[2:]) if len(rows) > 2 else TRACKER.read_text(encoding="utf-8"))
    return 0


def cmd_check_resume(args: argparse.Namespace) -> int:
    from resume_check import ResumeCheckError, validate_resume

    try:
        metrics = validate_resume(
            args.tex,
            output_pdf=args.output_pdf,
            min_fill=args.min_fill,
        )
    except ResumeCheckError as exc:
        print(f"resume check FAILED: {exc}", file=sys.stderr)
        return 1

    print("resume check PASSED")
    print(f"PDF: {metrics.pdf}")
    print(f"Pages: {metrics.pages}")
    print(f"Page fill: {metrics.fill_ratio:.1%}")
    print(f"Extracted words: {metrics.words}")
    print("Keywords:")
    for index, keyword in enumerate(metrics.keywords, start=1):
        print(f"  {index:02d}. {keyword}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobops")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check required workspace files.").set_defaults(func=cmd_doctor)

    add_url = sub.add_parser("add-url", help="Append a URL to data/pipeline.md.")
    add_url.add_argument("url")
    add_url.add_argument("--label", default="")
    add_url.set_defaults(func=cmd_add_url)

    fetch = sub.add_parser("fetch", help="Fetch a job URL into jds/.")
    fetch.add_argument("url")
    fetch.add_argument("--title", default="")
    fetch.add_argument("--company", default="")
    fetch.add_argument("--location", default="")
    fetch.add_argument("--filename", default="")
    fetch.add_argument("--overwrite", action="store_true")
    fetch.set_defaults(func=cmd_fetch)

    sub.add_parser("tracker", help="Print the application tracker.").set_defaults(func=cmd_tracker)

    check_resume = sub.add_parser(
        "check-resume",
        help="Compile and validate a one-page LaTeX resume.",
    )
    check_resume.add_argument("tex", type=Path)
    check_resume.add_argument("--output-pdf", type=Path)
    check_resume.add_argument("--min-fill", type=float, default=0.88)
    check_resume.set_defaults(func=cmd_check_resume)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "check-resume":
        moved = archive_resumes()
        if moved:
            print(f"Archived {len(moved)} resume(s) older than 24 hours.")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for the central Job App Agent app."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import AppConfig
from . import jd_intake
from .jd_intake import JobDescriptionFetchError
from .job_markdown import write_job_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="module_1_jd_fetcher")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser(
        "scrape",
        help="Fetch a job URL, normalize the description, and save it as Markdown.",
    )
    scrape.add_argument("url")
    scrape.add_argument("--title", default="")
    scrape.add_argument("--company", default="")
    scrape.add_argument("--location", default="")
    scrape.add_argument("--jobs-dir", type=Path, default=None)
    scrape.add_argument("--filename", default="")
    scrape.add_argument("--overwrite", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig.from_env()

    if args.command == "scrape":
        try:
            jd = jd_intake.fetch_from_url(
                args.url,
                title=args.title,
                company=args.company,
                location=args.location,
            )
        except (JobDescriptionFetchError, ValueError) as exc:
            print(f"Could not scrape this URL: {exc}", file=sys.stderr)
            return 1
        path = write_job_markdown(
            jd,
            args.jobs_dir or config.jobs_dir,
            filename=args.filename or None,
            overwrite=args.overwrite,
        )
        print(f"Saved job Markdown: {path}")
        print(f"{jd.company or '(unknown company)'} - {jd.title or '(untitled)'}")
        print(f"{len(jd.description)} chars, {len(jd.keywords)} keywords")
        return 0

    raise ValueError(f"Unknown command: {args.command}")

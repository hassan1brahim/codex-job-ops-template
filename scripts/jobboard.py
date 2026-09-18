#!/usr/bin/env python3
"""Job board CLI: discover internships, rank them by explainable priority, and hand
the good ones to the existing resume pipeline.

    jobboard refresh              pull every source, store new postings
    jobboard list --tier 1        show the board, NJ first
    jobboard describe --limit 25  backfill job descriptions
    jobboard notify               macOS notification for new high-priority jobs
    jobboard queue <key>          push a job into data/pipeline.md for Codex
    jobboard next                 print the top unworked job as a Codex brief
    jobboard render               write the HTML board
    jobboard status               source health and counts
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board import store  # noqa: E402
from board.filters import BoardConfig  # noqa: E402
from board.geo import TIER_LABELS  # noqa: E402
from board.resume_downloads import sync_ready_resumes  # noqa: E402
from board.sources import DEFAULT_SOURCES, REGISTRY  # noqa: E402

PIPELINE = ROOT / "data" / "pipeline.md"


def _change_status(conn, key: str, status: str) -> bool:
    """Change workflow state and keep duplicate-source resume rows together."""

    changed = store.set_status(conn, key, status)
    if not changed:
        return False
    normalized = status.upper().replace(" ", "_")
    if normalized != "READY_TO_APPLY":
        row = conn.execute(
            "SELECT resume_pdf_path FROM jobs WHERE key=?", (key,)
        ).fetchone()
        if row and row["resume_pdf_path"]:
            duplicates = conn.execute(
                """SELECT key FROM jobs
                   WHERE key<>? AND resume_pdf_path=? AND status='READY_TO_APPLY'""",
                (key, row["resume_pdf_path"]),
            ).fetchall()
            for duplicate in duplicates:
                store.set_status(conn, duplicate["key"], normalized)
    sync_ready_resumes(conn)
    return True


def _cfg(args) -> BoardConfig:
    cfg = BoardConfig()
    if getattr(args, "max_tier", None) is not None:
        cfg.max_tier = args.max_tier
    if getattr(args, "linkedin_pages", None):
        cfg.linkedin_pages = args.linkedin_pages
    if getattr(args, "terms", None):
        cfg.terms = set(args.terms)
    return cfg


def _age_days(newest: int | None) -> int:
    return int((time.time() - newest) // 86400) if newest else 0


_STATE_LABEL = {"ok": "ok", "degraded": "DEGRADED", "undated": "UNDATED",
                "empty": "EMPTY", "unknown": "unknown", "blocked": "BLOCKED"}

_HEALTH_NOTE = {
    "degraded": lambda newest: f"stale: newest posting {_age_days(newest)}d old",
    "undated": lambda newest: "unverifiable: no listing carries a posting date",
    "empty": lambda newest: "empty: source returned no matching listings",
}


def cmd_refresh(args) -> int:
    cfg = _cfg(args)
    names = args.sources or DEFAULT_SOURCES
    if args.all:
        names = list(REGISTRY)
    conn = store.connect()
    total_new = total_upd = 0
    degraded: list[str] = []

    for name in names:
        fetcher = REGISTRY.get(name)
        if not fetcher:
            print(f"! unknown source: {name}", file=sys.stderr)
            continue
        print(f"-> {name} ...", flush=True)
        started = time.time()
        try:
            result = fetcher(cfg)
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort the run
            store.record_run(conn, name, False, note=f"crash: {exc}")
            print(f"   FAILED: {exc}")
            continue

        if not result.ok and not result.jobs:
            store.record_run(conn, name, False, result.fetched, 0, result.note)
            print(f"   blocked: {result.note}")
            continue

        new, upd = store.upsert(conn, result.jobs)
        removed = 0
        if name == "ats" and result.ok:
            removed = store.mark_inactive_missing_ats(conn, result.jobs, result.successful_boards)
        elif result.ok and "failed" not in (result.note or ""):
            removed = store.mark_inactive_missing(conn, name, {job.key for job in result.jobs})
        health, newest = store.classify_run(result.jobs, args.stale_days)
        note = result.note
        if health != "ok":
            note = f"{_HEALTH_NOTE[health](newest)}; {note}" if note else _HEALTH_NOTE[health](newest)
            degraded.append(name)
        store.record_run(conn, name, True, result.fetched, len(result.jobs), note,
                         status=health, newest_posting=newest)
        total_new += new
        total_upd += upd
        secs = time.time() - started
        print(
            f"   {result.fetched} fetched, {len(result.jobs)} relevant, "
            f"{new} new, {upd} updated, {removed} removed ({secs:.1f}s) {result.note}"
        )
        if health != "ok":
            print(f"   ! DEGRADED: {_HEALTH_NOTE[health](newest)}")

    print(f"\n{total_new} new, {total_upd} updated.")
    if degraded:
        print(f"! degraded sources: {', '.join(degraded)} "
              f"(answered, but carried nothing posted within {args.stale_days}d)")
    s = store.stats(conn)
    print(f"Board: {s['active']} active, {s['described']} with descriptions, {s['unnotified']} unannounced.")
    return 0


def cmd_list(args) -> int:
    conn = store.connect()
    rows = store.all_jobs(conn)
    rows = [r for r in rows if r["tier"] <= (args.tier if args.tier is not None else 5)]
    if args.company:
        rows = [r for r in rows if args.company.lower() in r["company"].lower()]
    if args.new:
        rows = [r for r in rows if r["status"] == "new"]
    if not rows:
        print("No jobs match. Run: jobboard refresh")
        return 0

    print(f"\n{'=' * 100}\n  PRIORITY BOARD\n{'=' * 100}")
    shown = 0
    for row in rows:
        if shown >= args.limit:
            break
        dist = f'{row["distance_mi"]:.0f}mi' if row["distance_mi"] is not None else ""
        desc = "D" if row["description"] else " "
        flag = "*" if row["status"] != "NEW" else " "
        print(f'{flag}{desc} {row["priority_score"]:3} {row["key"]}  {row["company"][:26]:26}  {row["title"][:44]:44}  '
              f'{TIER_LABELS[row["tier"]][:12]:12} {(row["place_label"] or "")[:18]:18} {dist:>6}')
        shown += 1
    print(f"\n{shown} shown of {len(rows)}. Number = priority, D = JD cached, * = in workflow.")
    return 0


def cmd_describe(args) -> int:
    from board.describe import backfill

    conn = store.connect()
    rows = store.needing_description(conn, args.limit, max_tier=args.tier)
    if not rows:
        print("Every job in range already has a description.")
        return 0
    print(f"Fetching {len(rows)} descriptions (tier <= {args.tier}) ...")
    out = backfill(conn, rows)
    print(f"\n{out['fetched']} fetched, {out['failed']} unavailable.")
    return 0


def cmd_notify(args) -> int:
    from board.notify import send

    conn = store.connect()
    if args.mark_seen:
        # Everything on a freshly built board is 'new' only because the board is
        # new. Baseline once so the first scheduled run does not announce 1,500
        # jobs, and only genuinely new postings notify from then on.
        rows = conn.execute("SELECT key FROM jobs WHERE notified = 0").fetchall()
        store.mark_notified(conn, [r["key"] for r in rows])
        print(f"{len(rows)} existing jobs marked as seen. Future runs announce only new postings.")
        return 0
    out = send(conn, max_tier=args.tier, limit=args.limit, dry_run=args.dry_run)
    if out["new"] == 0:
        print("Nothing new to announce.")
    elif args.dry_run:
        print(f"{out['new']} would be announced (dry run, nothing marked).")
    else:
        print(f"{out['new']} new jobs announced ({out['sent']} notifications).")
    return 0


def _queue_row(conn, row) -> bool:
    """Push one row into the durable Codex pipeline. Return True when newly added."""
    score = row["priority_score"] or 0
    reason = "; ".join(json.loads(row["score_reasons"] or "[]")[:3])
    line = (
        f'- [ ] {row["company"]} | {row["title"]} | priority {score} | '
        f'{row["url"]} | board:{row["key"]}'
    )
    if reason:
        line += f" | {reason}"
    line += "\n"
    PIPELINE.parent.mkdir(parents=True, exist_ok=True)
    if not PIPELINE.exists():
        PIPELINE.write_text("# Job Pipeline\n\n", encoding="utf-8")
    existing = PIPELINE.read_text(encoding="utf-8")
    if row["url"] in existing or f'board:{row["key"]}' in existing:
        store.set_status(conn, row["key"], "EVALUATING")
        return False
    with PIPELINE.open("a", encoding="utf-8") as handle:
        handle.write(line)
    store.set_status(conn, row["key"], "EVALUATING")
    return True


def cmd_queue(args) -> int:
    """Push a job into data/pipeline.md, the queue the resume pipeline reads."""
    conn = store.connect()
    row = conn.execute("SELECT * FROM jobs WHERE key = ?", (args.key,)).fetchone()
    if not row:
        print(f"No job with key {args.key}. Run: jobboard list", file=sys.stderr)
        return 1

    if _queue_row(conn, row):
        print(f'Queued: {row["company"]} | {row["title"]} | priority {row["priority_score"]}')
    else:
        print("Already in the pipeline.")
    return 0


def cmd_autoqueue(args) -> int:
    """Queue only the highest-priority new jobs for Codex evaluation."""
    conn = store.connect()
    cutoff = int(time.time()) - args.max_age_hours * 3600
    # LinkedIn search cards provide the source date but not competition. Enrich
    # only the small candidate set we may queue, avoiding broad detail scraping.
    linkedin_candidates = conn.execute(
        """SELECT * FROM jobs WHERE active=1 AND status='NEW' AND source='linkedin'
           AND tier<=? AND date_posted IS NOT NULL AND date_posted>=?
           ORDER BY priority_score DESC, freshness_score DESC LIMIT ?""",
        (args.tier, cutoff, args.limit),
    ).fetchall()
    if linkedin_candidates:
        from board.sources.linkedin import fetch_detail

        for candidate in linkedin_candidates:
            try:
                detail = fetch_detail(candidate["url"])
            except RuntimeError:
                break
            if detail.description and not candidate["description"]:
                store.save_description(conn, candidate["key"], detail.description)
            store.save_listing_metadata(
                conn, candidate["key"], date_posted=detail.date_posted,
                applicant_count=detail.applicant_count,
            )
    rows = conn.execute(
        """SELECT * FROM jobs WHERE active=1 AND status='NEW' AND priority_score>=?
           AND tier<=? AND date_posted IS NOT NULL AND date_posted>=?
           ORDER BY priority_score DESC, freshness_score DESC LIMIT ?""",
        (args.min_score, args.tier, cutoff, args.limit),
    ).fetchall()
    added = 0
    for row in rows:
        if _queue_row(conn, row):
            added += 1
            print(f'+ {row["priority_score"]}: {row["company"]} — {row["title"]}')
    print(f"{added} job(s) queued for Codex evaluation.")
    return 0


def cmd_next(args) -> int:
    """Print the highest-ranked unworked job as a brief for Codex/Claude."""
    conn = store.connect()
    row = conn.execute(
        """SELECT * FROM jobs WHERE active = 1 AND status = 'NEW' AND tier <= ?
           ORDER BY priority_score DESC, freshness_score DESC LIMIT 1""",
        (args.tier,),
    ).fetchone()
    if not row:
        print("Nothing unworked in range. Try a higher --tier or run refresh.")
        return 1

    print(f'KEY:      {row["key"]}')
    print(f'COMPANY:  {row["company"]}')
    print(f'ROLE:     {row["title"]}')
    print(f'LOCATION: {row["place_label"]}  ({TIER_LABELS[row["tier"]]})')
    print(f'TERMS:    {row["terms"] or "unspecified"}')
    print(f'SOURCE:   {row["source"]}')
    print(f'PRIORITY: {row["priority_score"]}/100')
    print(f'URL:      {row["url"]}')
    if row["sponsorship"] and row["sponsorship"] != "Other":
        print(f'SPONSOR:  {row["sponsorship"]}')
    print()
    if row["description"]:
        print("DESCRIPTION:")
        print(row["description"][:args.chars])
        if len(row["description"]) > args.chars:
            print(f'... [{len(row["description"]) - args.chars} more chars]')
    else:
        print("DESCRIPTION: not fetched. Run: jobboard describe --limit 10")
    return 0


def cmd_brief(args) -> int:
    conn = store.connect()
    row = conn.execute("SELECT * FROM jobs WHERE key=?", (args.key,)).fetchone()
    if not row:
        print(f"No job with key {args.key}", file=sys.stderr)
        return 1
    print(f'KEY:      {row["key"]}')
    print(f'COMPANY:  {row["company"]}')
    print(f'ROLE:     {row["title"]}')
    print(f'LOCATION: {row["place_label"]}  ({TIER_LABELS[row["tier"]]})')
    print(f'PRIORITY: {row["priority_score"]}/100')
    print(f'STATUS:   {row["status"]}')
    print(f'URL:      {row["url"]}')
    print("\nDESCRIPTION:")
    print((row["description"] or "not fetched")[:args.chars])
    return 0


def cmd_process(args) -> int:
    """Run isolated Codex workers for the best queued jobs; never submits applications."""
    from board.authorization import profile_summary
    from board.describe import fetch_one

    auth = profile_summary()
    if "unknown" in auth.lower():
        print(f"Autoprocess paused: {auth}", file=sys.stderr)
        print("Set constraints.work_authorization and visa_sponsorship_needed in config/profile.yml.", file=sys.stderr)
        return 2
    conn = store.connect()
    recent_cutoff = int(time.time()) - args.prefer_new_hours * 3600
    max_age_cutoff = (
        int(time.time()) - args.max_age_hours * 3600
        if args.max_age_hours is not None else None
    )
    rows = conn.execute(
        """SELECT * FROM jobs WHERE active=1 AND status='EVALUATING'
           AND (resume_requested_at IS NOT NULL
                OR ? IS NULL OR (date_posted IS NOT NULL AND date_posted>=?))
           ORDER BY CASE WHEN resume_requested_at IS NOT NULL THEN 0 ELSE 1 END,
                    resume_requested_at,
                    CASE WHEN date_posted IS NOT NULL AND date_posted>=? THEN 0 ELSE 1 END,
                    CASE WHEN date_posted>=? THEN date_posted END DESC,
                    priority_score DESC, freshness_score DESC LIMIT ?""",
        (max_age_cutoff, max_age_cutoff, recent_cutoff, recent_cutoff, args.limit),
    ).fetchall()
    if not rows:
        print("No queued jobs await evaluation.")
        return 0
    for row in rows:
        if not row["description"]:
            print(f'Fetching fresh JD: {row["company"]} — {row["title"]}', flush=True)
            description = fetch_one(row["url"])
            if description:
                store.save_description(conn, row["key"], description)
                row = conn.execute("SELECT * FROM jobs WHERE key=?", (row["key"],)).fetchone()
            else:
                print(f'No usable JD for {row["key"]}; leaving it queued for retry.', file=sys.stderr)
                continue
        prompt = f"""Process job-board key {row['key']} end to end inside this workspace.
Read AGENTS.md, DATA_CONTRACT.md, modes/evaluate.md, and modes/resume.md first.
Run `python scripts/jobboard.py brief {row['key']}` for the exact role and cached JD.
Create the evidence-grounded fit report. Generate a tailored resume only when the evaluation recommends applying.
Never invent facts, technologies, metrics, dates, GPA, or responsibilities. Never submit an application or message anyone.
If you generate a resume, validate it with check-resume, then attach every artifact with
`python scripts/jobboard.py artifacts {row['key']} --fit-score SCORE --fit-report PATH --resume-tex PATH --resume-pdf PATH`.
If you only create a report, still attach it with the artifacts command and leave status EVALUATING for human review.
"""
        print(f'Processing {row["priority_score"]}: {row["company"]} — {row["title"]}', flush=True)
        result = subprocess.run(
            ["codex", "-a", "never", "exec", "--ephemeral", "-C", str(ROOT),
             "-s", "workspace-write", prompt], cwd=ROOT, timeout=args.timeout,
        )
        if result.returncode:
            print(f'Codex failed for {row["key"]} (exit {result.returncode}).', file=sys.stderr)
    return 0


def cmd_render(args) -> int:
    from board.render import render

    conn = store.connect()
    out = Path(args.out) if args.out else ROOT / "output" / "board.html"
    path = render(conn, out, max_tier=args.tier)
    print(f"Wrote {path}")
    return 0


def cmd_recompute(args) -> int:
    """Re-derive locations for stored rows after a geo.py improvement."""
    conn = store.connect()
    changed = store.recompute_places(conn)
    scored = store.recompute_scores(conn)
    print(f"{changed} rows re-placed; {scored} priority scores refreshed.")
    return 0


def cmd_set_status(args) -> int:
    conn = store.connect()
    try:
        changed = _change_status(conn, args.key, args.status)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not changed:
        print(f"No job with key {args.key}", file=sys.stderr)
        return 1
    print(f"{args.key} → {args.status.upper().replace(' ', '_')}")
    return 0


def cmd_sync_resumes(_args) -> int:
    result = sync_ready_resumes(store.connect())
    print(
        f'{result["ready"]} ready resumes in {result["directory"]} '
        f'({result["copied"]} copied, {result["removed"]} removed, '
        f'{result["missing"]} missing, {result["errors"]} permission/I/O errors).'
    )
    return 0


def cmd_check_urls(args) -> int:
    result = store.check_urls(store.connect(), limit=args.limit, older_than_hours=args.older_than)
    print(f'{result["checked"]} checked, {result["closed"]} likely closed, {result["unknown"]} inconclusive.')
    return 0


def cmd_artifacts(args) -> int:
    conn = store.connect()
    fit_score = args.fit_score
    if fit_score is not None:
        if 0 <= fit_score <= 5:
            fit_score = round(fit_score * 20)
        elif 0 <= fit_score <= 100:
            fit_score = round(fit_score)
        else:
            print("Fit score must be between 0 and 5 or between 0 and 100.", file=sys.stderr)
            return 2
    if not store.save_artifacts(
        conn, args.key, fit_score=fit_score, fit_report_path=args.fit_report,
        resume_tex_path=args.resume_tex, resume_pdf_path=args.resume_pdf,
    ):
        print(f"No job with key {args.key}", file=sys.stderr)
        return 1
    if args.resume_tex or args.resume_pdf:
        store.set_status(conn, args.key, "READY_TO_APPLY")
        sync_ready_resumes(conn)
    print(f"Artifacts saved for {args.key}.")
    return 0


def cmd_requests(args) -> int:
    """List the jobs the user flagged for a tailored resume.

    This is the handoff: the board button records the intent, and an agent
    runs this to learn which resumes to build, in the order they were asked for.
    """
    conn = store.connect()
    rows = store.resume_requests(conn, pending_only=not args.all)
    if not rows:
        print("No resume requests pending. Use the Tailor resume button on the board.")
        return 0
    if args.json:
        print(json.dumps([
            {
                "key": r["key"], "company": r["company"], "title": r["title"],
                "url": r["canonical_url"] or r["url"], "status": r["status"],
                "priority": r["priority_score"], "requested_at": r["resume_requested_at"],
                "has_description": bool(r["description"]),
                "resume_pdf": r["resume_pdf_path"] or None,
                "fit_report": r["fit_report_path"] or None,
            }
            for r in rows
        ], indent=2))
        return 0
    print(f"{len(rows)} resume request(s), oldest first:\n")
    for row in rows:
        asked = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["resume_requested_at"]))
        jd = "JD cached" if row["description"] else "NO JD - process will fetch it"
        print(f'  {row["company"]} | {row["title"]}')
        print(f'    key       {row["key"]}')
        print(f'    requested {asked}   priority {row["priority_score"]}   {row["status"]}')
        print(f'    {jd}')
        print(f'    {row["canonical_url"] or row["url"]}')
        if row["resume_pdf_path"]:
            print(f'    resume    {row["resume_pdf_path"]}')
        print()
    print("Build them with:  python scripts/jobboard.py process --limit 1")
    print("Tailoring rules:  modes/resume.md")
    return 0


def cmd_save(args) -> int:
    """Flag a job to come back to, without moving it through the pipeline."""
    conn = store.connect()
    wanted = args.state != "off"
    if not store.set_saved(conn, args.key, wanted):
        print(f"! unknown job: {args.key}", file=sys.stderr)
        return 1
    print(f"{args.key} {'saved' if wanted else 'unsaved'}")
    return 0


def cmd_seen(args) -> int:
    """Stamp jobs as read. Already-seen jobs keep their original stamp."""
    conn = store.connect()
    print(f"{store.mark_seen(conn, args.keys)} marked seen")
    return 0


def cmd_serve(args) -> int:
    """Serve the board locally with the one write endpoint the UI needs."""
    from board.render import render

    render(store.connect(), ROOT / "output" / "board.html", max_tier=args.tier)
    os.chdir(ROOT)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/output/board.html")
                self.end_headers()
                return
            super().do_GET()

        def _reply(self, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - stdlib handler API
            routes = ("/api/status", "/api/triage", "/api/recruiter", "/api/request-resume")
            if self.path not in routes:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                conn = store.connect()
                result = {"ok": True}

                if self.path == "/api/status":
                    if not _change_status(conn, payload["key"], payload["status"]):
                        raise KeyError("unknown job")
                elif self.path == "/api/request-resume":
                    key = payload["key"]
                    wanted = bool(payload.get("wanted", True))
                    if not store.request_resume(conn, key, wanted):
                        raise KeyError("unknown job")
                    if wanted:
                        row = conn.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
                        _queue_row(conn, row)
                elif self.path == "/api/triage":
                    key = payload["key"]
                    if payload.get("seen"):
                        store.mark_seen(conn, [key])
                    if "saved" in payload and not store.set_saved(conn, key, bool(payload["saved"])):
                        raise KeyError("unknown job")
                else:
                    result["id"] = store.save_recruiter(
                        conn, payload["key"],
                        contact_id=payload.get("id") or None,
                        name=payload.get("name", ""),
                        profile_url=payload.get("profile_url", ""),
                        message_url=payload.get("message_url", ""),
                        status=payload.get("status", "none"),
                        contacted_at=payload.get("contacted_at"),
                        notes=payload.get("notes", ""),
                    )

                # Marking a row seen happens on every row open; re-rendering the
                # whole page for that would make opening a job cost a full rebuild.
                if self.path != "/api/triage" or "saved" in payload:
                    render(store.connect(), ROOT / "output" / "board.html", max_tier=args.tier)
                self._reply(result)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                self.send_error(400, str(exc))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Job board: http://127.0.0.1:{args.port}/")
    print("Status changes write directly to data/board.db. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_status(args) -> int:
    conn = store.connect()
    s = store.stats(conn)
    print("BOARD")
    print(f'  {s["total"]} tracked, {s["active"]} active')
    print(f'  {s["described"]} with descriptions, {s["queued"]} queued, {s["unnotified"]} unannounced')
    rows = conn.execute(
        """SELECT tier, COUNT(*) n FROM jobs WHERE active=1 GROUP BY tier ORDER BY tier"""
    ).fetchall()
    print("\nBY LOCATION")
    for row in rows:
        print(f'  {TIER_LABELS[row["tier"]]:16} {row["n"]}')
    print("\nSOURCES")
    for run in store.latest_runs(conn):
        age = (time.time() - run["ran_at"]) / 3600
        state = "BLOCKED" if not run["ok"] else _STATE_LABEL.get(run["status"], run["status"].upper())
        newest = run["newest_posting"]
        freshest = f"newest {_age_days(newest)}d" if newest else "newest   ?"
        print(f'  {run["source"]:11} {state:8} {run["kept"]:5} kept  {age:5.1f}h ago  '
              f'{freshest:11} {(run["note"] or "")[:52]}')
    return 0


def cmd_analytics(args) -> int:
    data = store.analytics(store.connect())
    print(f'Applications: {data["applications"]}')
    print(f'OA rate:      {data["oa_rate"]:.1f}% ({data["oa"]})')
    print(f'Interview:    {data["interview_rate"]:.1f}% ({data["interviews"]})')
    print("\nWORKFLOW REACHED")
    for stage in ("EVALUATING", "READY_TO_APPLY", "APPLIED", "OA", "INTERVIEW", "OFFER", "REJECTED"):
        print(f'  {stage:18} {data["stages"][stage]}')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobboard", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ref = sub.add_parser("refresh", help="Pull all sources into the board.")
    ref.add_argument("--sources", nargs="*", help=f"Subset of {list(REGISTRY)}")
    ref.add_argument("--all", action="store_true", help="Include known-blocked sources.")
    ref.add_argument("--max-tier", type=int, default=4)
    ref.add_argument("--linkedin-pages", type=int)
    ref.add_argument("--terms", nargs="*")
    ref.add_argument("--stale-days", type=int, default=store.STALE_SOURCE_DAYS,
                     help="Flag a source as degraded when its newest posting is older than this.")
    ref.set_defaults(func=cmd_refresh)

    req = sub.add_parser("requests", help="List jobs flagged for a tailored resume.")
    req.add_argument("--json", action="store_true", help="Machine-readable, for agents.")
    req.add_argument("--all", action="store_true", help="Include requests already fulfilled.")
    req.set_defaults(func=cmd_requests)

    sav = sub.add_parser("save", help="Flag a job to return to later.")
    sav.add_argument("key")
    sav.add_argument("state", nargs="?", default="on", choices=["on", "off"])
    sav.set_defaults(func=cmd_save)

    see = sub.add_parser("seen", help="Mark jobs as read.")
    see.add_argument("keys", nargs="+")
    see.set_defaults(func=cmd_seen)

    lst = sub.add_parser("list", help="Show the highest-priority jobs first.")
    lst.add_argument("--tier", type=int, default=None, help="0 NJ, 1 NY, 2 remote, 3 US, 4 intl")
    lst.add_argument("--limit", type=int, default=60)
    lst.add_argument("--company")
    lst.add_argument("--new", action="store_true", help="Only jobs not yet queued.")
    lst.set_defaults(func=cmd_list)

    des = sub.add_parser("describe", help="Backfill job descriptions.")
    des.add_argument("--limit", type=int, default=20)
    des.add_argument("--tier", type=int, default=3)
    des.set_defaults(func=cmd_describe)

    nfy = sub.add_parser("notify", help="Announce new high-priority jobs.")
    nfy.add_argument("--tier", type=int, default=3)
    nfy.add_argument("--limit", type=int, default=8)
    nfy.add_argument("--dry-run", action="store_true")
    nfy.add_argument("--mark-seen", action="store_true",
                     help="Baseline: mark everything current as seen without notifying.")
    nfy.set_defaults(func=cmd_notify)

    que = sub.add_parser("queue", help="Send a job to data/pipeline.md.")
    que.add_argument("key")
    que.set_defaults(func=cmd_queue)

    auto = sub.add_parser("autoqueue", help="Queue top-scoring new jobs for Codex evaluation.")
    auto.add_argument("--limit", type=int, default=5)
    auto.add_argument("--min-score", type=int, default=75)
    auto.add_argument("--tier", type=int, default=3)
    auto.add_argument("--max-age-hours", type=int, default=24,
                      help="Only queue jobs first discovered this recently.")
    auto.set_defaults(func=cmd_autoqueue)

    nxt = sub.add_parser("next", help="Print the top unworked job as a brief.")
    nxt.add_argument("--tier", type=int, default=3)
    nxt.add_argument("--chars", type=int, default=6000)
    nxt.set_defaults(func=cmd_next)

    brief = sub.add_parser("brief", help="Print one exact job for evaluation.")
    brief.add_argument("key")
    brief.add_argument("--chars", type=int, default=12000)
    brief.set_defaults(func=cmd_brief)

    process = sub.add_parser("process", help="Run Codex on the best queued jobs (never applies).")
    process.add_argument("--limit", type=int, default=1)
    process.add_argument("--timeout", type=int, default=1800)
    process.add_argument(
        "--prefer-new-hours", type=int, default=6,
        help="Prioritize newly discovered queued roles within this window (default: 6).",
    )
    process.add_argument(
        "--max-age-hours", type=int,
        help="Only process roles discovered within this many hours.",
    )
    process.set_defaults(func=cmd_process)

    ren = sub.add_parser("render", help="Write the HTML board.")
    ren.add_argument("--out")
    ren.add_argument("--tier", type=int, default=4)
    ren.set_defaults(func=cmd_render)

    sub.add_parser(
        "recompute", help="Re-derive tier/distance for stored rows (after a geo update)."
    ).set_defaults(func=cmd_recompute)
    sub.add_parser("status", help="Source health and counts.").set_defaults(func=cmd_status)
    sub.add_parser("analytics", help="Application funnel rates from workflow history.").set_defaults(func=cmd_analytics)

    state = sub.add_parser("set-status", help="Move a job through the application workflow.")
    state.add_argument("key")
    state.add_argument("status", choices=sorted(store.WORKFLOW_STATUSES))
    state.set_defaults(func=cmd_set_status)

    sub.add_parser(
        "sync-resumes", help="Sync ready-to-apply PDFs into ~/Downloads/resumes."
    ).set_defaults(func=cmd_sync_resumes)

    links = sub.add_parser("check-urls", help="Recheck application links for definitive closure.")
    links.add_argument("--limit", type=int, default=30)
    links.add_argument("--older-than", type=int, default=24, help="Minimum hours since last check.")
    links.set_defaults(func=cmd_check_urls)

    art = sub.add_parser("artifacts", help="Attach a fit report and tailored resume to a job.")
    art.add_argument("key")
    art.add_argument("--fit-score", type=float, help="Fit on either a 0-5 or 0-100 scale.")
    art.add_argument("--fit-report")
    art.add_argument("--resume-tex")
    art.add_argument("--resume-pdf")
    art.set_defaults(func=cmd_artifacts)

    srv = sub.add_parser("serve", help="Open a local board with direct status controls.")
    srv.add_argument("--port", type=int, default=8765)
    srv.add_argument("--tier", type=int, default=4)
    srv.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

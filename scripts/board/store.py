"""SQLite store for the job board.

The board is an accumulating record, not a snapshot: a listing that disappears
upstream keeps its row so the tracker never loses a job you already looked at.
`first_seen` is what drives notifications, so it is never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "board.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key            TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    url            TEXT NOT NULL,
    locations      TEXT NOT NULL,
    category       TEXT,
    terms          TEXT,
    sponsorship    TEXT,
    date_posted    INTEGER,
    tier           INTEGER NOT NULL,
    distance_mi    REAL,
    place_label    TEXT,
    description    TEXT,
    description_at INTEGER,
    first_seen     INTEGER NOT NULL,
    last_seen      INTEGER NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1,
    notified       INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'NEW',
    queued_at      INTEGER
    ,ats_vendor          TEXT
    ,ats_board           TEXT
    ,ats_requisition_id  TEXT
    ,normalized_company  TEXT
    ,normalized_title    TEXT
    ,priority_score      INTEGER NOT NULL DEFAULT 0
    ,location_score      INTEGER NOT NULL DEFAULT 0
    ,freshness_score     INTEGER NOT NULL DEFAULT 0
    ,role_score          INTEGER NOT NULL DEFAULT 0
    ,skill_score         INTEGER NOT NULL DEFAULT 0
    ,company_score       INTEGER NOT NULL DEFAULT 0
    ,friction_score      INTEGER NOT NULL DEFAULT 0
    ,score_reasons       TEXT
    ,fit_score           INTEGER
    ,fit_report_path     TEXT
    ,resume_tex_path     TEXT
    ,resume_pdf_path     TEXT
    ,resume_generated_at INTEGER
    ,missing_since       INTEGER
    ,url_checked_at      INTEGER
    ,availability_status TEXT NOT NULL DEFAULT 'ACTIVE'
    ,applicant_count     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_tier ON jobs(tier, distance_mi);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_notified ON jobs(notified);
CREATE TABLE IF NOT EXISTS source_runs (
    source         TEXT NOT NULL,
    ran_at         INTEGER NOT NULL,
    ok             INTEGER NOT NULL,
    fetched        INTEGER NOT NULL DEFAULT 0,
    kept           INTEGER NOT NULL DEFAULT 0,
    note           TEXT,
    status         TEXT NOT NULL DEFAULT 'ok',
    newest_posting INTEGER
);
CREATE TABLE IF NOT EXISTS recruiter_contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key      TEXT NOT NULL,
    name         TEXT,
    profile_url  TEXT,
    message_url  TEXT,
    status       TEXT NOT NULL DEFAULT 'none',
    contacted_at INTEGER,
    notes        TEXT,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recruiter_job ON recruiter_contacts(job_key);
CREATE TABLE IF NOT EXISTS job_sources (
    job_key       TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_url    TEXT,
    first_seen_at INTEGER NOT NULL,
    last_seen_at  INTEGER NOT NULL,
    PRIMARY KEY (job_key, source)
);
CREATE TABLE IF NOT EXISTS job_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key     TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    happened_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_events_key ON job_events(job_key, happened_at);
"""

# Strip punctuation/legal suffixes so 'Meta Platforms, Inc.' == 'meta platforms'
_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh|sa|ag|nv|group|holdings)\b\.?",
    re.I,
)
_NOISE_RE = re.compile(r"[^a-z0-9 ]+")
# Req ids, seasons and years differ across boards for the same underlying job.
_TITLE_NOISE_RE = re.compile(
    r"\b(summer|fall|winter|spring|20\d\d|intern|internship|co-?op|program|"
    r"[a-z]{0,3}\d{4,})\b",
    re.I,
)

COMPANY_ALIASES = {
    "campbell soup": "campbells",
    "campbell soup company": "campbells",
    "campbells": "campbells",
    "campbell": "campbells",
    "the campbells": "campbells",
}

WORKFLOW_STATUSES = {
    "NEW", "EVALUATING", "RESUME_GENERATING", "READY_TO_APPLY", "APPLIED",
    "OA", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN",
}


def _slug(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("'s", "").replace("’s", "")
    text = _SUFFIX_RE.sub(" ", text)
    text = _NOISE_RE.sub(" ", text)
    words = text.split()
    # A leading 'the' is styling, not identity ('The Campbell's Company').
    if words and words[0] == "the":
        words = words[1:]
    value = " ".join(words)
    return COMPANY_ALIASES.get(value, value)


def _title_slug(text: str) -> str:
    value = _slug(_TITLE_NOISE_RE.sub(" ", text or ""))
    words = ["engineer" if word == "engineering" else word for word in value.split()]
    return " ".join(words)


def dedupe_key(company: str, title: str, url: str) -> str:
    """Identity for a posting across boards.

    Company plus a season/req-id-stripped title, so the same role found on
    Simplify and LinkedIn collapses to one row. Falls back to the URL when the
    stripped title is empty.
    """
    base_title = _title_slug(title)
    base = f"{_slug(company)}|{base_title}"
    if not base_title:
        base = f"{_slug(company)}|{(url or '').split('?')[0]}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


@dataclass
class Job:
    source: str
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    category: str | None = None
    terms: list[str] = field(default_factory=list)
    sponsorship: str | None = None
    date_posted: int | None = None
    tier: int = 5
    distance_mi: float | None = None
    place_label: str | None = None
    description: str | None = None
    ats_vendor: str | None = None
    ats_board: str | None = None
    ats_requisition_id: str | None = None

    @property
    def key(self) -> str:
        return dedupe_key(self.company, self.title, self.url)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to existing personal databases without replacing any data."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    columns = {
        "ats_vendor": "TEXT", "ats_board": "TEXT", "ats_requisition_id": "TEXT",
        "normalized_company": "TEXT", "normalized_title": "TEXT",
        "priority_score": "INTEGER NOT NULL DEFAULT 0",
        "location_score": "INTEGER NOT NULL DEFAULT 0",
        "freshness_score": "INTEGER NOT NULL DEFAULT 0",
        "role_score": "INTEGER NOT NULL DEFAULT 0", "skill_score": "INTEGER NOT NULL DEFAULT 0",
        "company_score": "INTEGER NOT NULL DEFAULT 0", "friction_score": "INTEGER NOT NULL DEFAULT 0",
        "score_reasons": "TEXT", "fit_score": "INTEGER", "fit_report_path": "TEXT",
        "resume_tex_path": "TEXT", "resume_pdf_path": "TEXT", "resume_generated_at": "INTEGER",
        "missing_since": "INTEGER", "url_checked_at": "INTEGER",
        "availability_status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        "applicant_count": "INTEGER",
        # Triage lives beside the processing pipeline, not inside it: `status`
        # tracks what the evaluator has done, `seen_at`/`saved` track what the
        # human has done. Collapsing them would make "I read this" indistinguishable
        # from "Codex has not queued this yet".
        "seen_at": "INTEGER",
        "saved": "INTEGER NOT NULL DEFAULT 0",
        "canonical_url": "TEXT",
        # An explicit human "tailor this one". Kept apart from status EVALUATING,
        # which autoqueue also sets on its own guess, so the agent can tell what
        # The user chose from what the board proposed.
        "resume_requested_at": "INTEGER",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    run_columns = {row[1] for row in conn.execute("PRAGMA table_info(source_runs)")}
    for name, definition in {
        "status": "TEXT NOT NULL DEFAULT 'ok'",
        "newest_posting": "INTEGER",
    }.items():
        if name not in run_columns:
            conn.execute(f"ALTER TABLE source_runs ADD COLUMN {name} {definition}")
    # Runs recorded before the staleness guard existed were never judged on
    # posting age, so they claim 'ok' by default. Mark them unknown rather than
    # let them assert a health check that never ran.
    if "status" not in run_columns:
        conn.execute("UPDATE source_runs SET status='unknown' WHERE ok=1")
        conn.execute("UPDATE source_runs SET status='blocked' WHERE ok=0")
    # Seed sighting history from the jobs already on the board, so source pills
    # are populated before the next refresh rather than appearing empty.
    if not conn.execute("SELECT 1 FROM job_sources LIMIT 1").fetchone():
        conn.execute(
            """INSERT OR IGNORE INTO job_sources (job_key, source, source_url, first_seen_at, last_seen_at)
               SELECT key, source, url, first_seen, last_seen FROM jobs"""
        )
    # Preserve old state while moving to the explicit workflow vocabulary.
    conn.execute("UPDATE jobs SET status=UPPER(status) WHERE status IN ('new','queued')")
    conn.execute("UPDATE jobs SET status='EVALUATING' WHERE status='QUEUED'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority_score DESC)")
    conn.commit()


def upsert(conn: sqlite3.Connection, jobs: list[Job]) -> tuple[int, int]:
    """Insert or refresh jobs. Returns (new, updated)."""
    now = int(time.time())
    new = updated = 0
    for job in jobs:
        key = job.key
        row = None
        if job.ats_vendor and job.ats_requisition_id:
            row = conn.execute(
                "SELECT key, description, first_seen FROM jobs WHERE ats_vendor=? AND ats_requisition_id=?",
                (job.ats_vendor, job.ats_requisition_id),
            ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT key, description, first_seen FROM jobs WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            company_norm, title_norm = _slug(job.company), _title_slug(job.title)
            candidates = conn.execute(
                """SELECT key,description,first_seen,normalized_company,normalized_title,
                          ats_vendor,ats_board,ats_requisition_id FROM jobs
                   WHERE normalized_company IS NOT NULL AND normalized_title IS NOT NULL"""
            ).fetchall()
            for candidate in candidates:
                if (job.ats_requisition_id and candidate["ats_requisition_id"]
                        and (job.ats_vendor, job.ats_board, job.ats_requisition_id) != (
                            candidate["ats_vendor"], candidate["ats_board"],
                            candidate["ats_requisition_id"]
                        )):
                    continue
                company_ratio = SequenceMatcher(
                    None, company_norm, candidate["normalized_company"]
                ).ratio()
                title_ratio = SequenceMatcher(
                    None, title_norm, candidate["normalized_title"]
                ).ratio()
                if company_ratio >= 0.88 and title_ratio >= 0.92:
                    row = candidate
                    break
        if row is not None:
            key = row["key"]
        payload = (
            job.source, job.company, job.title, job.url,
            "|".join(job.locations), job.category, "|".join(job.terms),
            job.sponsorship, job.date_posted, job.tier, job.distance_mi,
            job.place_label, job.ats_vendor, job.ats_board, job.ats_requisition_id,
            _slug(job.company), _title_slug(job.title),
        )
        if row is None:
            conn.execute(
                """INSERT INTO jobs (key, source, company, title, url, locations,
                   category, terms, sponsorship, date_posted, tier, distance_mi,
                   place_label, ats_vendor, ats_board, ats_requisition_id,
                   normalized_company, normalized_title, description, first_seen, last_seen, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (key, *payload, job.description, now, now),
            )
            new += 1
        else:
            # Never clobber a description we already paid to fetch.
            conn.execute(
                """UPDATE jobs SET source=?, company=?, title=?, url=?, locations=?,
                   category=?, terms=?, sponsorship=?, date_posted=?, tier=?,
                   distance_mi=?, place_label=?, ats_vendor=COALESCE(?,ats_vendor),
                   ats_board=COALESCE(?,ats_board), ats_requisition_id=COALESCE(?,ats_requisition_id),
                   normalized_company=?, normalized_title=?, last_seen=?, active=1, missing_since=NULL,
                   availability_status='ACTIVE'
                   WHERE key=?""",
                (*payload, now, key),
            )
            if job.description and not row["description"]:
                conn.execute(
                    "UPDATE jobs SET description=?, description_at=? WHERE key=?",
                    (job.description, now, key),
                )
            updated += 1
        # Record the sighting per source. The winning row carries one `source`,
        # but a deduped job is often seen by several feeds, and knowing which
        # ones found it is what makes the merge auditable.
        conn.execute(
            """INSERT INTO job_sources (job_key, source, source_url, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(job_key, source) DO UPDATE SET
                   source_url=excluded.source_url, last_seen_at=excluded.last_seen_at""",
            (key, job.source, job.url, now, now),
        )
        recompute_score(conn, key, now=now, commit=False)
    conn.commit()
    return new, updated


def sources_for(conn: sqlite3.Connection, job_keys: list[str] | None = None) -> dict[str, list[dict]]:
    """Every feed that has sighted each job, newest sighting first."""
    rows = conn.execute(
        """SELECT job_key, source, source_url, first_seen_at, last_seen_at
           FROM job_sources ORDER BY job_key, first_seen_at"""
    ).fetchall()
    wanted = set(job_keys) if job_keys is not None else None
    out: dict[str, list[dict]] = {}
    for row in rows:
        if wanted is not None and row["job_key"] not in wanted:
            continue
        out.setdefault(row["job_key"], []).append({
            "source": row["source"], "url": row["source_url"],
            "first_seen": row["first_seen_at"], "last_seen": row["last_seen_at"],
        })
    return out


def request_resume(conn: sqlite3.Connection, job_key: str, wanted: bool = True) -> bool:
    """Flag (or clear) a human request for a tailored resume."""
    cursor = conn.execute(
        "UPDATE jobs SET resume_requested_at=? WHERE key=?",
        (int(time.time()) if wanted else None, job_key),
    )
    conn.commit()
    return cursor.rowcount > 0


def resume_requests(conn: sqlite3.Connection, pending_only: bool = True) -> list[sqlite3.Row]:
    """Jobs the user asked to have tailored, oldest request first.

    `pending_only` drops the ones that already have a validated PDF, so the
    list reads as work outstanding rather than as history.
    """
    clause = "AND resume_pdf_path IS NULL" if pending_only else ""
    return conn.execute(
        f"""SELECT * FROM jobs
            WHERE resume_requested_at IS NOT NULL {clause}
            ORDER BY resume_requested_at"""
    ).fetchall()


def applied_at_map(conn: sqlite3.Connection) -> dict[str, int]:
    """When each job was first marked applied, from the status history."""
    return {
        row["job_key"]: row["at"]
        for row in conn.execute(
            """SELECT job_key, MIN(happened_at) AS at FROM job_events
               WHERE to_status = 'APPLIED' GROUP BY job_key"""
        )
    }


RECRUITER_STATES = ("none", "found", "messaged", "replied")


def recruiters_for(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Recruiter contacts keyed by job. A role can accumulate more than one."""
    out: dict[str, list[dict]] = {}
    for row in conn.execute(
        """SELECT id, job_key, name, profile_url, message_url, status, contacted_at, notes
           FROM recruiter_contacts ORDER BY job_key, created_at"""
    ):
        out.setdefault(row["job_key"], []).append(dict(row))
    return out


def save_recruiter(conn: sqlite3.Connection, job_key: str, *, contact_id: int | None = None,
                   name: str = "", profile_url: str = "", message_url: str = "",
                   status: str = "none", contacted_at: int | None = None,
                   notes: str = "") -> int:
    if status not in RECRUITER_STATES:
        raise ValueError(f"status must be one of {RECRUITER_STATES}")
    if not conn.execute("SELECT 1 FROM jobs WHERE key=?", (job_key,)).fetchone():
        raise KeyError("unknown job")
    now = int(time.time())
    # Moving past 'none' without a date stamps one, so "messaged" always has a when.
    if status != "none" and contacted_at is None:
        contacted_at = now
    if contact_id:
        conn.execute(
            """UPDATE recruiter_contacts SET name=?, profile_url=?, message_url=?,
               status=?, contacted_at=?, notes=?, updated_at=? WHERE id=? AND job_key=?""",
            (name, profile_url, message_url, status, contacted_at, notes, now, contact_id, job_key),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO recruiter_contacts
               (job_key, name, profile_url, message_url, status, contacted_at, notes,
                created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (job_key, name, profile_url, message_url, status, contacted_at, notes, now, now),
        )
        contact_id = cursor.lastrowid
    conn.commit()
    return contact_id


def delete_recruiter(conn: sqlite3.Connection, job_key: str, contact_id: int) -> bool:
    cursor = conn.execute(
        "DELETE FROM recruiter_contacts WHERE id=? AND job_key=?", (contact_id, job_key)
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_seen(conn: sqlite3.Connection, job_keys: list[str]) -> int:
    """Stamp first-read time. Idempotent: an already-seen job keeps its first stamp."""
    if not job_keys:
        return 0
    now = int(time.time())
    cursor = conn.executemany(
        "UPDATE jobs SET seen_at=? WHERE key=? AND seen_at IS NULL",
        [(now, key) for key in job_keys],
    )
    conn.commit()
    return cursor.rowcount


def set_saved(conn: sqlite3.Connection, job_key: str, saved: bool) -> bool:
    cursor = conn.execute(
        "UPDATE jobs SET saved=? WHERE key=?", (1 if saved else 0, job_key)
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_inactive_missing(conn: sqlite3.Connection, source: str, seen_keys: set[str]) -> int:
    """Flag rows from `source` that upstream no longer lists."""
    if not seen_keys:
        return 0
    rows = conn.execute(
        "SELECT key FROM jobs WHERE source = ? AND active = 1", (source,)
    ).fetchall()
    stale = [r["key"] for r in rows if r["key"] not in seen_keys]
    now = int(time.time())
    conn.executemany(
        """UPDATE jobs SET active = 0, missing_since=COALESCE(missing_since, ?),
           availability_status='REMOVED_BY_SOURCE' WHERE key = ?""",
        [(now, k) for k in stale],
    )
    conn.commit()
    return len(stale)


def mark_inactive_missing_ats(conn: sqlite3.Connection, jobs: list[Job],
                              successful_boards: set[tuple[str, str]]) -> int:
    """Mark missing direct postings only for boards that returned successfully."""
    if not successful_boards:
        return 0
    seen = {(j.ats_vendor, j.ats_board, j.ats_requisition_id) for j in jobs}
    stale = []
    for row in conn.execute(
        """SELECT key,ats_vendor,ats_board,ats_requisition_id FROM jobs
           WHERE active=1 AND ats_vendor IS NOT NULL"""
    ):
        board = (row["ats_vendor"], row["ats_board"])
        identity = (row["ats_vendor"], row["ats_board"], row["ats_requisition_id"])
        if board in successful_boards and identity not in seen:
            stale.append(row["key"])
    now = int(time.time())
    conn.executemany(
        """UPDATE jobs SET active=0,missing_since=COALESCE(missing_since,?),
           availability_status='REMOVED_BY_SOURCE' WHERE key=?""",
        [(now, key) for key in stale],
    )
    conn.commit()
    return len(stale)


# A source that answers HTTP 200 with a well-formed but frozen list looks
# identical to a healthy one. Judge it on the newest posting it carries instead.
STALE_SOURCE_DAYS = 14


def classify_run(jobs: list[Job], max_age_days: int = STALE_SOURCE_DAYS,
                 now: int | None = None) -> tuple[str, int | None]:
    """Judge a successful fetch by the freshest posting date it returned.

    Returns (status, newest_posting).

      ok        at least one posting is newer than the window
      degraded  the source answered, but everything it returned predates it
      undated   it returned jobs, none of which carry a posting date, so age
                is unknowable and must not be reported as healthy
      empty     it returned no jobs at all
    """
    if not jobs:
        return "empty", None
    dates = [job.date_posted for job in jobs if job.date_posted]
    if not dates:
        return "undated", None
    newest = max(dates)
    cutoff = (int(time.time()) if now is None else now) - max_age_days * 86400
    return ("ok" if newest >= cutoff else "degraded"), newest


def record_run(conn: sqlite3.Connection, source: str, ok: bool,
               fetched: int = 0, kept: int = 0, note: str = "",
               status: str | None = None, newest_posting: int | None = None) -> None:
    if status is None:
        status = "ok" if ok else "blocked"
    conn.execute(
        """INSERT INTO source_runs (source, ran_at, ok, fetched, kept, note, status, newest_posting)
           VALUES (?,?,?,?,?,?,?,?)""",
        (source, int(time.time()), 1 if ok else 0, fetched, kept, note, status, newest_posting),
    )
    conn.commit()


def latest_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT source, MAX(ran_at) AS ran_at, ok, fetched, kept, note, status, newest_posting
           FROM source_runs GROUP BY source ORDER BY source"""
    ).fetchall()


def unnotified(conn: sqlite3.Connection, max_tier: int = 3) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM jobs WHERE notified = 0 AND active = 1 AND tier <= ?
           ORDER BY priority_score DESC, freshness_score DESC""",
        (max_tier,),
    ).fetchall()


def mark_notified(conn: sqlite3.Connection, keys: list[str]) -> None:
    conn.executemany("UPDATE jobs SET notified = 1 WHERE key = ?", [(k,) for k in keys])
    conn.commit()


def set_status(conn: sqlite3.Connection, key: str, status: str) -> bool:
    status = status.upper().replace(" ", "_")
    if status not in WORKFLOW_STATUSES:
        raise ValueError(f"invalid status {status!r}; choose from {sorted(WORKFLOW_STATUSES)}")
    old = conn.execute("SELECT status FROM jobs WHERE key=?", (key,)).fetchone()
    if not old:
        return False
    if old["status"] == status:
        return True
    now = int(time.time())
    cur = conn.execute(
        "UPDATE jobs SET status = ?, queued_at = ? WHERE key = ?",
        (status, now if status == "EVALUATING" else None, key),
    )
    conn.execute(
        "INSERT INTO job_events(job_key,from_status,to_status,happened_at) VALUES (?,?,?,?)",
        (key, old["status"], status, now),
    )
    conn.commit()
    return cur.rowcount > 0


def all_jobs(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY priority_score DESC, freshness_score DESC, distance_mi"
    return conn.execute(sql).fetchall()


def needing_description(conn: sqlite3.Connection, limit: int, max_tier: int = 3) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM jobs
           WHERE active = 1 AND tier <= ? AND (description IS NULL OR description = '')
           ORDER BY priority_score DESC, freshness_score DESC LIMIT ?""",
        (max_tier, limit),
    ).fetchall()


def save_description(conn: sqlite3.Connection, key: str, text: str) -> None:
    conn.execute(
        "UPDATE jobs SET description = ?, description_at = ? WHERE key = ?",
        (text, int(time.time()), key),
    )
    recompute_score(conn, key, commit=False)
    conn.commit()


def save_listing_metadata(conn: sqlite3.Connection, key: str, *,
                          date_posted: int | None = None,
                          applicant_count: int | None = None) -> None:
    conn.execute(
        """UPDATE jobs SET date_posted=COALESCE(?,date_posted),
           applicant_count=COALESCE(?,applicant_count) WHERE key=?""",
        (date_posted, applicant_count, key),
    )
    recompute_score(conn, key, commit=False)
    conn.commit()


def recompute_score(conn: sqlite3.Connection, key: str, now: int | None = None,
                    commit: bool = True) -> None:
    from .scoring import score_job

    row = conn.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
    if not row:
        return
    score = score_job(row, now=now)
    conn.execute(
        """UPDATE jobs SET priority_score=?, location_score=?, freshness_score=?, role_score=?,
           skill_score=?, company_score=?, friction_score=?, score_reasons=? WHERE key=?""",
        (score.priority, score.location, score.freshness, score.role, score.skills,
         score.company, score.friction, json.dumps(score.reasons), key),
    )
    if commit:
        conn.commit()


def recompute_scores(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT key,company,title FROM jobs").fetchall()
    keys = [row["key"] for row in rows]
    now = int(time.time())
    for row in rows:
        conn.execute(
            "UPDATE jobs SET normalized_company=?,normalized_title=? WHERE key=?",
            (_slug(row["company"]), _title_slug(row["title"]), row["key"]),
        )
        recompute_score(conn, row["key"], now=now, commit=False)
    conn.commit()
    return len(keys)


def save_artifacts(conn: sqlite3.Connection, key: str, *, fit_score: int | None = None,
                   fit_report_path: str | None = None, resume_tex_path: str | None = None,
                   resume_pdf_path: str | None = None) -> bool:
    generated = int(time.time()) if resume_tex_path or resume_pdf_path else None
    cur = conn.execute(
        """UPDATE jobs SET fit_score=COALESCE(?,fit_score),
           fit_report_path=COALESCE(?,fit_report_path), resume_tex_path=COALESCE(?,resume_tex_path),
           resume_pdf_path=COALESCE(?,resume_pdf_path),
           resume_generated_at=COALESCE(?,resume_generated_at) WHERE key=?""",
        (fit_score, fit_report_path, resume_tex_path, resume_pdf_path, generated, key),
    )
    conn.commit()
    return cur.rowcount > 0


def check_urls(conn: sqlite3.Connection, limit: int = 30, older_than_hours: int = 24) -> dict:
    """Recheck application URLs; only definitive 404/410 responses close a row."""
    import urllib.error
    import urllib.request

    cutoff = int(time.time()) - older_than_hours * 3600
    rows = conn.execute(
        """SELECT key,url FROM jobs WHERE active=1 AND (url_checked_at IS NULL OR url_checked_at<?)
           ORDER BY priority_score DESC LIMIT ?""", (cutoff, limit)
    ).fetchall()
    checked = closed = unknown = 0
    now = int(time.time())
    for row in rows:
        state = "ACTIVE"
        try:
            request = urllib.request.Request(
                row["url"], headers={"User-Agent": "Mozilla/5.0"}, method="HEAD"
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status >= 400:
                    unknown += 1
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                # Some ATS hosts reject HEAD even for live pages. Confirm with GET.
                try:
                    request = urllib.request.Request(
                        row["url"], headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-4095"}
                    )
                    with urllib.request.urlopen(request, timeout=15):
                        pass
                except urllib.error.HTTPError as get_exc:
                    if get_exc.code in {404, 410}:
                        state = "LIKELY_CLOSED"
                        closed += 1
                    else:
                        unknown += 1
                except Exception:
                    unknown += 1
            else:
                unknown += 1
        except Exception:
            unknown += 1
        conn.execute(
            """UPDATE jobs SET url_checked_at=?, availability_status=?,
               active=CASE WHEN ?='LIKELY_CLOSED' THEN 0 ELSE active END,
               missing_since=CASE WHEN ?='LIKELY_CLOSED' THEN COALESCE(missing_since,?) ELSE missing_since END
               WHERE key=?""",
            (now, state, state, state, now, row["key"]),
        )
        checked += 1
    conn.commit()
    return {"checked": checked, "closed": closed, "unknown": unknown}


def recompute_places(conn: sqlite3.Connection) -> int:
    """Re-derive tier/distance/label for every row from its stored locations.

    Geo coverage improves over time (a new city added to CITY_COORDS). Without
    this, an existing row keeps whatever distance was computed the day it was
    scraped, and two rows for the same city disagree.
    """
    from .geo import best_place

    changed = 0
    for row in conn.execute("SELECT key, locations, tier, distance_mi FROM jobs").fetchall():
        locations = [x for x in (row["locations"] or "").split("|") if x]
        place = best_place(locations)
        same_tier = place.tier == row["tier"]
        old = row["distance_mi"]
        same_dist = (old is None and place.distance_mi is None) or (
            old is not None and place.distance_mi is not None and abs(old - place.distance_mi) < 0.5
        )
        if same_tier and same_dist:
            continue
        conn.execute(
            "UPDATE jobs SET tier=?, distance_mi=?, place_label=? WHERE key=?",
            (place.tier, place.distance_mi, place.display(), row["key"]),
        )
        changed += 1
    conn.commit()
    return changed


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(active) active,
                  SUM(CASE WHEN description IS NOT NULL AND description <> '' THEN 1 ELSE 0 END) described,
                  SUM(CASE WHEN status='EVALUATING' THEN 1 ELSE 0 END) queued,
                  SUM(CASE WHEN notified=0 AND active=1 THEN 1 ELSE 0 END) unnotified
           FROM jobs"""
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def analytics(conn: sqlite3.Connection) -> dict:
    # The same LinkedIn URL can coexist with an ATS-enriched duplicate row.
    # Count the posting URL once so analytics reflects applications, not sources.
    identity = "COALESCE(NULLIF(j.url, ''), j.key)"
    stages = {}
    for status in WORKFLOW_STATUSES:
        stages[status] = conn.execute(
            f"""SELECT COUNT(DISTINCT {identity}) FROM job_events e
                JOIN jobs j ON j.key=e.job_key WHERE e.to_status=?""", (status,)
        ).fetchone()[0]
    applied = conn.execute(
        f"""SELECT COUNT(DISTINCT {identity}) FROM job_events e
            JOIN jobs j ON j.key=e.job_key
            WHERE e.to_status IN ('APPLIED','OA','INTERVIEW','OFFER','REJECTED')"""
    ).fetchone()[0]
    oa = conn.execute(
        f"""SELECT COUNT(DISTINCT {identity}) FROM job_events e
            JOIN jobs j ON j.key=e.job_key
            WHERE e.to_status IN ('OA','INTERVIEW','OFFER')"""
    ).fetchone()[0]
    interviews = conn.execute(
        f"""SELECT COUNT(DISTINCT {identity}) FROM job_events e
            JOIN jobs j ON j.key=e.job_key
            WHERE e.to_status IN ('INTERVIEW','OFFER')"""
    ).fetchone()[0]
    return {
        "applications": applied, "oa": oa, "interviews": interviews,
        "oa_rate": (100 * oa / applied) if applied else 0,
        "interview_rate": (100 * interviews / applied) if applied else 0,
        "stages": stages,
    }

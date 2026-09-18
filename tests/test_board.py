from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.board.filters import BoardConfig, keep_job
from scripts.board.scoring import freshness_score, location_score, score_job
from scripts.board.resume_downloads import sync_ready_resumes
from scripts.board.render import render
from scripts.board.sources.ats import _plain, _timestamp, discover_boards
from scripts.board.sources.linkedin import _parse_cards
from scripts.board.sources.vansh import REPOS as VANSH_REPOS
from scripts.board.store import (Job, applied_at_map, classify_run, connect, dedupe_key,
                                 delete_recruiter, request_resume, resume_requests,
                                 latest_runs, mark_inactive_missing_ats, mark_seen, record_run,
                                 recruiters_for, save_recruiter, set_saved, set_status,
                                 sources_for, upsert)


class ScoringTests(unittest.TestCase):
    def test_freshness_decays(self):
        now = 2_000_000_000
        self.assertGreater(freshness_score(now - 900, now, now), freshness_score(now - 86400 * 4, now, now))

    def test_unknown_posting_date_is_not_treated_as_fresh(self):
        self.assertEqual(freshness_score(None, 2_000_000_000, 2_000_000_000), 20)

    def test_location_order(self):
        self.assertGreater(location_score(0, 10, "Primary City, CA"), location_score(1, 31, "Secondary City, WA"))
        self.assertGreater(location_score(1, 31, "Secondary City, WA"), location_score(2, None, "Remote (US)"))
        self.assertGreater(location_score(2, None, "Remote (US)"), location_score(4, None, "Toronto"))

    def test_explainable_score(self):
        now = 2_000_000_000
        result = score_job({
            "title": "Backend Software Engineer Intern", "company": "Example",
            "url": "https://jobs.lever.co/example/123", "tier": 1,
            "distance_mi": 31, "place_label": "New York, NY",
            "date_posted": now - 1200, "first_seen": now - 1200,
            "description": "Build Python APIs on AWS with PostgreSQL.",
            "ats_vendor": "lever", "sponsorship": None,
        }, now=now)
        self.assertGreaterEqual(result.priority, 90)
        self.assertTrue(any("Skills" in reason for reason in result.reasons))


class StoreTests(unittest.TestCase):
    def test_alias_dedupe(self):
        a = dedupe_key("Campbell Soup Company", "Software Engineer Intern", "a")
        b = dedupe_key("The Campbell's Company", "Software Engineering Internship", "b")
        self.assertEqual(a, b)

    def test_migration_workflow_and_artifact_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            job = Job(source="test", company="Example", title="Software Engineer Intern",
                      url="https://jobs.lever.co/example/abc", locations=["New York, NY"],
                      tier=1, distance_mi=31, place_label="New York, NY")
            upsert(conn, [job])
            row = conn.execute("SELECT * FROM jobs").fetchone()
            self.assertGreater(row["priority_score"], 0)
            self.assertEqual(row["status"], "NEW")
            self.assertTrue(set_status(conn, row["key"], "READY_TO_APPLY"))
            self.assertEqual(conn.execute("SELECT status FROM jobs").fetchone()[0], "READY_TO_APPLY")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0], 1)

    def test_ready_resume_download_sync_and_applied_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conn = connect(root / "board.db")
            job = Job(source="test", company="Example", title="Software Engineer Intern",
                      url="https://jobs.lever.co/example/abc", locations=["New York, NY"],
                      tier=1, distance_mi=31, place_label="New York, NY")
            upsert(conn, [job])
            row = conn.execute("SELECT * FROM jobs").fetchone()
            resume = root / "Example_Resume.pdf"
            resume.write_bytes(b"%PDF-test")
            conn.execute(
                "UPDATE jobs SET status='READY_TO_APPLY',resume_pdf_path=? WHERE key=?",
                (str(resume), row["key"]),
            )
            conn.commit()
            downloads = root / "downloads"
            result = sync_ready_resumes(conn, downloads)
            self.assertEqual(result["ready"], 1)
            copied = downloads / "Example_Software_Engineer_Intern_Your_Name_Resume.pdf"
            self.assertTrue(copied.is_file())

            set_status(conn, row["key"], "APPLIED")
            result = sync_ready_resumes(conn, downloads)
            self.assertEqual(result["removed"], 1)
            self.assertFalse(copied.exists())

    def test_board_renders_applied_checkbox_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conn = connect(root / "board.db")
            job = Job(source="test", company="Example", title="Software Engineer Intern",
                      url="https://jobs.lever.co/example/abc", locations=["New York, NY"],
                      tier=1, distance_mi=31, place_label="New York, NY")
            upsert(conn, [job])
            output = root / "board.html"
            render(conn, output)
            html = output.read_text()
            self.assertIn('type="checkbox" data-applied=', html)
            # The checkbox now toggles both ways rather than being a one-way latch.
            self.assertIn('applied.checked ? "APPLIED" : "NEW"', html)
            # Freshness leads the row; fit is demoted to a small right-hand column.
            self.assertIn('class="when', html)
            self.assertIn('class="fit"', html)
            self.assertNotIn('<small>priority</small>', html)

    def test_direct_feed_marks_only_successful_board_removals(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            jobs = [
                Job(source="ats", company="One", title=f"Software Engineer Intern {n}",
                    url=f"https://jobs.ashbyhq.com/one/{n}", ats_vendor="ashby",
                    ats_board="one", ats_requisition_id=str(n))
                for n in (1, 2)
            ]
            upsert(conn, jobs)
            removed = mark_inactive_missing_ats(conn, jobs[:1], {("ashby", "one")})
            self.assertEqual(removed, 1)
            self.assertEqual(conn.execute("SELECT SUM(active) FROM jobs").fetchone()[0], 1)


class AtsTests(unittest.TestCase):
    def test_html_and_timestamp(self):
        self.assertEqual(_plain("&lt;p&gt;Build APIs&lt;/p&gt;"), "Build APIs")
        self.assertIsInstance(_timestamp("2026-09-16T10:30:00-04:00"), int)

    def test_discovers_tenants_from_known_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE jobs (company TEXT, url TEXT)")
            conn.executemany("INSERT INTO jobs VALUES (?,?)", [
                ("One", "https://job-boards.greenhouse.io/one/jobs/12"),
                ("Two", "https://jobs.lever.co/two/abc/apply"),
                ("Three", "https://jobs.ashbyhq.com/three/uuid/application"),
            ])
            conn.commit(); conn.close()
            boards = discover_boards(path)
            self.assertIn(("greenhouse", "global", "one"), boards)
            self.assertIn(("lever", "global", "two"), boards)
            self.assertIn(("ashby", "global", "three"), boards)

    def test_linkedin_card_keeps_source_posting_date(self):
        body = '''<li><div class="base-card" data-entity-urn="urn:li:jobPosting:1234567">
        <h3 class="base-search-card__title">Software Engineer Intern</h3>
        <h4 class="base-search-card__subtitle"><a>Example</a></h4>
        <span class="job-search-card__location">New York, NY</span>
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/1234567"></a>
        <time class="job-search-card__listdate" datetime="2026-09-10"></time>
        </div></li>'''
        jobs = _parse_cards(body)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].date_posted, 1788998400)


class SourceHealthTests(unittest.TestCase):
    """A source that answers with a frozen list must not report itself healthy."""

    NOW = 2_000_000_000

    def _job(self, date_posted):
        return Job(source="test", company="Example", title="Software Engineer Intern",
                   url=f"https://jobs.lever.co/example/{date_posted}",
                   locations=["New York, NY"], tier=1, distance_mi=31,
                   place_label="New York, NY", date_posted=date_posted)

    def test_recent_posting_is_ok(self):
        jobs = [self._job(self.NOW - 86400 * 2), self._job(self.NOW - 86400 * 40)]
        status, newest = classify_run(jobs, 14, now=self.NOW)
        self.assertEqual(status, "ok")
        self.assertEqual(newest, self.NOW - 86400 * 2)

    def test_every_posting_older_than_window_is_degraded(self):
        # The real vanshb03 failure: 200 OK, well-formed, newest posting 27 days old.
        jobs = [self._job(self.NOW - 86400 * 27), self._job(self.NOW - 86400 * 400)]
        status, newest = classify_run(jobs, 14, now=self.NOW)
        self.assertEqual(status, "degraded")
        self.assertEqual(newest, self.NOW - 86400 * 27)

    def test_boundary_posting_is_still_ok(self):
        status, _ = classify_run([self._job(self.NOW - 86400 * 14)], 14, now=self.NOW)
        self.assertEqual(status, "ok")

    def test_undated_listings_are_not_reported_healthy(self):
        status, newest = classify_run([self._job(None)], 14, now=self.NOW)
        self.assertEqual(status, "undated")
        self.assertIsNone(newest)

    def test_no_listings_is_empty(self):
        self.assertEqual(classify_run([], 14, now=self.NOW), ("empty", None))

    def test_run_status_round_trips_through_the_database(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            record_run(conn, "vansh", True, 471, 269, "stale: newest posting 27d old",
                       status="degraded", newest_posting=self.NOW - 86400 * 27)
            run = latest_runs(conn)[0]
            self.assertTrue(run["ok"])
            self.assertEqual(run["status"], "degraded")
            self.assertEqual(run["newest_posting"], self.NOW - 86400 * 27)

    def test_legacy_runs_are_marked_unknown_not_ok(self):
        """Runs written before the guard existed never had their age checked."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.db"
            legacy = sqlite3.connect(path)
            legacy.execute("""CREATE TABLE source_runs (source TEXT NOT NULL, ran_at INTEGER NOT NULL,
                              ok INTEGER NOT NULL, fetched INTEGER NOT NULL DEFAULT 0,
                              kept INTEGER NOT NULL DEFAULT 0, note TEXT)""")
            legacy.execute("INSERT INTO source_runs VALUES ('vansh', 1, 1, 942, 269, '2/2 repos')")
            legacy.execute("INSERT INTO source_runs VALUES ('yc', 2, 0, 0, 0, 'blocked')")
            legacy.commit()
            legacy.close()

            runs = {row["source"]: row for row in latest_runs(connect(path))}
            self.assertEqual(runs["vansh"]["status"], "unknown")
            self.assertEqual(runs["vansh"]["kept"], 269)
            self.assertEqual(runs["yc"]["status"], "blocked")

    def test_vansh_does_not_fetch_the_same_repo_twice(self):
        """Summer2026 was renamed to Summer2027; both URLs served identical bytes."""
        self.assertEqual(len(VANSH_REPOS), len(set(VANSH_REPOS)))
        self.assertNotIn("vanshb03/Summer2026-Internships", VANSH_REPOS)


class FeedTrackingTests(unittest.TestCase):
    """Triage, recruiter CRM and multi-source sightings."""

    def _conn(self, directory):
        return connect(Path(directory) / "board.db")

    def _job(self, source="simplify", url="https://jobs.lever.co/example/abc", **kw):
        base = dict(source=source, company="Example", title="Software Engineer Intern",
                    url=url, locations=["New York, NY"], tier=1, distance_mi=31,
                    place_label="New York, NY")
        base.update(kw)
        return Job(**base)

    def test_seen_is_separate_from_the_processing_pipeline(self):
        """Reading a job must not pretend the evaluator has queued it."""
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job()])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            self.assertEqual(mark_seen(conn, [key]), 1)
            row = conn.execute("SELECT status, seen_at FROM jobs").fetchone()
            self.assertEqual(row["status"], "NEW")
            self.assertIsNotNone(row["seen_at"])

    def test_seen_keeps_its_first_stamp(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job()])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            mark_seen(conn, [key])
            first = conn.execute("SELECT seen_at FROM jobs").fetchone()["seen_at"]
            self.assertEqual(mark_seen(conn, [key]), 0)
            self.assertEqual(conn.execute("SELECT seen_at FROM jobs").fetchone()["seen_at"], first)

    def test_saved_toggles_without_touching_status(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job()])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            self.assertTrue(set_saved(conn, key, True))
            self.assertEqual(conn.execute("SELECT saved, status FROM jobs").fetchone()["saved"], 1)
            self.assertTrue(set_saved(conn, key, False))
            self.assertEqual(conn.execute("SELECT saved FROM jobs").fetchone()["saved"], 0)
            self.assertFalse(set_saved(conn, "nosuchjob", True))

    def test_recruiter_contact_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job()])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            cid = save_recruiter(conn, key, name="Jane Smith",
                                 profile_url="https://linkedin.com/in/janesmith",
                                 status="messaged", notes="University recruiter")
            contact = recruiters_for(conn)[key][0]
            self.assertEqual(contact["name"], "Jane Smith")
            self.assertEqual(contact["status"], "messaged")
            self.assertIsNotNone(contact["contacted_at"], "messaged must carry a date")

            save_recruiter(conn, key, contact_id=cid, name="Jane Smith", status="replied")
            self.assertEqual(recruiters_for(conn)[key][0]["status"], "replied")
            self.assertEqual(len(recruiters_for(conn)[key]), 1, "update must not duplicate")

            self.assertTrue(delete_recruiter(conn, key, cid))
            self.assertNotIn(key, recruiters_for(conn))

    def test_one_job_can_hold_several_recruiters(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job()])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            save_recruiter(conn, key, name="First", status="messaged")
            save_recruiter(conn, key, name="Second", status="found")
            self.assertEqual(len(recruiters_for(conn)[key]), 2)

    def test_recruiter_rejects_unknown_job_and_bad_status(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job()])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            with self.assertRaises(KeyError):
                save_recruiter(conn, "nosuchjob", name="X")
            with self.assertRaises(ValueError):
                save_recruiter(conn, key, status="ghosted")

    def test_sightings_record_every_source_that_found_a_job(self):
        """A deduped job should prove which feeds merged into it."""
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job(source="simplify", url="https://simplify.jobs/p/1")])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            upsert(conn, [self._job(source="vansh", url="https://github.com/vansh/listing")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1,
                             "the two sightings must dedupe to one job")
            found = {s["source"]: s["url"] for s in sources_for(conn)[key]}
            self.assertEqual(set(found), {"simplify", "vansh"})
            self.assertEqual(found["vansh"], "https://github.com/vansh/listing")

    def test_repeat_sighting_updates_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = self._conn(directory)
            upsert(conn, [self._job(source="simplify")])
            upsert(conn, [self._job(source="simplify")])
            key = conn.execute("SELECT key FROM jobs").fetchone()["key"]
            self.assertEqual(len(sources_for(conn)[key]), 1)


class FeedOrderingTests(unittest.TestCase):
    """The rendered payload must carry a freshness-first ordering key."""

    def test_when_prefers_posting_date_and_falls_back_to_discovery(self):
        from scripts.board.render import _rows_payload
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            posted = Job(source="simplify", company="Dated", title="Software Engineer Intern",
                         url="https://jobs.lever.co/a/1", locations=["New York, NY"], tier=1,
                         distance_mi=31, place_label="New York, NY", date_posted=1_900_000_000)
            undated = Job(source="simplify", company="Undated", title="Software Engineer Intern",
                          url="https://jobs.lever.co/b/2", locations=["New York, NY"], tier=1,
                          distance_mi=31, place_label="New York, NY", date_posted=None)
            upsert(conn, [posted, undated])
            rows = {r["c"]: r for r in _rows_payload(conn)}
            self.assertEqual(rows["Dated"]["when"], 1_900_000_000)
            self.assertTrue(rows["Dated"]["postedKnown"])
            self.assertEqual(rows["Undated"]["when"], rows["Undated"]["discovered"])
            self.assertFalse(rows["Undated"]["postedKnown"],
                             "a missing posting date must not be presented as known")


class FilterTests(unittest.TestCase):
    """A source's own category and the title are both evidence; neither is a veto."""

    def _job(self, title, category=None, terms=None, tier=1):
        return Job(source="simplify", company="Example", title=title,
                   url="https://jobs.lever.co/example/abc", locations=["New York, NY"],
                   category=category, terms=terms or [], tier=tier, distance_mi=31,
                   place_label="New York, NY")

    def test_trusted_category_admits_an_unusual_title(self):
        """Simplify tagged Waymo's 'Scenes Intern' Software; the title regex did not."""
        self.assertTrue(keep_job(self._job("Scenes Intern", "Software"), BoardConfig()))
        self.assertTrue(keep_job(self._job("IoT Intern", "Software"), BoardConfig()))

    def test_explicit_software_title_survives_a_wrong_category(self):
        """REV Robotics filed a literal Software Engineer Intern under hardware."""
        self.assertTrue(keep_job(self._job("Software Engineer Intern", "Hardware"), BoardConfig()))

    def test_a_category_veto_still_blocks_genuine_hardware(self):
        cfg = BoardConfig()
        self.assertFalse(keep_job(self._job("Analog Design Intern - Hardware Engineering", "Hardware"), cfg))
        self.assertFalse(keep_job(self._job("Embedded Systems Intern", "Hardware"), cfg))
        self.assertFalse(keep_job(self._job("Electrical Engineer Intern", "Hardware"), cfg))

    def test_non_engineering_roles_stay_out(self):
        cfg = BoardConfig()
        self.assertFalse(keep_job(self._job("Marketing Analyst Intern", "AI/ML/Data"), cfg))
        self.assertFalse(keep_job(self._job("Quantitative Strategist Intern", "Quant"), cfg))
        self.assertFalse(keep_job(self._job("Product Management Analyst Intern", "Product"), cfg))

    def test_a_feed_without_a_category_still_needs_an_engineering_title(self):
        """LinkedIn carries no category, so the title stays the only guard."""
        cfg = BoardConfig()
        self.assertTrue(keep_job(self._job("Software Engineer Intern", None), cfg))
        self.assertFalse(keep_job(self._job("Scenes Intern", None), cfg))

    def test_winter_2026_is_an_accepted_cycle(self):
        cfg = BoardConfig()
        self.assertTrue(keep_job(self._job("Software Engineer Intern", "Software", ["Winter 2026"]), cfg))

    def test_past_cycles_are_still_rejected(self):
        cfg = BoardConfig()
        self.assertFalse(keep_job(self._job("Software Engineer Intern", "Software", ["Summer 2026"]), cfg))


class AppliedTrackingTests(unittest.TestCase):
    """Applied jobs stay reachable and carry their own timeline."""

    def _setup(self, directory):
        conn = connect(Path(directory) / "board.db")
        job = Job(source="simplify", company="Example", title="Software Engineer Intern",
                  url="https://jobs.lever.co/example/abc", locations=["New York, NY"],
                  tier=1, distance_mi=31, place_label="New York, NY")
        upsert(conn, [job])
        return conn, conn.execute("SELECT key FROM jobs").fetchone()["key"]

    def test_applied_at_comes_from_the_status_history(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            self.assertEqual(applied_at_map(conn), {})
            set_status(conn, key, "APPLIED")
            self.assertIn(key, applied_at_map(conn))

    def test_applied_at_keeps_the_first_submission(self):
        """Re-marking, or moving on to OA, must not rewrite when you applied."""
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            set_status(conn, key, "APPLIED")
            first = applied_at_map(conn)[key]
            set_status(conn, key, "OA")
            set_status(conn, key, "APPLIED")
            self.assertEqual(applied_at_map(conn)[key], first)

    def test_applied_rows_reach_the_rendered_payload(self):
        from scripts.board.render import _rows_payload
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            set_status(conn, key, "APPLIED")
            row = _rows_payload(conn)[0]
            self.assertIsNotNone(row["appliedAt"])
            self.assertEqual(row["st"], "APPLIED")

    def test_applied_chip_is_not_vetoed_by_the_active_view(self):
        """The Applied chip returned nothing because View=Active filtered first."""
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            set_status(conn, key, "APPLIED")
            output = Path(directory) / "board.html"
            render(conn, output)
            html = output.read_text()
            self.assertIn('state.chip === "applied" || state.chip === "followup"', html)
            self.assertIn('if (!chipWantsApplied){', html)

    def test_board_exposes_follow_up_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            set_status(conn, key, "APPLIED")
            save_recruiter(conn, key, name="Jane", status="messaged")
            output = Path(directory) / "board.html"
            render(conn, output)
            html = output.read_text()
            self.assertIn("needsFollowUp", html)
            self.assertIn('chip("followup","Follow up"', html)
            self.assertIn('"APPLIED":"Submitted.', html)
            self.assertIn("Jane", html)


class ResumeRequestTests(unittest.TestCase):
    """The Tailor button is a handoff: a human pick an agent can read back."""

    def _setup(self, directory):
        conn = connect(Path(directory) / "board.db")
        upsert(conn, [Job(source="simplify", company="Example",
                          title="Software Engineer Intern",
                          url="https://jobs.lever.co/example/abc",
                          locations=["New York, NY"], tier=1, distance_mi=31,
                          place_label="New York, NY")])
        return conn, conn.execute("SELECT key FROM jobs").fetchone()["key"]

    def test_request_is_recorded_and_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            self.assertEqual(resume_requests(conn), [])
            self.assertTrue(request_resume(conn, key))
            pending = resume_requests(conn)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["key"], key)
            self.assertIsNotNone(pending[0]["resume_requested_at"])

    def test_request_can_be_withdrawn(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            request_resume(conn, key)
            self.assertTrue(request_resume(conn, key, False))
            self.assertEqual(resume_requests(conn), [])

    def test_a_fulfilled_request_drops_off_the_pending_list(self):
        """The list is work outstanding, not history."""
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            request_resume(conn, key)
            conn.execute("UPDATE jobs SET resume_pdf_path=? WHERE key=?",
                         ("output/Example_Resume.pdf", key))
            conn.commit()
            self.assertEqual(resume_requests(conn), [])
            self.assertEqual(len(resume_requests(conn, pending_only=False)), 1)

    def test_requests_are_ordered_oldest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, first = self._setup(directory)
            upsert(conn, [Job(source="simplify", company="Second",
                              title="Software Engineer Intern",
                              url="https://jobs.lever.co/second/xyz",
                              locations=["New York, NY"], tier=1, distance_mi=31,
                              place_label="New York, NY")])
            second = conn.execute("SELECT key FROM jobs WHERE company='Second'").fetchone()["key"]
            request_resume(conn, second)
            conn.execute("UPDATE jobs SET resume_requested_at=? WHERE key=?", (1, first))
            conn.commit()
            self.assertEqual([r["key"] for r in resume_requests(conn)], [first, second])

    def test_unknown_job_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, _ = self._setup(directory)
            self.assertFalse(request_resume(conn, "nosuchjob"))

    def test_board_renders_the_tailor_control(self):
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            request_resume(conn, key)
            output = Path(directory) / "board.html"
            render(conn, output)
            html = output.read_text()
            self.assertIn("data-tailor=", html)
            self.assertIn("/api/request-resume", html)
            self.assertIn('chip("tailor","Tailor queue"', html)
            self.assertIn('"reqAt":', html)


if __name__ == "__main__":
    unittest.main()

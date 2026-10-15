from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.board.filters import BoardConfig, keep_job
from scripts.board.scoring import freshness_score, location_score, score_job
from scripts.board.resume_downloads import sync_ready_resumes
from scripts.board.render import render
from scripts.board.sources.ats import _plain, _timestamp, discover_boards
from scripts.board.sources.linkedin import _parse_cards
from scripts.board.sources.vansh import REPOS as VANSH_REPOS
from scripts.board.sources.yc import _payload as parse_yc_payload, fetch as fetch_yc
from scripts.board.store import (Job, applied_at_map, classify_run, connect, dedupe_key,
                                 delete_recruiter, request_resume, resume_requests,
                                 latest_runs, mark_inactive_missing_ats, mark_seen, record_run,
                                 recruiters_for, save_artifacts, save_recruiter, set_saved, set_status,
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


class YCombinatorSourceTests(unittest.TestCase):
    PAGE = """<html><body><div data-page="{&quot;props&quot;:{&quot;season&quot;:&quot;N/A&quot;,&quot;jobPostings&quot;:[{&quot;id&quot;:42,&quot;title&quot;:&quot;Software Engineer Intern&quot;,&quot;url&quot;:&quot;/companies/acme/jobs/abc-intern&quot;,&quot;location&quot;:&quot;New York, NY, US&quot;,&quot;prettyRole&quot;:&quot;Engineering&quot;,&quot;companyName&quot;:&quot;Acme&quot;,&quot;visa&quot;:&quot;US citizen/visa only&quot;}]}}"></div></body></html>"""

    def test_parses_public_data_page(self):
        props = parse_yc_payload(self.PAGE)
        self.assertEqual(props["jobPostings"][0]["companyName"], "Acme")

    def test_successful_fetch_returns_jobs(self):
        with patch("scripts.board.sources.yc._get", return_value=self.PAGE):
            result = fetch_yc(BoardConfig())
        self.assertTrue(result.ok)
        self.assertEqual(result.fetched, 1)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0].source, "yc")

    def test_missing_payload_is_reported_blocked(self):
        with patch("scripts.board.sources.yc._get", return_value="<html></html>"):
            result = fetch_yc(BoardConfig())
        self.assertFalse(result.ok)
        self.assertIn("payload missing", result.note)

    def test_zero_upstream_rows_is_reported_blocked(self):
        page = '<div data-page="{&quot;props&quot;:{&quot;jobPostings&quot;:[]}}"></div>'
        with patch("scripts.board.sources.yc._get", return_value=page):
            result = fetch_yc(BoardConfig())
        self.assertFalse(result.ok)
        self.assertIn("zero postings", result.note)


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

    def test_ready_resume_download_sync_is_add_only_after_applied(self):
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
            self.assertEqual(result["removed"], 0)
            self.assertTrue(copied.exists())

    def test_ready_resume_download_sync_respects_user_deletion(self):
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

            first = sync_ready_resumes(conn, downloads)
            copied = downloads / "Example_Software_Engineer_Intern_Your_Name_Resume.pdf"
            self.assertEqual(first["copied"], 1)
            copied.unlink()

            second = sync_ready_resumes(conn, downloads)
            self.assertEqual(second["copied"], 0)
            self.assertEqual(second["suppressed"], 1)
            self.assertFalse(copied.exists())

            third = sync_ready_resumes(conn, downloads)
            self.assertEqual(third["suppressed"], 1)
            self.assertFalse(copied.exists())

    def test_ready_resume_download_sync_retries_a_copy_that_never_succeeded(self):
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

            with patch("scripts.board.resume_downloads.shutil.copy2", side_effect=OSError):
                failed = sync_ready_resumes(conn, downloads)
            self.assertEqual(failed["errors"], 1)
            self.assertEqual(failed["suppressed"], 0)

            retried = sync_ready_resumes(conn, downloads)
            copied = downloads / "Example_Software_Engineer_Intern_Your_Name_Resume.pdf"
            self.assertEqual(retried["copied"], 1)
            self.assertEqual(retried["suppressed"], 0)
            self.assertTrue(copied.exists())

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
            self.assertIn('id="fReady"', html)
            self.assertIn('Resume ready', html)
            self.assertIn('id="scope"', html)
            self.assertIn('scope.addEventListener("change"', html)
            self.assertIn('More filters', html)
            self.assertNotIn('id="tierPills"', html)
            self.assertIn('id="pageNext"', html)
            self.assertIn('pageSize:50', html)
            self.assertIn('matched.slice(state.page*state.pageSize', html)
            self.assertIn('setTimeout(() => {', html)
            self.assertIn('/api/description?key=', html)
            self.assertIn('row.pendingSaved = true', html)
            self.assertIn('other.pendingStatus = true', html)

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
                ("Xcel", "https://xcelenergy.wd1.myworkdayjobs.com/External/job/Denver/Intern_JR1"),
                ("Brevan", "https://wd3.myworkdaysite.com/recruiting/brevanhoward/BH_ExternalCareers/job/New-York/Intern_JR2"),
                ("Wellmark", "https://jobs.smartrecruiters.com/WellmarkInc/7440001-intern"),
                ("Intel", "https://intel.wd1.myworkdayjobs.com/en-us/external/job/Phoenix/Intern_JR3"),
                ("JPMorgan", "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210799739"),
            ])
            conn.commit(); conn.close()
            boards = discover_boards(path)
            self.assertIn(("greenhouse", "global", "one"), boards)
            self.assertIn(("lever", "global", "two"), boards)
            self.assertIn(("ashby", "global", "three"), boards)
            self.assertIn(("workday", "us", "xcelenergy.wd1.myworkdayjobs.com|xcelenergy|External"), boards)
            self.assertIn(("workday", "us", "wd3.myworkdaysite.com|brevanhoward|BH_ExternalCareers"), boards)
            self.assertIn(("smartrecruiters", "us", "WellmarkInc"), boards)
            self.assertIn(("workday", "us", "intel.wd1.myworkdayjobs.com|intel|external"), boards)
            self.assertIn(("oracle", "us", "jpmc.fa.oraclecloud.com|CX_1001"), boards)

    def test_eightfold_country_first_location_is_readable(self):
        from scripts.board.sources.career_sites import _eightfold_location
        self.assertEqual(_eightfold_location("United States-California-Manhattan Beach"),
                         "Manhattan Beach, California")
        self.assertEqual(_eightfold_location("Sunnyvale, CA"), "Sunnyvale, CA")

    def test_plural_internships_and_full_us_name_are_recognised(self):
        from scripts.board.filters import INTERN_WORDS
        from scripts.board.geo import best_place
        self.assertTrue(INTERN_WORDS.search("Machine Learning and AI Masters Internships"))
        self.assertLessEqual(best_place(["United States of America"]).tier, 3)

    def test_goldman_division_makes_engineering_summer_analyst_a_tech_role(self):
        from scripts.board.sources import career_sites
        payload = {"data": {"roleSearch": {"totalCount": 2, "items": [
            {"roleId": "1_GS_CAMPUS", "jobTitle": "2027 | Americas | New York | Engineering | Summer Analyst",
             "jobFunction": None, "locations": [{"city": "New York", "state": "NY", "country": "United States"}]},
            {"roleId": "2_GS_CAMPUS", "jobTitle": "2027 | Americas | New York | Global Banking | Summer Analyst",
             "jobFunction": None, "locations": [{"city": "New York", "state": "NY", "country": "United States"}]},
        ]}}}
        with patch.object(career_sites, "_json", return_value=payload):
            jobs = career_sites._goldman("goldman", "Goldman Sachs", "us")
        kept = [j.title for j in jobs if keep_job(j, BoardConfig())]
        self.assertEqual(kept, ["2027 | Americas | New York | Engineering | Summer Analyst"])

    def test_board_fetch_retries_transient_errors_but_not_404(self):
        import urllib.error
        from scripts.board.sources import ats
        calls = {"flaky": 0, "gone": 0}

        def flaky(token, company, region):
            calls[token] += 1
            if token == "gone":
                raise urllib.error.HTTPError("u", 404, "Not Found", None, None)
            if calls[token] < 3:
                raise urllib.error.URLError("nodename nor servname provided")
            return []

        boards = {("flaky", "us", "flaky"): "A", ("flaky", "us", "gone"): "B"}
        with patch.object(ats, "discover_boards", return_value=boards), \
                patch.object(ats, "load_companies", return_value=[]), \
                patch.dict(ats.FETCHERS, {"flaky": flaky}), patch.object(ats.time, "sleep"):
            result = ats._fetch_vendors(BoardConfig(), {"flaky"}, "career_pages")
        self.assertEqual(calls, {"flaky": 3, "gone": 1})
        self.assertEqual(result.successful_boards, {("flaky", "flaky")})

    def test_avature_cards_parse_title_link_and_us_location(self):
        from scripts.board.sources import career_sites
        page = """<article class="article"><h3><a class="link"
            href="https://careers.twosigma.com/careers/JobDetail/New-York-SWE-Internship/14100">
            Software Engineering Internship (Summer 2027) </a></h3>
            <span class="paragraph_inner-span">United States - NY New York</span></article>"""
        with patch.object(career_sites, "_request", side_effect=[page, page]):
            jobs = career_sites._avature("careers.twosigma.com|/careers/OpenRoles", "Two Sigma", "us")
        self.assertEqual([(j.title, j.locations, j.ats_requisition_id) for j in jobs],
                         [("Software Engineering Internship (Summer 2027)", ["New York, NY"], "14100")])
        self.assertTrue(keep_job(jobs[0], BoardConfig()))

    def test_company_list_is_valid(self):
        from scripts.board.sources.ats import FETCHERS
        from scripts.board.sources.career_sites import load_companies
        companies = load_companies()
        self.assertTrue(companies)
        for entry in companies:
            self.assertIn(entry["vendor"], FETCHERS, entry)
            self.assertTrue(entry["company"] and entry["board"], entry)

    def test_direct_board_discovery_uses_only_us_sightings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE jobs (company TEXT, url TEXT, tier INTEGER)")
            conn.executemany("INSERT INTO jobs VALUES (?,?,?)", [
                ("US", "https://us.wd1.myworkdayjobs.com/Careers/job/A/Intern_1", 3),
                ("Intl", "https://intl.wd1.myworkdayjobs.com/Careers/job/B/Intern_2", 4),
            ])
            conn.commit(); conn.close()
            boards = discover_boards(path)
            self.assertTrue(any("us.wd1.myworkdayjobs.com" in key[2] for key in boards))
            self.assertFalse(any("intl.wd1.myworkdayjobs.com" in key[2] for key in boards))

    def test_greenhouse_embed_widget_uses_for_param_not_path(self):
        """boards.greenhouse.io/embed/job_app is a shared widget path across every
        tenant; the real board slug rides in ?for=, and a bare numeric ?token= (an
        older widget shape) identifies a job, not a board, so it can't be learned."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE jobs (company TEXT, url TEXT)")
            conn.executemany("INSERT INTO jobs VALUES (?,?)", [
                ("Gemini", "https://boards.greenhouse.io/embed/job_app?for=gemini&token=8214272"),
                ("Jump Trading", "https://boards.greenhouse.io/embed/job_app?token=8052351"),
            ])
            conn.commit(); conn.close()
            boards = discover_boards(path)
            self.assertIn(("greenhouse", "global", "gemini"), boards)
            self.assertNotIn(("greenhouse", "global", "embed"), boards)
            self.assertEqual(len(boards), 1)

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


class PostedDateTests(unittest.TestCase):
    """Workday's "Posted Today" is stamped at scan time; rescans must not make it look newer."""

    def test_rescan_never_moves_post_date_past_first_seen(self):
        job = lambda posted: Job(source="ats", company="KLA", title="Software Engineer Intern - AI",
                                 url="https://kla.wd1.myworkdayjobs.com/x/job/1", locations=["Remote"],
                                 tier=1, distance_mi=0, place_label="Remote", date_posted=posted)
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            with patch("scripts.board.store.time.time", return_value=1_000):
                upsert(conn, [job(1_000)])
            with patch("scripts.board.store.time.time", return_value=5_000):
                upsert(conn, [job(5_000)])
            row = conn.execute("SELECT date_posted, first_seen FROM jobs").fetchone()
            self.assertEqual((row["date_posted"], row["first_seen"]), (1_000, 1_000))


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
                         distance_mi=31, place_label="New York, NY", date_posted=1_791_000_000)
            undated = Job(source="simplify", company="Undated", title="Software Engineer Intern",
                          url="https://jobs.lever.co/b/2", locations=["New York, NY"], tier=1,
                          distance_mi=31, place_label="New York, NY", date_posted=None)
            upsert(conn, [posted, undated])
            rows = {r["c"]: r for r in _rows_payload(conn)}
            self.assertEqual(rows["Dated"]["when"], 1_791_000_000)
            self.assertTrue(rows["Dated"]["postedKnown"])
            self.assertEqual(rows["Undated"]["when"], rows["Undated"]["discovered"])
            self.assertFalse(rows["Undated"]["postedKnown"],
                             "a missing posting date must not be presented as known")

    def test_hides_pre_october_postings_unless_applied(self):
        from scripts.board.render import _rows_payload
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            old = lambda name: Job(source="simplify", company=name, title="Software Engineer Intern",
                                   url=f"https://jobs.lever.co/{name}/1", locations=["New York, NY"],
                                   tier=1, distance_mi=31, place_label="New York, NY",
                                   date_posted=1_788_000_000)  # 2026-08-29
            upsert(conn, [old("Skipped"), old("Applied")])
            key = conn.execute("SELECT key FROM jobs WHERE company='Applied'").fetchone()["key"]
            set_status(conn, key, "APPLIED")
            self.assertEqual([r["c"] for r in _rows_payload(conn)], ["Applied"])


class FilterTests(unittest.TestCase):
    """A source's own category and the title are both evidence; neither is a veto."""

    def _job(self, title, category=None, terms=None, tier=1, source="vansh"):
        return Job(source=source, company="Example", title=title,
                   url="https://jobs.lever.co/example/abc", locations=["New York, NY"],
                   category=category, terms=terms or [], tier=tier, distance_mi=31,
                   place_label="New York, NY")

    def test_simplify_listings_are_kept_whole(self):
        """Simplify is hand-curated: hardware and quant interns show, new-grad rows do not."""
        cfg = BoardConfig()
        self.assertTrue(keep_job(self._job("Signal Integrity Intern", "Hardware", source="simplify"), cfg))
        self.assertTrue(keep_job(self._job("Quantitative Investment Intern", "Quant", source="simplify"), cfg))
        self.assertFalse(keep_job(self._job("Software Engineer, New Grad", "Software", source="simplify"), cfg))

    def test_member_of_technical_staff_is_not_a_staff_role(self):
        cfg = BoardConfig()
        self.assertTrue(keep_job(self._job("Frontend Engineer Intern - Member of Technical Staff", "Software"), cfg))
        self.assertFalse(keep_job(self._job("Staff Software Engineer", "Software"), cfg))

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

    def test_skip_reason_reaches_the_board_and_is_highlighted(self):
        from scripts.board.render import _rows_payload
        with tempfile.TemporaryDirectory() as directory:
            conn, key = self._setup(directory)
            reason = "Requires current PhD enrollment."
            save_artifacts(conn, key, fit_score=20, decision_reason=reason)
            set_status(conn, key, "WITHDRAWN")
            self.assertEqual(_rows_payload(conn)[0]["decision"], reason)
            output = Path(directory) / "board.html"
            render(conn, output)
            html = output.read_text()
            self.assertIn("Do not apply", html)
            self.assertIn(reason, html)

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
            self.assertIn('["followup","Follow up"]', html)
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
            self.assertIn('["tailor","Tailor queue"]', html)
            self.assertIn('"reqAt":', html)



class SameTitleTests(unittest.TestCase):
    """One feed listing one title at several links is several jobs."""

    def _job(self, url, title="Software Engineer Intern", source="simplify"):
        return Job(source=source, company="Parsons", title=title, url=url,
                   locations=["Huntsville, AL"], tier=3)

    def test_same_title_different_links_are_separate_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            upsert(conn, [self._job("https://p.example/a")])
            upsert(conn, [self._job("https://p.example/a"), self._job("https://p.example/b?src=x")])
            urls = sorted(r["url"] for r in conn.execute("SELECT url FROM jobs"))
            self.assertEqual(urls, ["https://p.example/a", "https://p.example/b?src=x"])
            # A rescan keeps both rows instead of swapping them.
            upsert(conn, [self._job("https://p.example/b?src=x"), self._job("https://p.example/a")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)

    def test_near_identical_titles_in_one_feed_stay_apart(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            upsert(conn, [self._job("https://g.example/1", "AI Evaluation & Benchmarking Intern")])
            upsert(conn, [self._job("https://g.example/2", "AI Evaluation & Benchmarking Intern - AGI")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)


    def test_relisting_at_a_new_link_gets_its_own_date(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            old = self._job("https://d.example/old")
            old.date_posted = 1_788_000_000
            upsert(conn, [old])
            new = self._job("https://d.example/new")
            new.date_posted = 1_792_000_000
            upsert(conn, [new])
            self.assertEqual(conn.execute("SELECT date_posted FROM jobs").fetchone()[0], 1_792_000_000)


    def test_link_id_keeps_job_ids_in_the_query(self):
        from scripts.board.store import link_id
        self.assertNotEqual(link_id("https://d2l.com/jobs/?job_id=1"), link_id("https://d2l.com/jobs/?job_id=2"))
        self.assertEqual(link_id("https://x.com/j/1?utm_source=a"), link_id("https://x.com/j/1/"))


if __name__ == "__main__":
    unittest.main()


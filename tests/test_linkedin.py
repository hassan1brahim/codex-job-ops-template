from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.board.filters import BoardConfig
from scripts.board.sources import linkedin
from scripts.board.filters import keep_job
from scripts.board.store import (Job, connect, expire_unseen, mark_inactive_missing,
                                 retire_filtered, upsert)


def card(n: int, title: str = "Software Engineer Intern") -> str:
    return f'''<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{1000000 + n}">
    <h3 class="base-search-card__title">{title}</h3>
    <h4 class="base-search-card__subtitle"><a>Company {n}</a></h4>
    <span class="job-search-card__location">New York, NY</span>
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{1000000 + n}"></a>
    </div></li>'''


class PlanTests(unittest.TestCase):
    def test_fast_is_broad_and_internship_filtered(self):
        queries = linkedin.plan("fast")
        self.assertTrue(all(q.internship_filter and q.posted_within == "r86400" for q in queries))

    def test_deep_adds_unfiltered_role_searches(self):
        deep = linkedin.plan("deep")
        self.assertGreater(len(deep), len(linkedin.plan("fast")))
        self.assertTrue(any(not q.internship_filter for q in deep))


class PaginationTests(unittest.TestCase):
    def test_steps_by_cards_returned_and_filters_titles(self):
        pages = {0: "".join(card(n) for n in range(10)),
                 10: card(10) + card(11, "Senior Software Engineer")}
        starts = []

        def fake_get(url, timeout=25):
            start = int(url.rsplit("start=", 1)[1].split("&")[0])
            starts.append(start)
            return pages.get(start, "")

        cfg = BoardConfig(linkedin_queries=[("software engineer intern", "New York, United States")])
        with patch.object(linkedin, "_get", fake_get), patch.object(linkedin.time, "sleep"):
            result = linkedin.fetch(cfg)
        self.assertEqual(starts, [0, 10])            # not 0, 25
        self.assertEqual(result.fetched, 12)
        self.assertEqual(len(result.jobs), 11)       # the senior role is filtered out

    def test_429_backs_off_then_retries(self):
        calls = []

        def flaky(url, timeout=25):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("rate limited (429)")
            return ""

        with patch.object(linkedin, "_get", flaky), patch.object(linkedin.time, "sleep"):
            self.assertEqual(linkedin._get_with_backoff("u"), "")
        self.assertEqual(len(calls), 2)


class SightingTests(unittest.TestCase):
    def _job(self, source, url, **extra):
        return Job(source=source, company="Robinhood", title="Software Engineering Intern, Android",
                   url=url, locations=["New York, NY"], tier=1, distance_mi=31,
                   place_label="New York, NY", **extra)

    def test_linkedin_sighting_does_not_overwrite_richer_row(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            upsert(conn, [self._job("simplify", "https://jobs.lever.co/robinhood/abc",
                                    category="Software", terms=["Summer 2027"])])
            upsert(conn, [self._job("linkedin", "https://www.linkedin.com/jobs/view/123")])
            row = conn.execute("SELECT source, url, category, terms FROM jobs").fetchone()
            self.assertEqual(tuple(row), ("simplify", "https://jobs.lever.co/robinhood/abc",
                                          "Software", "Summer 2027"))
            sources = {r[0] for r in conn.execute("SELECT source FROM job_sources")}
            self.assertEqual(sources, {"simplify", "linkedin"})

    def test_search_sample_expires_by_age_not_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            upsert(conn, [self._job("linkedin", "https://www.linkedin.com/jobs/view/1")])
            self.assertEqual(expire_unseen(conn, "linkedin", 7), 0)
            conn.execute("UPDATE jobs SET last_seen=?", (int(time.time()) - 8 * 86400,))
            self.assertEqual(expire_unseen(conn, "linkedin", 7), 1)
            row = conn.execute("SELECT active, availability_status FROM jobs").fetchone()
            self.assertEqual(tuple(row), (0, "NOT_SEEN_RECENTLY"))


class MissingTests(unittest.TestCase):
    def test_merged_row_seen_this_run_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            job = Job(source="ats", company="Cigna Group", title="Software Engineering Intern",
                      url="https://job-boards.greenhouse.io/cigna/jobs/1", locations=[], tier=1)
            upsert(conn, [job])
            started = int(time.time())
            # Simplify sees the same job under another URL; it merges into the ATS row's key.
            seen = Job(source="simplify", company="Cigna Group", title="Software Engineering Intern",
                       url="https://cigna.example/apply/1", locations=[], tier=1, category="Software")
            upsert(conn, [seen])
            self.assertEqual(mark_inactive_missing(conn, "simplify", {seen.key}, since=started), 0)
            self.assertEqual(conn.execute("SELECT active FROM jobs").fetchone()[0], 1)


class FilterTests(unittest.TestCase):
    def _keep(self, title, category=None):
        job = Job(source="linkedin", company="X", title=title, url="u", locations=[],
                  category=category, tier=1)
        return keep_job(job, BoardConfig())

    def test_uncategorized_titles_need_a_tech_signal(self):
        for title in ("Structural Engineering Intern", "Mining Engineering Intern",
                      "Process Engineer Co-Op", "Engineering Internship"):
            self.assertFalse(self._keep(title), title)
        for title in ("Summer 2027 Cybersecurity Internship", "Forward Deployed Engineer (FDE) Intern",
                      "Technical Advisor Intern, GenAI", "Software Engineer Intern"):
            self.assertTrue(self._keep(title), title)

    def test_retire_filtered_spares_rows_the_user_touched(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "board.db")
            upsert(conn, [Job(source="linkedin", company=f"C{n}", title="Mining Engineering Intern",
                              url=f"https://www.linkedin.com/jobs/view/{n}", locations=[], tier=1)
                          for n in range(2)])
            conn.execute("UPDATE jobs SET saved=1 WHERE company='C1'")
            self.assertEqual(retire_filtered(conn, lambda j: keep_job(j, BoardConfig())), 1)
            active = [r[0] for r in conn.execute("SELECT company FROM jobs WHERE active=1")]
            self.assertEqual(active, ["C1"])


if __name__ == "__main__":
    unittest.main()

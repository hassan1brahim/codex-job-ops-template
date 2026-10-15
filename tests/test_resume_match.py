from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from resume_match import (rank_starters, resume_text, score_resume, skill_counts,  # noqa: E402
                          title_hint)


class SkillCountsTest(unittest.TestCase):
    def test_ignores_ordinary_english(self):
        text = "Go to market fast. The rest of the team. Spring 2027. R&D co-op. Insurance agents."
        self.assertEqual(skill_counts(text), {})

    def test_finds_case_sensitive_skills_and_aliases(self):
        counts = skill_counts("REST APIs in Go and Golang, k8s, Postgres, C and C++.")
        for skill in ("REST APIs", "Go", "Kubernetes", "PostgreSQL", "C", "C++"):
            self.assertIn(skill, counts)

    def test_one_mention_with_two_aliases_counts_once(self):
        self.assertEqual(skill_counts("Build REST APIs.")["REST APIs"], 1)


class ScoreTest(unittest.TestCase):
    def test_weights_repeated_skills_up_to_three(self):
        jd = skill_counts("Python Python Python Python. React.")
        result = score_resume(jd, "Python", Path("r.tex"))
        self.assertAlmostEqual(result.score, 3 / 4)
        self.assertEqual(result.missing, ("React",))

    def test_keyword_comments_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.tex"
            path.write_text("% KW-01: Kubernetes | none\n\\item Built Python tools.\n")
            text = resume_text(path)
        self.assertNotIn("Kubernetes", skill_counts(text))
        self.assertIn("Python", skill_counts(text))

    def test_ranks_best_starter_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.tex").write_text("\\item React and TypeScript.")
            (root / "b.tex").write_text("\\item Python and PostgreSQL.")
            _, results = rank_starters("Python, PostgreSQL, Docker", root)
        self.assertEqual(results[0].resume.name, "b.tex")


class TitleHintTest(unittest.TestCase):
    def test_title_picks_role_starter(self):
        self.assertEqual(title_hint("# Ramp — Software Engineer Intern - Backend"), "SWE_Full_Stack")
        self.assertEqual(title_hint("# Acme — Full-Stack Engineer"), "SWE_Full_Stack")
        self.assertEqual(title_hint("# Crowe — Machine Learning Intern"), "SWE_ML")
        self.assertEqual(title_hint("# Jump — C++ Software Engineer Intern"), "SWE_Systems_CPP")
        self.assertEqual(title_hint("# Verisk — Data Science Intern"), "SWE_ML")
        self.assertIsNone(title_hint("# Clay — Software Engineering Intern"))

    def test_title_breaks_near_ties(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SWE_ML.tex").write_text("\\item Python, Flask, PostgreSQL, AWS.")
            (root / "SWE_Full_Stack.tex").write_text("\\item Python, Flask, PostgreSQL.")
            _, results = rank_starters("# Co — Backend Intern\nPython Flask PostgreSQL AWS", root)
        self.assertEqual(results[0].resume.stem, "SWE_ML")  # 100% vs 75%: score wins

    def test_ignores_embedded_in_prose(self):
        self.assertNotIn("Embedded systems", skill_counts("Ramp is embedded in the transaction flow."))


if __name__ == "__main__":
    unittest.main()

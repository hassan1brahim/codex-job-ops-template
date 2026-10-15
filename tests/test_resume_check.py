from __future__ import annotations

import unittest

from scripts.resume_check import (HYPHEN_BREAK_RE, ResumeCheckError, _text_above_skills, lint_source,
                                  parse_gaps, parse_keywords)


def kw_block(count: int, evidence: str = "Project A") -> str:
    return "\n".join(f"% KW-{i:02d}: phrase {i} | {evidence}" for i in range(1, count + 1))


class ParseKeywordsTest(unittest.TestCase):
    def test_accepts_six_to_twelve_with_evidence(self):
        for count in (6, 12):
            self.assertEqual(len(parse_keywords(kw_block(count))), count)

    def test_rejects_too_few_or_too_many(self):
        for count in (5, 13, 15):
            with self.assertRaises(ResumeCheckError):
                parse_keywords(kw_block(count))

    def test_rejects_missing_evidence(self):
        source = kw_block(5) + "\n% KW-06: React"
        with self.assertRaisesRegex(ResumeCheckError, "names no evidence"):
            parse_keywords(source)

    def test_rejects_gap_in_numbering_and_duplicates(self):
        with self.assertRaises(ResumeCheckError):
            parse_keywords(kw_block(6).replace("KW-03", "KW-09"))
        with self.assertRaises(ResumeCheckError):
            parse_keywords(kw_block(6).replace("phrase 2", "phrase 1"))

    def test_gaps_are_reported(self):
        self.assertEqual(parse_gaps("% GAP: Kubernetes\n% GAP: Go"), ("Kubernetes", "Go"))


class LintSourceTest(unittest.TestCase):
    def test_flags_skills_keyword_dump(self):
        body = r"\textbf{Role-aligned evidence:} problem-solving; final presentation"
        self.assertTrue(lint_source(body))

    def test_flags_bolded_keyword_inside_bullet(self):
        self.assertTrue(lint_source(r"\item Shipped \textbf{React} features."))

    def test_allows_bolded_figures_inside_bullet(self):
        source = r"\item Cut latency \textbf{from 2.48s to 0.33s} with \textbf{zero data loss}."
        self.assertEqual(lint_source(source), [])

    def test_allows_bold_headings_and_comments(self):
        source = kw_block(6) + "\n% ATS KEYWORDS USED\n" + r"\textbf{Languages:} Python" + "\n" + r"\item Shipped React features."
        self.assertEqual(lint_source(source), [])


class HyphenBreakTest(unittest.TestCase):
    def test_detects_word_split_across_lines(self):
        self.assertEqual(HYPHEN_BREAK_RE.findall("in React, Type-\n   Script and"), [("Type", "Script")])

    def test_ignores_real_hyphenated_words_on_one_line(self):
        self.assertEqual(HYPHEN_BREAK_RE.findall("an open-source, 21-file reskin"), [])


class TextAboveSkillsTest(unittest.TestCase):
    def test_cuts_at_skills_heading(self):
        text = "Experience\n  Built React apps\nTechnical Skills\n  Kubernetes\n"
        above = _text_above_skills(text)
        self.assertIn("React", above)
        self.assertNotIn("Kubernetes", above)

    def test_requires_skills_heading(self):
        with self.assertRaises(ResumeCheckError):
            _text_above_skills("Experience\nBuilt things\n")


if __name__ == "__main__":
    unittest.main()

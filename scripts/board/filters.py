"""Relevance filters: internships only, SWE/AI/Data only.

Two independent guards, because no single source is trustworthy on its own.
Simplify carries a `category` field; LinkedIn carries nothing, so titles are
screened as well. Anything that looks full-time, senior, or non-engineering is
dropped even if the source filed it under an internship repo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Source categories we accept. Both naming schemes appear in the same feed.
ALLOWED_CATEGORIES = {
    "software", "software engineering",
    "ai/ml/data", "data science, ai & machine learning",
}
# Present in the feeds but out of scope for these searches.
EXCLUDED_CATEGORIES = {"hardware", "hardware engineering", "product",
                       "product management", "quant", "quantitative finance"}

ROLE_WORDS = re.compile(
    r"\b(software|swe|developer|engineer|engineering|programmer|data|"
    r"machine learning|ml|ai|artificial intelligence|deep learning|nlp|"
    r"computer vision|backend|back-end|frontend|front-end|full[ -]?stack|"
    r"platform|infrastructure|devops|site reliability|sre|cloud|systems|"
    r"analytics|research|technology|technical|computer science|informatics|"
    r"information science|security|cyber)\b",
    re.I,
)

# Unambiguous software signals. Narrower than ROLE_WORDS on purpose: this is
# the only thing allowed to overrule a category veto, so "Hardware Engineering"
# must not qualify while "Software Engineer" does.
STRONG_ROLE = re.compile(
    r"\b(software|swe|developer|programmer|full[ -]?stack|backend|back-end|"
    r"frontend|front-end|machine learning|data scien\w*|data engineer\w*|"
    r"artificial intelligence|computer science)\b",
    re.I,
)

# A posting must look like an internship.
INTERN_WORDS = re.compile(r"\b(intern|internship|co-?op|apprentice|trainee|summer analyst)\b", re.I)

# Hard excludes: seniority and full-time signals that sometimes slip into
# "internship" repos (new-grad rows are the usual offender).
EXCLUDE_TITLE = re.compile(
    r"\b(new ?grad|graduate program|entry[- ]level|full[- ]time|"
    r"senior|staff|principal|lead|manager|director|head of|vp|"
    r"phd (only|required)|postdoc|faculty|professor|"
    r"sales|marketing|recruit|hr |human resources|finance intern|"
    r"accounting|legal|paralegal|nurse|teacher)\b",
    re.I,
)

# Roles that are technically internships but not the target discipline.
EXCLUDE_DISCIPLINE = re.compile(
    r"\b(mechanical|civil|chemical|biomedical|aerospace structures|"
    r"manufacturing|industrial|supply chain|logistics|hardware test|"
    r"validation technician|field service|quality technician|"
    r"revenue management|portfolio (resource|management)|procurement|"
    r"merchandising|clinical|regulatory|actuarial|audit|tax|"
    r"strategy & insights|brand|communications|public relations)\b",
    re.I,
)


@dataclass
class BoardConfig:
    """Tunable relevance rules. Kept as data so the CLI can override them."""

    terms: set[str] = field(default_factory=lambda: {
        "Summer 2027", "Fall 2026", "Winter 2026", "Spring 2027",
        "Winter 2027", "Fall 2027", "N/A",
    })
    max_tier: int = 4
    allow_categories: set[str] = field(default_factory=lambda: set(ALLOWED_CATEGORIES))
    require_intern_word: bool = True
    linkedin_pages: int = 2
    linkedin_queries: list | None = None
    max_age_days: int | None = 120

    def term_ok(self, terms: list[str]) -> bool:
        if not self.terms:
            return True
        if not terms:
            return True  # sources that carry no season at all (LinkedIn)
        # Some feeds give a bare season ('Summer') with no year. That is not
        # evidence of the wrong cycle, so it passes rather than being dropped.
        if not any(re.search(r"\b20\d\d\b", t or "") for t in terms):
            return True
        return any(t in self.terms for t in terms)


def keep_job(job, cfg: BoardConfig) -> bool:
    """Decide whether a scraped posting belongs on the board."""
    title = job.title or ""
    category = (job.category or "").strip().lower()

    if EXCLUDE_TITLE.search(title):
        return False
    if EXCLUDE_DISCIPLINE.search(title):
        return False
    # A category veto is overruled by an unambiguous software title. Sources
    # mis-file: REV Robotics posted a literal 'Software Engineer Intern' under
    # category 'hardware', and the veto used to discard it.
    if category and category in EXCLUDED_CATEGORIES and not STRONG_ROLE.search(title):
        return False

    # Discipline: an allowed category OR an engineering title admits the role.
    # Requiring both discarded real work - Simplify tagged Waymo's 'Scenes
    # Intern' as Software, and the title vocabulary threw it away. Feeds with no
    # category at all (LinkedIn) still depend on the title, as before.
    category_ok = bool(category) and category in cfg.allow_categories
    if not category_ok and not ROLE_WORDS.search(title):
        return False

    # Internship: the category-bearing feeds are internship-only repos, but the
    # title still has to agree, because new-grad rows do appear in them.
    if cfg.require_intern_word and not INTERN_WORDS.search(title):
        return False

    if not cfg.term_ok(job.terms or []):
        return False
    if job.tier > cfg.max_tier:
        return False
    return True

#!/usr/bin/env python3
"""Compile and validate a tailored LaTeX resume."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


KEYWORD_RE = re.compile(r"^\s*%\s*KW-(\d{2}):\s*(.*?)\s*$", re.MULTILINE)
GAP_RE = re.compile(r"^\s*%\s*GAP:\s*(.+?)\s*$", re.MULTILINE)
MIN_KEYWORDS = 6
MAX_KEYWORDS = 12
# Labels that turn the Skills section into a pasted list of JD phrases.
BANNED_LABELS = ("role-aligned", "relevant keywords", "jd keywords", "ats keywords", "keywords:")
ITEM_LINE_RE = re.compile(r"^\s*\\item\b.*$", re.MULTILINE)
BOLD_RE = re.compile(r"\\textbf\{([^{}]*)\}")
DEFAULT_MIN_FILL = 0.95
# A word broken at a line end: "Type-" then "Script" on the next line.
HYPHEN_BREAK_RE = re.compile(r"([A-Za-z]{2,})-\n\s*([a-zA-Z]{2,})")
SKILLS_HEADING_RE = re.compile(r"\btechnical skills\b|\bskills\b", re.IGNORECASE)
PAGE_RE = re.compile(r"^Pages:\s+(\d+)\s*$", re.MULTILINE)
PAGE_BOX_RE = re.compile(r'<page\s+width="([\d.]+)"\s+height="([\d.]+)">')
WORD_BOX_RE = re.compile(r'<word\s+[^>]*yMax="([\d.]+)"[^>]*>')


@dataclass(frozen=True)
class ResumeMetrics:
    pdf: Path
    pages: int
    fill_ratio: float
    words: int
    keywords: tuple[str, ...]
    gaps: tuple[str, ...] = ()


class ResumeCheckError(RuntimeError):
    """Raised when a resume violates the output contract."""


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ResumeCheckError(f"Command failed ({' '.join(command)}):\n{detail}")
    return result.stdout


def parse_keywords(source: str) -> tuple[tuple[str, str], ...]:
    """Return (phrase, evidence) pairs from `% KW-NN: phrase | evidence` comments."""
    matches = KEYWORD_RE.findall(source)
    if not MIN_KEYWORDS <= len(matches) <= MAX_KEYWORDS:
        raise ResumeCheckError(
            f"Expected {MIN_KEYWORDS}-{MAX_KEYWORDS} keyword comments, found {len(matches)}. "
            "Pick only JD phrases with real evidence; list the rest as % GAP: lines."
        )
    numbers = [number for number, _ in matches]
    if numbers != [f"{n:02d}" for n in range(1, len(matches) + 1)]:
        raise ResumeCheckError("Keyword comments must be numbered KW-01, KW-02, ... in order.")

    pairs: list[tuple[str, str]] = []
    for number, body in matches:
        phrase, _, evidence = body.partition("|")
        phrase, evidence = phrase.strip(), evidence.strip()
        if not phrase or phrase.upper() == "REPLACE ME":
            raise ResumeCheckError(f"KW-{number} has no phrase.")
        if not evidence:
            raise ResumeCheckError(
                f"KW-{number} ({phrase}) names no evidence; use `% KW-{number}: phrase | master.md section`."
            )
        pairs.append((phrase, evidence))
    normalized = [phrase.casefold() for phrase, _ in pairs]
    if len(set(normalized)) != len(normalized):
        raise ResumeCheckError("Keyword phrases must be unique.")
    return tuple(pairs)


def parse_gaps(source: str) -> tuple[str, ...]:
    return tuple(gap for gap in GAP_RE.findall(source) if gap)


def lint_source(source: str) -> list[str]:
    """Catch keyword stuffing that a page/keyword count cannot see."""
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("%"))
    errors: list[str] = []
    lowered = body.casefold()
    for label in BANNED_LABELS:
        if label in lowered:
            errors.append(f"remove the '{label}' line; JD phrases belong in bullets, not a pasted list")
    for line in ITEM_LINE_RE.findall(body):
        for bolded in BOLD_RE.findall(line):
            if not re.search(r"\d|\bzero\b", bolded, re.IGNORECASE):
                errors.append(
                    f"bold only measured results in bullets; '{bolded}' has no figure"
                )
    return errors


def _text_above_skills(extracted: str) -> str:
    """Visible text before the Skills heading, where keywords must be demonstrated."""
    lines = extracted.splitlines()
    for index, line in enumerate(lines):
        if SKILLS_HEADING_RE.fullmatch(line.strip()):
            return "\n".join(lines[:index])
    raise ResumeCheckError("Could not find a 'Technical Skills' heading in the PDF text.")


def _normalize_text(value: str) -> str:
    value = value.casefold().replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip()


OVERFULL_RE = re.compile(r"^Overfull \\hbox \(([\d.]+)pt too wide\)", re.MULTILINE)


def _compile(tex_path: Path, output_pdf: Path) -> tuple[Path, list[float]]:
    """Compile to `output_pdf`; also return how far (pt) each overfull line sticks past the margin."""
    compiler = shutil.which("tectonic")
    if not compiler:
        raise ResumeCheckError("tectonic is required to compile and validate LaTeX resumes.")

    with tempfile.TemporaryDirectory(prefix="resume-check-") as temp_name:
        temp_dir = Path(temp_name)
        _run(
            [compiler, "--keep-logs", "--outdir", str(temp_dir), str(tex_path)],
            cwd=tex_path.parent,
        )
        compiled = temp_dir / tex_path.with_suffix(".pdf").name
        if not compiled.exists():
            raise ResumeCheckError("tectonic completed without producing a PDF.")
        log = temp_dir / tex_path.with_suffix(".log").name
        overfull = [float(x) for x in OVERFULL_RE.findall(log.read_text(errors="ignore"))] if log.exists() else []
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compiled, output_pdf)
    return output_pdf, overfull


def validate_resume(
    tex_path: Path,
    *,
    output_pdf: Path | None = None,
    min_fill: float = DEFAULT_MIN_FILL,
) -> ResumeMetrics:
    tex_path = tex_path.resolve()
    if not tex_path.is_file():
        raise ResumeCheckError(f"Resume source does not exist: {tex_path}")
    if tex_path.suffix.lower() != ".tex":
        raise ResumeCheckError("Resume source must be a .tex file.")
    if not 0.0 < min_fill < 1.0:
        raise ResumeCheckError("--min-fill must be between 0 and 1.")

    source = tex_path.read_text(encoding="utf-8")
    pairs = parse_keywords(source)
    keywords = tuple(phrase for phrase, _ in pairs)
    lint_errors = lint_source(source)
    if lint_errors:
        raise ResumeCheckError("; ".join(lint_errors))
    pdf_path = (output_pdf or tex_path.with_suffix(".pdf")).resolve()
    _, overfull = _compile(tex_path, pdf_path)
    visible = [width for width in overfull if width > 1.0]
    if visible:
        raise ResumeCheckError(
            f"{len(visible)} line(s) run past the right margin (up to {max(visible):.1f}pt); "
            "reword them or raise \\emergencystretch"
        )

    info = _run(["pdfinfo", str(pdf_path)])
    page_match = PAGE_RE.search(info)
    if not page_match:
        raise ResumeCheckError("pdfinfo did not report a page count.")
    pages = int(page_match.group(1))

    extracted = _run(["pdftotext", "-layout", str(pdf_path), "-"])
    split = HYPHEN_BREAK_RE.findall(extracted)
    if split:
        raise ResumeCheckError(
            "words are hyphenated across lines, which an ATS reads as two words: "
            + ", ".join(f"{a}-{b}" for a, b in split[:5])
        )
    demonstrated_text = _normalize_text(_text_above_skills(extracted))
    words = len(re.findall(r"\b[\w+#./-]+\b", extracted))
    if words < 150:
        raise ResumeCheckError(
            f"ATS extraction produced only {words} words; the PDF may be sparse or unreadable."
        )

    missing = [
        keyword for keyword in keywords if _normalize_text(keyword) not in demonstrated_text
    ]
    if missing:
        raise ResumeCheckError(
            "These keywords appear only in Skills or nowhere; show them in a bullet or project line: "
            + ", ".join(missing)
        )

    bbox = _run(["pdftotext", "-bbox", str(pdf_path), "-"])
    page_box = PAGE_BOX_RE.search(bbox)
    word_boxes = [float(value) for value in WORD_BOX_RE.findall(bbox)]
    if not page_box or not word_boxes:
        raise ResumeCheckError("Could not measure visible page fill from PDF text boxes.")
    page_height = float(page_box.group(2))
    fill_ratio = max(word_boxes) / page_height

    errors: list[str] = []
    if pages != 1:
        errors.append(f"expected exactly 1 page, found {pages}")
    if fill_ratio < min_fill:
        errors.append(
            f"page fill is {fill_ratio:.1%}, below the {min_fill:.1%} threshold; add stronger evidence"
        )
    if errors:
        raise ResumeCheckError("; ".join(errors))

    return ResumeMetrics(pdf_path, pages, fill_ratio, words, keywords, parse_gaps(source))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile a resume and enforce its one-page ATS/keyword contract."
    )
    parser.add_argument("tex", type=Path, help="Tailored LaTeX resume to validate.")
    parser.add_argument(
        "--output-pdf",
        type=Path,
        help="PDF destination; defaults to the .tex path with a .pdf extension.",
    )
    parser.add_argument(
        "--min-fill",
        type=float,
        default=DEFAULT_MIN_FILL,
        help="Minimum bottom-most text position as a fraction of page height (default: 0.95).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metrics = validate_resume(
            args.tex,
            output_pdf=args.output_pdf,
            min_fill=args.min_fill,
        )
    except ResumeCheckError as exc:
        print(f"resume check FAILED: {exc}")
        return 1

    print("resume check PASSED")
    print(f"PDF: {metrics.pdf}")
    print(f"Pages: {metrics.pages}")
    print(f"Page fill: {metrics.fill_ratio:.1%}")
    print(f"Extracted words: {metrics.words}")
    print("Keywords:")
    for index, keyword in enumerate(metrics.keywords, start=1):
        print(f"  {index:02d}. {keyword}")
    if metrics.gaps:
        print("Gaps (JD asks, no evidence):")
        for gap in metrics.gaps:
            print(f"  - {gap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


KEYWORD_RE = re.compile(r"^\s*%\s*KW-(\d{2}):\s*(.+?)\s*$", re.MULTILINE)
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


def parse_keywords(source: str) -> tuple[str, ...]:
    matches = KEYWORD_RE.findall(source)
    expected = [f"{number:02d}" for number in range(1, 16)]
    numbers = [number for number, _ in matches]
    if numbers != expected:
        raise ResumeCheckError(
            "Expected exactly one ordered keyword comment for KW-01 through KW-15."
        )

    keywords = tuple(keyword.strip() for _, keyword in matches)
    if any(not keyword or keyword.upper() == "REPLACE ME" for keyword in keywords):
        raise ResumeCheckError("All 15 keyword placeholders must be replaced.")
    normalized = [keyword.casefold() for keyword in keywords]
    if len(set(normalized)) != 15:
        raise ResumeCheckError("The 15 keyword phrases must be unique.")
    return keywords


def _normalize_text(value: str) -> str:
    value = value.casefold().replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip()


def _compile(tex_path: Path, output_pdf: Path) -> Path:
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
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compiled, output_pdf)
    return output_pdf


def validate_resume(
    tex_path: Path,
    *,
    output_pdf: Path | None = None,
    min_fill: float = 0.88,
) -> ResumeMetrics:
    tex_path = tex_path.resolve()
    if not tex_path.is_file():
        raise ResumeCheckError(f"Resume source does not exist: {tex_path}")
    if tex_path.suffix.lower() != ".tex":
        raise ResumeCheckError("Resume source must be a .tex file.")
    if not 0.0 < min_fill < 1.0:
        raise ResumeCheckError("--min-fill must be between 0 and 1.")

    source = tex_path.read_text(encoding="utf-8")
    keywords = parse_keywords(source)
    pdf_path = (output_pdf or tex_path.with_suffix(".pdf")).resolve()
    _compile(tex_path, pdf_path)

    info = _run(["pdfinfo", str(pdf_path)])
    page_match = PAGE_RE.search(info)
    if not page_match:
        raise ResumeCheckError("pdfinfo did not report a page count.")
    pages = int(page_match.group(1))

    extracted = _run(["pdftotext", "-layout", str(pdf_path), "-"])
    normalized_text = _normalize_text(extracted)
    words = len(re.findall(r"\b[\w+#./-]+\b", extracted))
    if words < 150:
        raise ResumeCheckError(
            f"ATS extraction produced only {words} words; the PDF may be sparse or unreadable."
        )

    missing = [
        keyword for keyword in keywords if _normalize_text(keyword) not in normalized_text
    ]
    if missing:
        rendered = ", ".join(missing)
        raise ResumeCheckError(
            "Keyword comments are not enough; these phrases are missing from visible PDF text: "
            + rendered
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

    return ResumeMetrics(pdf_path, pages, fill_ratio, words, keywords)


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
        default=0.88,
        help="Minimum bottom-most text position as a fraction of page height (default: 0.88).",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

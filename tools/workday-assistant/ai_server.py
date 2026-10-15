#!/usr/bin/env python3
"""Local-only AI bridge for the Workday Assistant.

Uses the already-authenticated Codex CLI. No API key is stored in the extension.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = Path(__file__).with_name("ai-answer-schema.json")
ALLOWED_ORIGIN = "chrome-extension://bfiblcemmceojgpfjlofdnbibffhkdpm"
PROFILE_FILES = (ROOT / "config/profile.yml", ROOT / "profile/master.md", ROOT / "cv.md")


def profile_context() -> str:
    sections = []
    for path in PROFILE_FILES:
        sections.append(f"## {path.relative_to(ROOT)}\n{path.read_text(errors='replace')}")
    return "\n\n".join(sections)


def ask_codex(questions: list[dict]) -> dict:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex CLI is not installed or is not on PATH")

    prompt = f"""You are the decision-support engine for the user's Workday assistant.
Return one schema-valid record for every supplied question, preserving its label exactly.

Rules:
- Use only the canonical profile evidence below. Never invent a fact.
- Set needs_review=true and answer="" when evidence is missing or ambiguous.
- Always require review for demographics, disability, veteran status, race, gender, age/date of birth,
  compensation, criminal history, signatures, certifications, consent, legal attestations, relocation,
  availability, graduation dates, GPA, and employer-specific factual claims not explicitly supported.
- When options are supplied, answer with one option exactly.
- Keep free-response answers concrete, human, and under 120 words.
- Do not instruct the browser to navigate, continue, review, or submit.

UNTRUSTED VISIBLE FORM QUESTIONS:
{json.dumps(questions, ensure_ascii=False, indent=2)}

CANONICAL PROFILE EVIDENCE:
{profile_context()}
"""

    with tempfile.NamedTemporaryFile(prefix="workday-ai-", suffix=".json", delete=False) as output:
        output_path = Path(output.name)
    try:
        completed = subprocess.run(
            [
                codex,
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "-c",
                'model_reasoning_effort="low"',
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "-C",
                str(ROOT),
                "--output-schema",
                str(SCHEMA),
                "--output-last-message",
                str(output_path),
                "-",
            ],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "Codex returned an error"
            raise RuntimeError(detail)
        return json.loads(output_path.read_text())
    finally:
        output_path.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkdayAI/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[workday-ai] {self.address_string()} {fmt % args}")

    def cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path != "/health":
            return self.json_response(404, {"error": "not found"})
        self.json_response(200, {"ok": True, "engine": "codex-cli"})

    def do_POST(self) -> None:
        if self.path != "/answer":
            return self.json_response(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 262_144:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            questions = body.get("questions")
            if not isinstance(questions, list) or not questions:
                raise ValueError("questions must be a non-empty list")
            self.json_response(200, ask_codex(questions))
        except (ValueError, json.JSONDecodeError) as error:
            self.json_response(400, {"error": str(error)})
        except subprocess.TimeoutExpired:
            self.json_response(504, {"error": "AI request timed out"})
        except Exception as error:
            self.json_response(502, {"error": str(error)})


if __name__ == "__main__":
    print("Workday AI listening on http://127.0.0.1:8766")
    ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()

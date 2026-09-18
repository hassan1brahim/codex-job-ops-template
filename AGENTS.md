# Codex Job Ops Instructions

You are operating a personal job-application workspace for the user. Work locally, keep durable state in files, and never submit applications without explicit human review.

Claude Code can navigate the same workspace through `CLAUDE.md` and `.claude/skills/job-ops/SKILL.md`. Keep these files aligned when changing routing or rules.

## First Move

At the start of a session:

```bash
python scripts/jobops.py doctor
```

Also check `docs/MAINTENANCE.md` for the weekly workspace review and data-audit routine.

If required files are missing, copy the matching `.example` files or ask the user for the missing content.

## Data Contract

Read `DATA_CONTRACT.md`.

User-owned files are the source of truth and should not be overwritten casually:

- `cv.md`
- `config/profile.yml`
- `profile/master.md`
- `data/applications.md`
- `data/pipeline.md`
- `data/reminders.md`
- `jds/*`
- `reports/*`
- `output/*`

System-owned files can be edited when improving the workspace:

- `AGENTS.md`
- `modes/*`
- `scripts/*`
- `tools/*`
- `docs/*`
- `templates/*`

## Command Routing

| User intent | Read |
|---|---|
| Find/discover roles, "what's new", "anything in NJ" | `modes/board.md` |
| Paste URL/JD, evaluate a role | `modes/evaluate.md` |
| Tailor, rewrite, or generate a resume | `modes/resume.md` |
| "which resumes should I tailor", picking what to build next | `jobboard requests` (the board's Tailor button writes it) |
| Process inbox/pipeline | `modes/pipeline.md` |
| Fill out an application | `modes/apply.md` |
| Check status | `modes/tracker.md` |

## Core Rules

- Prefer local files over external services.
- Use `tools/jd_fetcher` for deterministic JD fetches before browser/web fallbacks.
- Use Codex reasoning for fit, tailoring, and story selection. Do not rely on keyword-overlap as the decision engine.
- For every tailored resume, follow `modes/resume.md`. Select exactly 15 JD keywords, ground each
  one in `profile/master.md` or `cv.md`, and list them as numbered comments in the generated LaTeX.
- Rewrite extended experience and project descriptions to mirror the JD while preserving the
  substance of what the user actually did. If the evidence bank cannot support a desired keyword or
  impact claim, stop and ask the user for the missing facts before drafting that claim.
- A resume is not complete until `python scripts/jobops.py check-resume <file.tex>` passes: exactly
  one page, all 15 keywords visible in extracted text, ATS-readable output, and sufficient page fill.
- Keep evidence concrete: cite exact CV/profile facts, project names, metrics, and JD requirements.
- Strongly discourage low-fit applications.
- Stop before external submission, sending messages, or clicking final apply.
- Update `data/applications.md` and `reports/` whenever an evaluation or application draft is created.
- For board-originated work, record fit/resume paths with `jobboard artifacts <key>` so the board is
  the durable source of workflow state. Never run automatic processing while work authorization is unknown.

## Local Helpers

```bash
python scripts/jobops.py doctor
python scripts/jobops.py add-url "<job-url>"
python scripts/jobops.py fetch "<job-url>"
python scripts/jobops.py tracker
python scripts/jobops.py check-resume output/<resume>.tex
```

`fetch` writes Markdown job descriptions to `jds/`.

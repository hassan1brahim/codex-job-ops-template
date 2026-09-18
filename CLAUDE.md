# Claude Navigation

This is a personal job-application workspace for the user. It is designed to work with both Codex and Claude Code.

Start by reading:

1. `AGENTS.md` — canonical operating rules.
2. `DATA_CONTRACT.md` — user-owned vs system-owned files.
3. `README.md` — repo layout and quick start.

Then run:

```bash
python scripts/jobops.py doctor
```

Also read `docs/MAINTENANCE.md` for the weekly workspace review and data-audit routine.

## How To Use This Repo

If the user asks to find or discover roles, read `modes/board.md`.

If the user pastes a job URL or JD, read `modes/evaluate.md`.

If the user asks for a tailored resume, read `modes/resume.md` and use its evidence, keyword-comment,
overflow, and validation loop.

**Before asking which role to tailor for, check the board's request queue.** The
`Tailor` button on the board records which roles the user picked:

```bash
python scripts/jobboard.py requests          # human-readable, oldest request first
python scripts/jobboard.py requests --json   # same list for programmatic use
```

A row in that list means the user explicitly asked for a tailored resume for that
job. It is a stronger signal than `autoqueue`, which guesses. `process` builds
requested jobs first. `requests` hides roles that already have a validated PDF,
so the list is work outstanding, not history.

If the user asks to process pending jobs, read `modes/pipeline.md`.

If the user is filling out an application, read `modes/apply.md`.

If the user asks for status, read `modes/tracker.md`.

## Important Constraints

- This is not an auto-apply bot.
- Never submit applications or send messages without explicit review.
- Use `tools/jd_fetcher` for deterministic JD fetches.
- Use Claude/Codex reasoning over `cv.md`, `profile/master.md`, and the JD for matching. Do not use keyword overlap as the core decision engine.
- Keep all personal facts in the user layer listed in `DATA_CONTRACT.md`.
- Move resumes used for submitted applications to `output/completed-resumes/`; the hourly archive
  intentionally leaves that directory unchanged.

## Helpful Commands

```bash
python scripts/jobops.py doctor
python scripts/jobops.py add-url "<job-url>"
python scripts/jobops.py fetch "<job-url>"
python scripts/jobops.py tracker
python scripts/jobops.py check-resume output/Your_Name_Role_Resume.tex

# Job board (discovery)
python scripts/jobboard.py refresh
python scripts/jobboard.py list --tier 1
python scripts/jobboard.py next
python scripts/jobboard.py queue <key>
python scripts/jobboard.py autoqueue
python scripts/jobboard.py serve
python scripts/jobboard.py requests   # roles the user flagged for a tailored resume
python scripts/jobboard.py analytics
```

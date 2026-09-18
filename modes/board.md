# Mode: Board

Use this mode when the user asks to **find** roles rather than evaluate one already selected:
"what's new", "find nearby roles", "find me internships", "work the board".

The board is the discovery half of this workspace. `modes/evaluate.md` and `modes/resume.md` are the
tailoring half. This mode connects them: it picks a job and hands it over in the exact shape those
modes already expect, so nothing downstream changes.

## What The Board Is

`scripts/jobboard.py` aggregates SWE / AI / Data **internships** from Simplify, the vanshb03
community mirror, LinkedIn, and direct Greenhouse/Lever/Ashby feeds into `data/board.db`. Jobs are
ranked by location (35%), freshness (25%), role relevance (20%), skill evidence (10%), company
preference (5%), and application friction (5%).

| Tier | Meaning |
|---|---|
| 0 | Configured primary area |
| 1 | Configured secondary area |
| 2 | Remote (US) |
| 3 | Rest of US, ordered by great-circle miles from home |
| 4 | International |

Wellfound, swelist and YC are **known blocked**, not missing. `jobboard status` prints why. Never
report the board as complete coverage of those three.

## The Loop

```bash
python scripts/jobboard.py refresh          # pull every source
python scripts/jobboard.py describe --limit 25   # fetch JD text, closest first
python scripts/jobboard.py next             # highest-ranked unworked job, as a brief
python scripts/jobboard.py autoqueue        # queue the strongest new jobs
```

`next` prints a brief containing the key, company, role, location, distance, source, URL and the
cached job description. That brief is enough to run `modes/evaluate.md` without fetching anything
else.

Then, for a job worth pursuing:

```bash
python scripts/jobboard.py queue <key>      # appends to data/pipeline.md, marks it queued
```

`queue` writes a durable line with the board key, priority, URL, and ranking reasons. `autoqueue`
does this for only the top-scoring new roles. From that point the existing workflow takes over.

## Working A Job End To End

1. `jobboard next` (or `jobboard list --tier 1` and pick a key).
2. If the brief says the description was not fetched, run
   `jobboard describe --limit 5` and try again. **Never evaluate a role from its title alone.**
3. Run `modes/evaluate.md` against the description in the brief. Save the JD to `jds/` with
   `python scripts/jobops.py fetch "<url>"` so the evaluation has a durable artifact.
4. If the fit is worth a resume, run `modes/resume.md`. All of its rules still apply: exactly 15
   grounded keyword phrases, evidence from `profile/master.md`, and
   `python scripts/jobops.py check-resume <file.tex>` must pass.
5. Attach outputs with `jobboard artifacts <key> ...`; this makes report/resume buttons appear.
6. Move the job to `READY_TO_APPLY` and add it to `data/applications.md` per `modes/tracker.md`.
7. Tell the user the resume is ready and provide the apply URL. **Do not submit.**

## Rules

- **The board proposes, it does not decide.** A tier-0 job that does not fit is still a bad
  application. Rank by distance, choose by fit.
- **Do not evaluate from a title.** Fetch the description first. Titles like "Technology Intern"
  carry no information about the actual work.
- **Respect the sponsorship flag.** A row carrying `U.S. Citizenship is Required` or
  `Does Not Offer Sponsorship` must be raised with the user before any resume work when
  `config/profile.yml` records work authorization as `unknown`.
- **Never auto-submit.** The board ends at a tailored resume and a URL. The user clicks apply.
- **Stale is worse than empty.** If `jobboard status` shows a source last ran days ago, refresh
  before reporting what is available.
- Descriptions are fetched politely and rate-limited. Do not raise `--limit` above ~50 in one run,
  and do not parallelise it.

## Notifications

`jobboard notify` sends a macOS notification for new postings at tier 3 or better and marks them
announced so they are not repeated. The launchd job in
The generic launchd templates in `scripts/com.user.codex-job-ops.*.plist` run refresh, description,
notifications, and the local server. Install them
with `bash scripts/install-jobboard-schedule.sh`.

## Reporting To The User

Lead with the configured primary and secondary areas, then remote. Give counts, not a wall of rows.
Name blocked sources when asked about coverage. When something genuinely good is close by, say so
plainly and provide the key so the user can act on it in one command.

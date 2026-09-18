---
name: job-ops
description: Personal local job-application workspace for evaluating roles, tailoring resumes, processing a job pipeline, drafting application answers, and tracking applications.
arguments: mode
user_invocable: true
argument-hint: "[pipeline | apply | tracker | <job-url-or-jd>]"
---

# job-ops

Read `AGENTS.md`, `DATA_CONTRACT.md`, and `CLAUDE.md`.

Then route:

- Find/discover/board request → `modes/board.md`
- Empty/status/tracker request → `modes/tracker.md`
- URL or JD text → `modes/evaluate.md`
- Resume tailoring or generation → `modes/resume.md`
- `pipeline` → `modes/pipeline.md`
- `apply` → `modes/apply.md`

Before work, run:

```bash
python scripts/jobops.py doctor
```

Use local files as the source of truth. Never submit applications or send messages without the user's explicit final approval.
For a board-originated job, record generated artifacts with `jobboard artifacts <key>`.

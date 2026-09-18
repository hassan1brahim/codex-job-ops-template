# /job-ops

Personal job-application command center.

## Router

Use the argument or user request to pick a mode:

| Request | Mode file |
|---|---|
| no args / status | `modes/tracker.md` |
| job URL or pasted JD | `modes/evaluate.md` |
| tailor/generate resume | `modes/resume.md` |
| `pipeline` | `modes/pipeline.md` |
| `apply` | `modes/apply.md` |
| `tracker` | `modes/tracker.md` |

Always read `AGENTS.md` and `DATA_CONTRACT.md` first.

Run:

```bash
python scripts/jobops.py doctor
```

Then execute the selected mode.

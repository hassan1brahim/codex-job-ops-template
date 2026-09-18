# Mode: Apply

Use this when the user is filling out a live application.

## Inputs

Read:

- matching report from `reports/`
- matching JD from `jds/`
- `config/profile.yml`
- `profile/master.md`
- `cv.md` if present

## Workflow

1. Identify company and role.
2. Find the matching report.
3. Confirm the form still matches the same role.
4. Extract every visible question or ask the user to paste them.
5. Draft concise answers using report evidence.
6. Mark sensitive fields as `Ask user` unless already present in `config/profile.yml`.
7. Stop before submission.

## Answer Style

- Direct.
- Specific.
- Evidence-backed.
- No generic passion filler.
- No invented facts.

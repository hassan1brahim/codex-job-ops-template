# Maintenance

## Weekly Workspace Review

Cadence: once per week, preferably Monday.

Required workflow:

```bash
python scripts/jobops.py doctor
python scripts/resume_archive.py
python -m py_compile scripts/jobops.py scripts/resume_check.py tools/jd_fetcher/*.py
```

Then inspect the current workspace:

```bash
git status --short
rg -n "TODO|FIXME|upstream|sync" .
```

Update this repo when there are useful improvements to make:

- Improve prompts, modes, tracker/report flow, docs, or local helper scripts.

After updates:

```bash
python scripts/jobops.py doctor
python -m py_compile scripts/jobops.py scripts/resume_check.py tools/jd_fetcher/*.py
git status --short
```

Commit useful workspace updates separately from unrelated personal data changes.

## Data Audit Reminder

When asked whether this repo stores data, check:

- `config/profile.yml`
- `profile/master.md`
- `data/applications.md`
- `data/pipeline.md`
- `jds/`
- `reports/`
- `output/`

These are the main user-data locations defined by `DATA_CONTRACT.md`.

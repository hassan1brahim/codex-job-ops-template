# Mode: Evaluate Job

Use this when the user pastes a job URL, a JD, or asks whether to apply.

## Inputs

Read:

- `modes/_shared.md`
- `config/profile.yml`
- `profile/master.md`
- `cv.md` if present
- the JD from the pasted text, URL fetch, or `jds/`

## URL Handling

For a URL, try the deterministic fetcher first:

```bash
python scripts/jobops.py fetch "<url>"
```

If that fails, use browser/web fetching. If neither works, ask the user to paste the JD.

## Output Blocks

Create a report with:

1. **Role Summary**: company, title, location, seniority, remote/onsite, internship/full-time.
2. **Score**: `0.0-5.0`, with one-line recommendation.
3. **Match Table**: JD requirement, user evidence, strength, gap.
4. **Keyword Evidence Matrix**: exactly 15 high-value JD phrases, each mapped to a concrete fact in
   `profile/master.md` or `cv.md`; label unsupported phrases as gaps rather than forcing them in.
5. **Resume Tailoring Plan**: experiences/projects to emphasize, proof points to use, and material to
   cut. Follow `modes/resume.md` when generating the resume.
6. **Application Answers**: only if score is `4.5+`, draft common answers.
7. **Risks**: logistics, sponsorship, seniority mismatch, domain mismatch, stale/ghost posting signals.
8. **Source + Tracking**: where the role came from, expected response quality if known, and suggested tracker status.
9. **Next Action**: apply, save for later, ask for referral, skip, or gather more info.

## Report File

Save as:

```text
reports/YYYY-MM-DD-company-role.md
```

## Tracker Update

Append or update `data/applications.md`.

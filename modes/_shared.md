# Shared Job Ops Rules

## Quality Bar

This workspace is for focused applications, not volume spam.

- Score roles honestly.
- Recommend against applying below `4.0 / 5` unless the user has a strategic reason.
- Do not submit applications, send outreach, or click final apply.
- Prefer concrete proof over claims.
- Do not invent legal, demographic, work authorization, salary, disability, veteran, relocation, or background-check answers.

## Fit Scoring

Use a `0.0` to `5.0` score:

- `5.0`: unusually strong fit; apply quickly with tailored materials.
- `4.5`: strong fit; generate report, tailored resume plan, and answers.
- `4.0`: reasonable fit; apply if the company or learning upside is strong.
- `3.0-3.9`: weak or speculative; usually skip.
- `<3.0`: do not apply.

Evaluate:

- Role relevance to target roles.
- Evidence from `cv.md` and `profile/master.md`.
- Technical match.
- Internship/new-grad suitability.
- Location/logistics.
- Work authorization and visa sponsorship fit, using only known facts from `config/profile.yml`.
- Company quality and legitimacy.
- Opportunity cost.

## Required Evidence

When making a claim, tie it to:

- Exact JD language.
- A concrete profile/CV item.
- A gap or risk.
- A mitigation or rewrite plan.

## File Updates

For each evaluated role:

- Save a report in `reports/`.
- Save or link the JD in `jds/`.
- Update `data/applications.md`.

## Source Ideas

- From `career-ops`: agent-led evaluation, reports, tracker, human review.
- From `Job_App_Agent`: deterministic JD fetch into Markdown.
- From `job-ops`: think in terms of source quality, location/visa fit, application funnel status, and response-rate learning. Do not copy `job-ops` product code into this repo.

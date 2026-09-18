# Data Contract

This repo separates private user state from reusable system machinery. Real user-layer files are
ignored by Git; tracked `.example` files document the expected shape.

## User Layer

These files are personal data, work product, or preferences. Do not overwrite them during system updates.

| File | Purpose |
|---|---|
| `cv.md` | Canonical resume/CV in Markdown |
| `config/profile.yml` | Identity, target roles, constraints, logistics, salary, preferences |
| `profile/master.md` | Master bank of projects, experience, proof points, stories |
| `data/applications.md` | Application tracker |
| `data/pipeline.md` | Incoming URL/JD queue |
| `data/reminders.md` | Repo-local reminders and recurring maintenance tasks |
| `jds/*` | Saved job descriptions |
| `reports/*` | Evaluations, drafts, interview prep |
| `output/*` | Generated resumes, cover letters, PDFs |

## System Layer

These files define behavior and can be improved as the workspace evolves.

| File | Purpose |
|---|---|
| `AGENTS.md` | Codex operating instructions |
| `modes/*` | Playbooks for evaluation, pipeline, application assistance, tracking |
| `scripts/*` | Local deterministic helper commands |
| `templates/*` | System-owned, ATS-readable artifact templates |
| `tools/*` | Vendored or adapted deterministic tools |
| `docs/*` | Source notes and design docs |

## Rule

Personalization goes in the user layer. System behavior goes in the system layer.

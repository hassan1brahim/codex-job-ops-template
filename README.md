# Local Job Ops Template

A private-by-default job-search workspace for Codex or Claude Code. It includes:

- a locally hosted job board at `http://localhost:8765`;
- job discovery from public feeds and direct ATS pages;
- a persistent SQLite workflow tracker;
- job-description fetching and evaluation playbooks;
- evidence-grounded, one-page LaTeX resume generation and validation;
- application status, reminders, and analytics;
- optional macOS scheduling.

It does **not** auto-submit applications. The workflow stops at a reviewable resume and application link.

## Privacy model

The repository tracks the application code and blank examples. Your real profile, application history, job descriptions, reports, databases, and generated resumes are ignored by Git.

Do not remove those ignore rules unless you intentionally want private career data in a remote repository.

## First-time setup

Clone the repository, then create your private local files from the examples:

```bash
cp config/profile.example.yml config/profile.yml
cp config/board.example.json config/board.json
cp cv.example.md cv.md
cp profile/master.example.md profile/master.md
cp profile/resume-variants.example.md profile/resume-variants.md
cp data/applications.example.md data/applications.md
cp data/pipeline.example.md data/pipeline.md
cp data/reminders.example.md data/reminders.md
```

Fill in the files described below, then verify the workspace:

```bash
python scripts/jobops.py doctor
python -m unittest discover -s tests
```

Resume validation requires `tectonic`, `pdfinfo`, and `pdftotext` on your PATH.

## Personal details to add

### `config/profile.yml`

Add only facts you are comfortable storing on your own computer:

- full name, professional email, phone number, city/state, and timezone;
- LinkedIn, GitHub, portfolio, and personal-site URLs;
- target roles, industries, locations, seniority, and preferred company types;
- work authorization and whether sponsorship is needed;
- graduation date, degree status, availability, relocation preferences, and onsite policy;
- optional compensation expectations, notice period, and deal-breakers.

Do not guess legal or demographic information. Keep disability, race, gender, veteran status, and other voluntary self-identification answers out of the profile unless you deliberately want them stored.

### `cv.md`

Add the canonical resume facts:

- education, dates, GPA only if you want it used;
- employment and volunteer experience;
- projects, technologies, and links;
- verified metrics and outcomes;
- awards, certifications, publications, or leadership.

### `profile/master.md`

This is the detailed evidence bank. Add more context than fits on a resume:

- what you personally built, fixed, tested, or owned;
- team size, users, scale, latency, cost, accuracy, or adoption metrics;
- architecture and technology choices;
- difficult bugs, tradeoffs, failures, and lessons;
- stakeholder collaboration and customer outcomes;
- facts that must never be exaggerated or inferred.

### `profile/resume-variants.md`

Record ordering rules and role-specific emphasis, such as which experience must always appear, what can be omitted, and which projects best support backend, frontend, data, AI, or systems roles.

### `config/board.json`

Set your home coordinates, primary/secondary state codes, display labels, and LinkedIn search queries. Coordinates are used only for local distance ranking.

## Use the local tracker and board

```bash
python scripts/jobboard.py refresh
python scripts/jobboard.py describe --limit 25
python scripts/jobboard.py render
python scripts/jobboard.py serve
```

Then open `http://localhost:8765`. The SQLite database is created locally at `data/board.db` and is ignored by Git.

Useful commands:

```bash
python scripts/jobboard.py list --tier 1
python scripts/jobboard.py requests
python scripts/jobboard.py analytics
python scripts/jobboard.py sync-resumes
python scripts/jobops.py add-url "https://example.com/job"
python scripts/jobops.py fetch "https://example.com/job"
python scripts/jobops.py tracker
python scripts/jobops.py check-resume output/Your_Name_Role_Resume.tex
```

Validated `READY_TO_APPLY` PDFs can be mirrored to `~/Downloads/resumes`. Marking a role `APPLIED` removes only the managed upload copy; the local workflow record remains.

## Optional macOS automation

The installer derives the repository path and current macOS username automatically:

```bash
bash scripts/install-jobboard-schedule.sh
```

It installs the local board server plus hourly and twice-daily refresh jobs. Review the scripts and limits before enabling automation.

## Repository layout

| Path | Purpose |
|---|---|
| `AGENTS.md`, `CLAUDE.md` | Agent routing and safety rules |
| `DATA_CONTRACT.md` | Private user layer versus reusable system layer |
| `config/*.example.*` | Safe configuration templates |
| `profile/*.example.md` | Evidence-bank templates |
| `data/*.example.md` | Empty tracking templates |
| `scripts/jobboard.py` | Discovery, tracking, local server, and analytics CLI |
| `scripts/jobops.py` | Intake, fetching, tracker, and resume validation CLI |
| `modes/` | Evaluation, resume, application, and tracking playbooks |
| `templates/resume.tex` | ATS-readable LaTeX starting point |
| `tools/jd_fetcher/` | Deterministic job-description fetcher |

## Safety boundaries

- Never fabricate resume claims or metrics.
- Never submit an application or send a message without explicit approval.
- Treat work authorization, graduation dates, compensation, and demographic answers as user-provided facts only.
- Keep real files matched by `.gitignore` out of commits and remote repositories.

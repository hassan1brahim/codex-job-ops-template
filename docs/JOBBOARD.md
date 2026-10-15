# Job Board

The discovery and triage half of this workspace. It finds SWE / AI / Data **internships**, ranks
them with an explainable priority score, and hands only the strongest new roles to the existing
evaluation and resume pipeline.

## Quick Start

```bash
python scripts/jobboard.py refresh            # pull every source
python scripts/jobboard.py list --tier 1      # configured primary + secondary areas
python scripts/jobboard.py describe --limit 25
python scripts/jobboard.py next               # top unworked job, as a brief
python scripts/jobboard.py queue <key>        # hand it to data/pipeline.md
python scripts/jobboard.py autoqueue           # queue the top five new roles
python scripts/jobboard.py render             # write output/board.html
python scripts/jobboard.py serve              # interactive board at localhost:8765
python scripts/jobboard.py set-status <key> APPLIED
python scripts/jobboard.py analytics
```

Install hourly discovery (including a fast LinkedIn sweep of the last 24 hours) plus deeper description,
URL-check, and Codex processing runs at 08:30 and 17:30:

```bash
bash scripts/install-jobboard-schedule.sh
```

The installer also keeps the interactive frontend running at `http://localhost:8765` and restarts
it after a crash or login. Run `python scripts/jobboard.py serve` only when using the frontend
without the installed macOS services.

After a fresh build, baseline notifications once so the first scheduled run does not announce the
entire board:

```bash
python scripts/jobboard.py notify --mark-seen
```

## Ranking

Everything sorts by a 0–100 score:

| Signal | Weight |
|---|---:|
| Location | 35% |
| Freshness | 25% |
| Role relevance | 20% |
| Evidence-backed skill overlap | 10% |
| Preferred-company/domain signal | 5% |
| Application friction | 5% |

Freshness uses only the source posting timestamp. When that date is unavailable, `first_seen` is
shown as discovery time but does not grant a freshness bonus. LinkedIn candidates selected for
auto-queue are enriched with the visible applicant count; 50+, 100+, and 200+ counts receive
increasing competition penalties before Codex processing begins.
Location retains great-circle miles from the home coordinates in `config/board.json` while
distinguishing configured primary and secondary states, remote US, the remaining US, and international roles.

| Tier | Group | Note |
|---|---|---|
| 0 | Primary area | User-configured preferred state |
| 1 | Secondary area | User-configured secondary state |
| 2 | Remote (US) | Location-independent US role |
| 3 | Rest of US | Ordered by distance |
| 4 | International | Visa + relocation |

A role listed in several cities is ranked by its **best** configured location.

## Sources

| Source | State | How |
|---|---|---|
| **Simplify / Pitt CSC** | Working | `listings.json` from the GitHub repos. ~34k rows, ~4k live. Carries category, terms and sponsorship. This is the backbone. |
| **vanshb03** | Working | Same JSON shape, independent contributor pool. Adds roles Simplify misses. |
| **LinkedIn** | Working | Public guest endpoint, no login, 10 cards a page. `--linkedin-mode fast` (hourly): broad intern/internship/co-op searches over NJ, NY and the US, last 24h, newest first. `deep` (daily): adds 15 role searches over the last week without LinkedIn's internship facet, because many internships are filed as Entry level; the title filter keeps only internships. A LinkedIn sighting never overwrites a row another feed owns, and LinkedIn rows expire after 7 days unseen rather than on one missed run. |
| **Direct ATS** | Working | Learns Greenhouse, Lever, Ashby, Workday, and SmartRecruiters board IDs from known U.S. job URLs, then polls the companies directly. All feeds run hourly. Full descriptions arrive inline for Greenhouse/Ashby/Lever. |
| **Y Combinator** | Working | Parses the structured `data-page` payload on YC's public internships page. HTTP rejection, missing/schema-changed data, or a zero-row payload records the source as blocked. |
| **Wellfound** | Blocked | Cloudflare Turnstile on every request. Needs a real browser session. |
| **swelist** | Omitted | Its internship CLI reads an older Simplify repository; current Simplify cycles are already ingested directly. |

Blocked or structurally changed sources are recorded as blocked rather than silently returning
nothing, so `jobboard status` and the dashboard show real coverage. YC deliberately treats an HTTP
rejection, a missing/invalid embedded payload, or zero upstream rows as a source failure. A valid
page with rows that do not match the configured seasons or disciplines is reported as empty rather
than blocked.

## Filtering

Two independent guards, because no source is trustworthy alone:

1. **Category** must be Software or AI/ML/Data where the source provides one.
2. **Title** must contain an engineering role word *and* an internship word, and must not match the
   seniority/full-time or non-engineering-discipline excludes.

Category alone is too loose: Simplify files business roles like "Revenue Management Intern" under
`AI/ML/Data`. Title alone is too loose in the other direction. Both must agree.

Tune the rules in `scripts/board/filters.py`.

## Automation and workflow

`autoqueue` appends the best new roles to `data/pipeline.md` and moves them to `EVALUATING`.
Scheduled jobs stop after discovery, ranking, queueing, rendering, and notification. They do not
invoke Codex evaluation or generate resumes. Evaluation and resume work happens only when the user
explicitly requests it, and it never submits an application.

Workflow statuses are `NEW`, `EVALUATING`, `RESUME_GENERATING`, `READY_TO_APPLY`, `APPLIED`, `OA`,
`INTERVIEW`, `OFFER`, `REJECTED`, and `WITHDRAWN`. Serve the board locally to change them from each
card; the static HTML falls back to copying the corresponding CLI command.

The **Done** filter contains one card per validated `READY_TO_APPLY` resume PDF. Those PDFs are
mirrored into `~/Downloads/resumes` with company-first filenames for quick uploading. Changing a card to `APPLIED` removes its
managed Downloads copy and removes the card from Done while preserving the database record and
status history. Run `python scripts/jobboard.py sync-resumes` to reconcile the folder manually.

Use `jobboard artifacts` after processing to attach `fit_score`, `fit_report_path`,
`resume_tex_path`, `resume_pdf_path`, and `resume_generated_at`. Status history feeds
`jobboard analytics`; rates are zero until real applications are tracked.

## Descriptions

Fetched lazily, closest first, because fetching all ~1,800 would be thousands of requests for roles
you will never open. LinkedIn uses its guest JD endpoint; everything else goes through
`tools/jd_fetcher`, the same fetcher `jobops fetch` uses, so a description here is identical to one
saved by the existing workflow. Expect roughly 85% success: some career pages are JS-only.

## Files

| Path | Purpose |
|---|---|
| `scripts/jobboard.py` | CLI |
| `scripts/board/geo.py` | Location parsing, tiers, distance |
| `scripts/board/filters.py` | Internship + discipline rules |
| `scripts/board/store.py` | SQLite store, dedupe, status |
| `scripts/board/sources/` | One module per board |
| `scripts/board/describe.py` | Description fetching |
| `scripts/board/notify.py` | macOS notifications |
| `scripts/board/render.py` | HTML board |
| `data/board.db` | The board (user layer, gitignored) |
| `output/board.html` | Rendered board |
| `modes/board.md` | Codex/Claude playbook |

## Gotchas

- **`recompute`, not `refresh`, after a geo change.** Stored rows keep the distance computed when
  they were scraped. `jobboard recompute` re-derives tier/distance/label for every row from its
  stored locations with no network access. Without it two rows for the same city disagree.
- **Descriptions are never overwritten** by a refresh, so re-running refresh is cheap and safe.
- Dedupe uses normalized company/title values, aliases, ATS requisition IDs, and direct ATS URLs.
  Add genuine trade-name aliases to `COMPANY_ALIASES` in `store.py` as they appear.
- **LinkedIn will 429.** Each request waits out a 429 twice (20s, then 60s) before the source stops
  early and records it in the note rather than failing the run. If `jobboard status` shows LinkedIn kept 0 with "stopped early", wait a few hours.
- **Notifications need permission once.** macOS asks the first time; if it is denied, `osascript`
  fails silently forever after.

## Relevance filter notes

- Feeds without a category (LinkedIn, company ATS boards, vansh) must carry a software/data/AI word in
  the title (`TECH_TITLE` in `scripts/board/filters.py`); "Engineering Intern" alone is usually
  structural, process or mining work.
- Every refresh retires untouched `NEW` rows that the current filter rejects (`FILTERED_OUT`), so a
  filter fix applies to old rows too. Saved rows and anything past `NEW` are never touched.

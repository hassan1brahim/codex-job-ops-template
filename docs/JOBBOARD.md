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

Install hourly discovery (including a capped two-page LinkedIn pass) plus deeper description,
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
| **LinkedIn** | Working | Public guest endpoint, no login. Also the only source with a reliable full-text description endpoint. Runs hourly with a two-page cap and request throttling. |
| **Direct ATS** | Working | Learns Greenhouse, Lever, and Ashby board IDs from known URLs, then polls the companies directly. Full descriptions arrive inline for Greenhouse/Ashby/Lever. |
| **Wellfound** | Blocked | Cloudflare Turnstile on every request. Needs a real browser session. |
| **swelist** | Blocked | Next.js app with no API route and no data in the RSC payload. Needs Playwright, and it aggregates the same GitHub feeds we already read. |
| **Y Combinator** | Blocked | `workatastartup.com/jobs` returns 406 without a session; the public board's Algolia keys are buried in JS bundles. |

The blocked three are recorded as blocked rather than silently returning nothing, so
`jobboard status` always shows real coverage. Turning any of them on means adding a Playwright
fetcher; note that a previous Playwright batch job on this machine leaked orphaned Chrome renderers
to ~24 GB RAM, so validate teardown on a small wave first.

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
The hourly job runs at most one isolated Codex evaluation/resume worker for roles discovered in the
last two hours. The scheduled daily job processes one best remaining queued role, so older strong
jobs are not abandoned.
That command requires explicit work authorization in the private `config/profile.yml`. It never
submits an application.

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
- **LinkedIn will 429.** The source stops early and records it in the note rather than failing the
  run. If `jobboard status` shows LinkedIn kept 0 with "stopped early", wait a few hours.
- **Notifications need permission once.** macOS asks the first time; if it is denied, `osascript`
  fails silently forever after.

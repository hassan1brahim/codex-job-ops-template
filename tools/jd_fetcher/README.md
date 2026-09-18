# Module 1 — Job Description Fetcher

(Module 0 is Job Discovery — see `foundations/job_discovery/` at the repo root.
That module finds job links; this one takes a single job link and turns it
into a clean, normalized job description saved as a Markdown file.)

No database, no JS execution, no browser automation. Just an HTTP GET (or a
JSON API call) per job, then a parser, then a `.md` file.

## Running it

```bash
python -m tools.jd_fetcher scrape "<job-url>"
```

Optional flags: `--title`, `--company`, `--location` (override what gets
auto-detected), `--jobs-dir` (default `module_1_jd_fetcher/.job_app_agent/jobs/`
— co-located with this module, not the caller's cwd — also settable via
`JOB_APP_AGENT_JOBS_DIR`), `--filename`, `--overwrite`.

On success it prints the saved path and a one-line summary. On failure
(bad URL, login wall, no real JD found) it prints a clean message to stderr
and exits with code 1 — it does not crash with a raw traceback.

## What kind of scraper is this?

Not a general-purpose one. It's stdlib-only HTTP (`urllib.request`) plus a
per-site router with three different strategies, depending on what the
target site actually exposes:

**1. Direct public APIs (Greenhouse, Lever, Workday)** — no HTML involved at
all. The job ID is parsed out of the URL, then the request goes straight to
that platform's public JSON API (e.g.
`boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}`). The response is
already structured JSON — `title`/`description`/`location` are just read out
of it.

**2. JSON embedded inside the HTML page (Simplify)** — the page is fetched
as HTML, but the job data isn't read from rendered text. Simplify is a
Next.js app, and Next.js serializes its page data as a JSON *string* inside
a `<script id="__NEXT_DATA__" type="application/json">` tag — written there
specifically so the browser doesn't need a second network request. We grab
just the text inside that tag and run it through `json.loads()`, the same
function you'd use to read a `.json` file off disk. No JavaScript is
executed anywhere — it's inert JSON text, not code. We then recursively walk
the resulting dict/list looking for a `description`-shaped field.

If you give it a Simplify *search-results* URL (e.g.
`simplify.jobs/jobs?...&jobId=<uuid>`) instead of a direct posting URL, it
first rewrites that to the canonical `simplify.jobs/p/<uuid>` page (Simplify
resolves the UUID alone, no slug needed) before doing the above.

**3. Actual HTML markup parsing (LinkedIn)** — LinkedIn has no public API and
doesn't embed the JD as JSON either. The full job description genuinely only
exists as rendered HTML, inside one specific element:
`<div class="show-more-less-html__markup">`. So this is the one case where
we actually walk the HTML tag tree (using Python's stdlib `html.parser`,
not a real DOM) looking for that element by its `class` attribute, and pull
out just its text. Two extra steps make this work reliably:
- The URL is first resolved to the canonical `linkedin.com/jobs/view/{id}/`
  (tracking query params, or a `currentJobId` param from a search-results
  URL, get stripped/redirected through).
- The request uses browser-like headers (`User-Agent`, `Accept`,
  `Accept-Language`) — LinkedIn serves a different, content-free shell page
  to obvious non-browser requests.
- If LinkedIn redirects to `/signup`, `/login`, or `/authwall`, we raise a
  clean error instead of silently saving a useless login-wall page.

**Everything else (Ashby, SmartRecruiters, unrecognized sites)** falls back
to a generic path that tries, in order: JSON-LD `JobPosting` schema blocks →
Next.js `__NEXT_DATA__` (same trick as Simplify) → `og:`/meta description
tags (shortest, least reliable) → raw visible-text extraction as a last
resort.

### Why no Selenium/Playwright?

Because none of the sites above need it — every one either has a real API,
embeds its data as parseable JSON, or has the JD sitting in static HTML.
A real headless-browser engine is only required if a site computes its
content via JavaScript that runs *after* the initial page load (true SPA,
nothing in the raw HTTP response). If you hit a site like that, this
approach hits a wall — that's the point at which Playwright/Selenium would
actually be necessary, not before.

### Why does it sometimes get garbage instead of failing?

If a URL points at the wrong *type* of page — a search-results listing, a
company directory page, anything that isn't an individual job posting — and
that page happens to have over 80 characters of *some* text (even just a
generic meta description), the scraper has no way to know that text isn't a
real job description. It's not validating "is this actually a JD," just
"is there enough text here to be worth saving." Always pass a direct
job-posting URL, not a search/listing URL, for reliable results.

## Why `cli.py` exists

`jd_intake.py`/`job_markdown.py`/`models.py` are a plain library — no
`argv`, no `print`, no knowledge of "command line" at all. `cli.py` is the
one adapter that turns that library into something runnable from a shell:
it parses `sys.argv` into typed values (`argparse`), resolves config from
the environment, calls the library functions, and is the only place that
catches `JobDescriptionFetchError`/`ValueError` and turns them into a clean
stderr message + exit code instead of a raw traceback.

## Why `python -m module_1_jd_fetcher` and not `python cli.py`

`cli.py` lives inside a package and uses relative imports
(`from .config import AppConfig`, `from . import jd_intake`, etc.) — those
only resolve if Python already knows what package the file belongs to.
Running `python cli.py` directly treats it as a standalone script with no
parent package, so `from .config import ...` fails immediately with
`ImportError: attempted relative import with no known parent package`.

`python -m module_1_jd_fetcher` instead tells Python to run the
**package** (it imports `module_1_jd_fetcher/__init__.py` first, then runs
`__main__.py`, the file Python looks for by convention when you `-m` a
package) — so every relative import inside resolves correctly against the
real package context.

### What actually happens when you run it

1. Python loads `module_1_jd_fetcher` as a package, runs `__main__.py`:
   `from .cli import main; raise SystemExit(main())`.
2. `cli.main()` parses argv via `argparse`, builds `AppConfig.from_env()`.
3. Calls `jd_intake.fetch_from_url(url, ...)` inside a `try/except` — this
   is the whole router/scraper chain described above.
4. On success, `write_job_markdown()` (`job_markdown.py`) serializes the
   result to `---`-delimited frontmatter + body and writes it under
   `module_1_jd_fetcher/.job_app_agent/jobs/`. On failure, prints the error
   and returns `1`.
5. `SystemExit(main())` turns that return value into the process's real
   exit code.

## Tests

`tests/tests_for_Module_1/`:

- `test_jd_intake.py`, `test_job_markdown.py`, `test_cli.py` — fast unit
  tests, all I/O mocked (`monkeypatch`), no network calls. Run with
  `python -m pytest tests/ -q`.
- `test_live_integration.py` — opt-in, hits the real internet against the
  actual URLs scraped manually during development (LinkedIn, Simplify,
  an invalid Greenhouse URL). Skipped by default since live job postings
  can expire or sites can change their markup. Run explicitly with:
  ```bash
  RUN_LIVE_TESTS=1 python -m pytest tests/tests_for_Module_1/test_live_integration.py -v
  ```

## Files

- `cli.py` — argv parsing, the only place that talks to stdout/stderr/exit
  codes.
- `jd_intake.py` — the actual scraper: source detection, per-site fetchers,
  HTML/JSON parsers, text normalization, keyword extraction.
- `job_markdown.py` — serializes a `JobDescription` to/as a `.md` file with
  JSON-safe frontmatter.
- `models.py` — the one shared dataclass, `JobDescription`.
- `config.py` — resolves `jobs_dir` from the environment
  (`JOB_APP_AGENT_DATA_DIR` / `JOB_APP_AGENT_JOBS_DIR`).

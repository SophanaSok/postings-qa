# Job Posting Automation Bot

A Playwright-based bot that scrapes job listings from **LinkedIn**, **Indeed** and **Glassdoor**, runs a set of
**data-QA checks** to drop duplicate, irrelevant and low-quality listings, keeps a history in SQLite, and exports
an **Excel dashboard with charts** for analysis.

```
jobbot run                # scrape → QA → output/jobs-YYYY-MM-DD.xlsx
```

## What you get

| Sheet | Contents |
|---|---|
| **Dashboard** | KPI row (scraped / passed QA / rejected / new this run / blocked sources) and seven charts: jobs by source, top 10 companies, postings per day, remote vs on-site, salary distribution, QA rejections by check, new vs previously seen |
| **Jobs** | Listings that passed QA as a filterable Excel table: hyperlinked URL, real dates, numeric salary columns, `New` rows highlighted |
| **Rejected** | Everything that failed QA, with the reason(s) per row so the filtering is auditable |
| **QA Summary** | Per-source pass rates and per-check rejection / soft-warning counts |
| **Raw** | Every listing scraped this run, unfiltered |

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
uv run jobbot init          # writes config.yaml from config.example.yaml
uv run jobbot run
```

## Usage

```
jobbot run     [--headed] [--source linkedin,indeed] [--keywords "QA Engineer,SDET"] [--location "United States"]
               [--max-pages N] [--no-details] [--out FILE]
jobbot scrape  ...          # same as run, but no Excel (stores results + raw JSONL only)
jobbot export  [--days 30] [--out FILE]   # rebuild the workbook from the SQLite history, no scraping
jobbot stats                # last run summary
jobbot -v ...               # debug logging
```

Outputs: `output/jobs-<date>.xlsx`, `data/jobs.db` (history), `data/raw-<run>.jsonl` (raw scrape),
`data/debug/*.html` (saved only when a page could not be parsed).

## Configuration (`config.yaml`)

| Section | Keys |
|---|---|
| `search` | `keywords` (list), `location`, `max_age_days`, `max_pages` per keyword per source, `fetch_descriptions`, `max_details` |
| `sources` | `linkedin` / `indeed` / `glassdoor`: `{enabled: true}` |
| `browser` | `headed`, `profile_dir`, `delay_seconds: [min, max]`, `timeout_seconds` |
| `qa` | `include_keywords`, `exclude_keywords`, `remote_ok`, `locations`, `max_age_days`, `min_description_chars`, `salary_bounds_usd_year`, `blocked_companies`, `agency_patterns`, `spam_patterns` |
| `storage` / `export` | `db_path`, `output_dir`, `filename` (`{date}` placeholder) |

Shipped defaults target QA / data roles in the United States with remote allowed.

## QA checks

Each listing passes through every check; a failure rejects it with a reason, a soft **flag** only annotates it.

| Check | Rejects when |
|---|---|
| `duplicate` | same id, or same normalised title + company + location already seen this run |
| `required_fields` | title, company or URL missing |
| `url_valid` | not http(s), or host does not belong to the source site |
| `title_relevance` | title contains an `exclude_keywords` word (whole-word), or none of `include_keywords` |
| `staffing_agency` | company is in `blocked_companies` or matches an `agency_patterns` regex |
| `location_match` | not remote (when `remote_ok`) and location matches none of `locations` (flag if unknown) |
| `posting_age` | posted more than `max_age_days` ago, or in the future (flag if unknown) |
| `salary_sanity` | min > max, or the annualised amount is outside `salary_bounds_usd_year` (flag for estimates / non-USD) |
| `description_quality` | shorter than `min_description_chars`, or matches a `spam_patterns` regex (flag if not fetched) |

## How each site is scraped

| Site | Method | Notes |
|---|---|---|
| LinkedIn | Public guest job-search endpoints via Playwright's HTTP client, no browser window, no login | 10 results per page; job detail (description, seniority, employment type) from the guest `jobPosting` endpoint. Rate limits (HTTP 429) are backed off. |
| Indeed | Headless Chromium on the search page; jobs come from the embedded `mosaic-provider-jobcards` JSON (DOM fallback) | Details are read from the search page's side pane (`vjk=`), because `/viewjob` redirects guests to sign-in. Cloudflare challenges are common on the second and later page loads. |
| Glassdoor | Headless Chromium on the `SRCH` results page; GraphQL responses are captured in flight, DOM cards are the fallback | Job detail is loaded by clicking the card (no navigation); direct job pages are usually behind a Cloudflare challenge. |

### When a site blocks the bot

Indeed and Glassdoor use Cloudflare bot management. The bot:

1. runs headless with a per-site persistent browser profile in `.browser-profile/`;
2. on a challenge page, wipes that profile and retries once (a flagged cookie is the usual cause);
3. if still challenged, marks the source **blocked**, keeps whatever was already collected, and continues
   with the other sources. The run summary and the QA Summary sheet show which sources were blocked.

Run `jobbot run --headed` to get a visible browser: solve the challenge by hand once, and the persistent profile
keeps the clearance cookie for later headless runs. No credentials are ever used; the bot only reads pages that
are public to logged-out visitors.

## Development

```bash
uv run pytest            # offline tests against saved HTML/JSON fixtures in tests/fixtures/
```

Layout: `jobbot/sources/` (one adapter per site, pure `parse_*` functions separated from fetching),
`jobbot/qa/` (checks + pipeline), `jobbot/storage.py` (SQLite), `jobbot/export/` (openpyxl workbook + charts),
`jobbot/browser.py` (Playwright session, hardening, challenge detection), `jobbot/cli.py`.

## Caveats

- Scraping job boards is against most of their terms of service. This project is for personal, low-volume use;
  keep `max_pages` small and the delays on.
- Site markup changes without notice. Adapters try structured data first and fall back to DOM selectors; when
  neither parses, the page is saved under `data/debug/` for inspection.

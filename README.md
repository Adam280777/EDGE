# EDGE

Equity Discovery & Growth Engine is a local equity-research and decision-support
application. It is not an autonomous trading bot.

## What works in this milestone

- The real, pinned **NautilusTrader 2.0.0rc8** native engine on Windows / CPython 3.13.
- Explicitly fictional quote fixtures, a deterministic round trip and an engine
  rejection scenario. These are acceptance tests, **not market research**.
- Strict identity, source, timestamp, currency/unit, price and liquidity validation.
- SQLite run index with per-run input, manifest and result JSON evidence.
- A local simulation CLI and Streamlit workbench with native workspace navigation
  between engine acceptance (default), explicit SEC filing research and a local
  user-authored Research journal, plus the
  opt-in `edge sec-filings TICKER` metadata lookup.
- A local amber-on-near-black 1980s terminal dashboard: four compact, square,
  ruled panes for acceptance controls, fixture provenance, session outputs and
  persisted history. Cyan labels and explicit green/red status cues; no visual
  assets, live feed or fabricated market panels.
- An opt-in OpenBB v5 SEC filing-metadata adapter. Its validation and provider
  contract have offline tests; no live SEC lookup has yet been verified.
- Automated input, accounting, persistence and application tests.

Nautilus owns order processing, simulated fills, account cash and positions.
EDGE owns input validation, product workflows, provenance and presentation. It
does not implement a replacement backtester. AI is not used for financial results.

## Run locally (PowerShell)

The existing `.venv` is preserved. Use a separate environment for the foundation:

```powershell
& .\.venv\Scripts\python.exe -m venv .venv-nautilus
& .\.venv-nautilus\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-simulation.txt
& .\.venv-nautilus\Scripts\python.exe -m pip install -e '.[dev,dashboard]'

& .\.venv-nautilus\Scripts\python.exe -m edge doctor
& .\.venv-nautilus\Scripts\python.exe -m edge simulate --scenario round-trip
& .\.venv-nautilus\Scripts\python.exe -m edge simulate --scenario risk-rejection
& .\.venv-nautilus\Scripts\python.exe -m edge runs
& .\.venv-nautilus\Scripts\python.exe -m edge show RUN_ID
& .\.venv-nautilus\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

Simulation CLI outputs go to `data` under the working directory. Use `--output-dir`
on `simulate`, `runs` and `show` to select another directory. The dashboard uses
the repository's `data` directory. Run the tests without starting a server:

```powershell
& .\.venv-nautilus\Scripts\python.exe -m pytest -q
& .\.venv-nautilus\Scripts\python.exe -m pip check
```

`requirements-windows-py313.lock.txt` records the exact installed dependency set
used for Windows acceptance. For a fresh matching environment, install that file
and then `pip install --no-deps -e .`. It is not a portable or hash-verified lock.
The checked-in Streamlit configuration binds to loopback and disables usage
telemetry; it does not expose a public server.

No commands connect a broker, obtain paid data or place live orders. The dashboard
runs the engine only when a simulation button is pressed, not on normal reruns.
Planning context is under the fixture pane's disclosure. Pane rows stack below
1050 CSS pixels; tables retain native scrolling. Use Tab to reach controls and
Enter/Space to activate buttons (no custom function-key bindings). Recorded
outputs, event tabs, assumptions and JSON evidence downloads remain available.
Dashboard workflows can be checked without a service:

```powershell
& .\.venv-nautilus\Scripts\python.exe -m pytest -q tests/test_dashboard.py tests/test_dashboard_sec.py tests/test_dashboard_journal.py
```

AppTest verifies behavior, not rendered CSS or visual/keyboard accessibility;
those require a browser check of the installed Streamlit version.

## Local Research journal

Choose **Research journal** in Workspace, then **Swing** or **Long-term**.
Each horizon has its own saved-entry list and new-entry form. This is a local,
append-only record of user-authored opinions, not verified research,
recommendations, trade plans or validated company identity. No market data,
financial calculations, network requests or automatic SEC imports are involved.

Choose **New entry**, complete the required title, ticker, company, exact
`YYYY-MM-DD` research date and thesis, then explicitly click **Save new entry**.
Enter does not submit. Bull/bear cases, catalysts and invalidation are optional.
The form offers up to three manual labelled source references, each with an
optional absolute HTTPS URL and optional source date. Dates cannot be future;
URLs cannot contain credentials or whitespace. Title, company and source labels
are limited to 200 characters, narratives to 10,000, and URLs to 2,048.
Unused sections, citations, URLs and source dates are explicitly shown as not
supplied. All notes and references display literally, not as HTML or Markdown.
Unsaved edits may be lost on navigation.

Only a valid explicit save initializes/writes `data/research-journal.sqlite3`,
separate from engine evidence and SEC session results. No existing database is
migrated. Startup, navigation, list/view, draft edits, reruns and downloads do
not create entries. There are no edit/delete controls. Saved snapshots retain
their entry ID, user-authored provenance and UTC creation time independently of
draft edits; research/source dates remain user declarations, not retrieval times.
After a failed attempt any previous snapshot is labelled as previous, never a
new success. Saved entries persist across sessions; lists paginate ten at a time.
**Download saved entry (JSON)** exports the saved backend entry envelope with no
extra disk file or resubmission. This local file is not encrypted, synced or
tamper-proof; protect the data directory and avoid storing sensitive information.

## Optional SEC filing metadata

The optional `sec` extra provides an explicit SEC filing-metadata lookup through
OpenBB v5. It returns filing identifiers, forms, dates and SEC archive URLs only;
it is not a price or financial-statement feed, is not persisted to the simulation
database, and is available in the separate **SEC filing research** workspace.
Importing EDGE, requesting help,
or running other commands does not load the SEC provider or make an SEC request.
Only the explicit `sec-filings` command, `lookup_sec_filings(...)` API call, or
dashboard **Look up SEC filings** form submission can initiate a lookup.

The dashboard uses a native horizontal workspace selector; its default engine
workbench retains the four-pane acceptance workflow. The SEC page accepts a US
ticker, exact allowlisted forms and optional inclusive ISO filing-date bounds.
Its native form does not submit on Enter. No request occurs on page load,
navigation, edits, reruns, disclosures or evidence downloads. Provider code loads
only on an explicit lookup, after input/configuration validation.

Results show validated ticker/CIK identity, SEC archive links, returned/provider/
excluded counts, submitted filters, response coverage and warnings, source,
provider/framework versions, UTC retrieval window and remaining provenance.
Unavailable dates/documents remain null in JSON and blank in the table.
The JSON download follows the CLI contract below (no dashboard envelope).
Results are tied to the submitted filters, never new draft edits; a failed attempt
clears previous success and its download. Results remain session-local with no
disk, database or cache persistence; another browser session starts empty.
This is metadata research only, not prices, financial statements, simulation
input, production acceptance or proof of a verified live SEC connection.

Install the extra in the isolated environment:

```powershell
& .\.venv-nautilus\Scripts\python.exe -m pip install -e '.[sec]'
```

Before calling the command, API or dashboard lookup, set `EDGE_SEC_USER_AGENT` in the process to an
SEC-compliant identification string with a monitored contact email. Do not use
a placeholder contact. For example, PowerShell can prompt for it without adding
the value to a source file:

```powershell
$env:EDGE_SEC_USER_AGENT = Read-Host "SEC User-Agent: identifying name and monitored contact email"
```

For the dashboard, launch/restart Streamlit from that configured server environment.
Never enter this contact in the dashboard: it has no contact/credential input.
Missing/invalid configuration produces setup guidance on submit. Expected lookup
errors are explicit and use fixed contact-safe messages; raw provider exception
details and headers are not displayed or retained as session evidence.

Then, when you intend to make a real SEC request:

```powershell
& .\.venv-nautilus\Scripts\python.exe -m edge sec-filings MSFT
& .\.venv-nautilus\Scripts\python.exe -m edge sec-filings MSFT --start-date 2024-01-01 --end-date 2024-12-31 --form 10-K --form 10-Q
```

The installed console entry point is equivalently `edge sec-filings MSFT`.
Dates must be ISO calendar dates (`YYYY-MM-DD`); bounds are inclusive filing dates,
not report or acceptance dates. Dates before 1993-01-01, future start dates, and
reversed windows are rejected before provider access. A future end date is allowed,
consistent with the API. Omit bounds for no corresponding date restriction.
Forms are case-normalized, deduplicated in argument order, and matched exactly;
repeat `--form` rather than using commas or wildcards. Allowed forms are `10-K`,
`10-K/A`, `10-Q`, `10-Q/A`, `8-K`, `8-K/A`, `20-F`, `20-F/A`, `40-F`,
`40-F/A`, `6-K`, `6-K/A`, and `DEF 14A` (quote forms containing spaces).
Omitting `--form` selects `10-K`, `10-K/A`, `10-Q`, `10-Q/A`, `8-K`, `8-K/A`.
The ticker is normalized to uppercase; share-class dots become hyphens for SEC.

Success exits zero and prints one JSON object to stdout, with no lookup persistence,
cache, or output-file option. Errors go to stderr with nonzero status, without an
expected-error traceback or the configured contact. The exact object contract
below uses type notation, not sample company data: `date` is a `YYYY-MM-DD` JSON
string, `utc_timestamp` is an ISO 8601 JSON string with `+00:00`, and `| null`
marks nullable values. Tuples become JSON arrays; counts remain integers and flags
remain booleans. Any Decimal values are encoded as exact decimal strings, never
floats; unsupported values are not silently stringified.

```text
{
  "identity": {
    "requested_ticker": string,
    "sec_ticker": string,
    "cik": string,
    "sec_primary_ticker": string
  },
  "filings": [{
    "cik": string,
    "accession_number": string,
    "form_type": string,
    "filing_date": date,
    "report_date": date | null,
    "accepted_at": utc_timestamp | null,
    "primary_document": string | null,
    "primary_document_url": string | null,
    "filing_index_url": string
  }],
  "requested_form_types": [string],
  "start_date": date | null,
  "end_date": date | null,
  "provider_record_count": integer,
  "excluded_record_count": integer,
  "coverage_start": date,
  "coverage_end": date,
  "coverage_may_be_incomplete": boolean,
  "warnings": [string],
  "provenance": {
    "source": string,
    "provider": string,
    "provider_version": string,
    "framework_version": string,
    "endpoints": [string],
    "retrieved_at_start": utc_timestamp,
    "retrieved_at_end": utc_timestamp,
    "timeliness": string,
    "cache_used": boolean,
    "user_agent_configured": boolean,
    "usage_scope": string
  }
}
```

CIKs are ten-digit, zero-padded strings, not numbers. Filings are sorted by filing
date then accession number, descending. A valid lookup with no matching forms or
dates returns an empty `filings` array and a warning, not invented records.
`provider_record_count` counts all response records; `excluded_record_count`
counts those outside the requested forms/window, without a reason breakdown.
Coverage dates describe the full **observed provider response span**, before
filtering, not a guarantee of complete history. `coverage_may_be_incomplete` is
true when no start date was supplied or it precedes the observed coverage start.
Warnings retain provider and coverage caveats. Provenance describes delayed,
as-of-retrieval metadata, not a real-time feed; `cache_used` is false and
`user_agent_configured` is true on success. The User-Agent itself is never returned.

The adapter disables OpenBB's persistent cache, validates ticker/CIK identity,
filing types, dates, accession numbers and SEC URLs, and reports retrieval
provenance and coverage limitations. The provider's recent-submissions response
may omit older filings; the result warns when its observed coverage is incomplete.
The adapter currently depends on OpenBB's internal SEC fetcher and shared-header
modules, overrides the provider's placeholder User-Agent only during a locked
lookup, and accepts only `openbb-core==2.0.1` with `openbb-sec==2.0.0`. This is a
version-sensitive integration, not yet a production-accepted data source. Tests
stub provider responses and block external requests; the example above is the
first step that would contact SEC, and has not been run as part of this milestone.

## Input and evidence

The default dataset is `EDGE_TEST.SIM`, a fictional USD instrument with six
historical **synthetic** bid/ask quotes. The fixed source timestamp and data hash
make financial inputs reproducible. Dates do not make these observations real
market data. Full provenance is in every saved manifest.

`--dataset PATH` accepts only `edge.quote-dataset` version 1 JSON with the same
synthetic-only contract. It cannot enable arbitrary symbols, currencies, brokers
or strategy code. Unknown fields, real-data declarations, missing or ambiguous
units, duplicates, crossed quotes, unexplained large jumps and unknown/action-bearing
histories are rejected. Current quality thresholds apply to test fixtures and are
not investment risk settings.

Each successful engine invocation produces:

```text
data\
  edge.sqlite3
  runs\
    RUN_ID\
      dataset.json
      manifest.json
      result.json
```

The manifest records the engine/strategy versions, configuration, source,
timestamps, synthetic status, dataset hash and assumptions. Financial values are
decimal strings, not AI estimates. A failed invocation remains a `failed` run
with its error rather than becoming an empty successful result. An interrupted
process can leave a `running` record; inspect its evidence before retrying as a
new run. Existing runs are never overwritten.

The default round-trip fixture buys 10 fictional shares at USD 100.10 and sells
them at USD 100.90, charging USD 1.00 per order. Expected final cash is
USD 10,006.00, realized P/L is USD 6.00 and ending quantity is zero. The rejection
fixture deliberately sets an engine-only maximum-order-notional acceptance policy
below the order value: Nautilus must deny it with no fills and preserve USD
10,000.00 cash. This fixture policy is not a user investment risk setting.

## Scope and acceptance gates

**This is an engine integration workbench, not an investment-ready platform.**
The first tests use quote-based fills, explicit spread and fixed commissions.
They do not model market impact, realistic latency, queue priority, annualised
performance or profitable alpha. Quote-event timing must not be interpreted as
a same-close bar strategy or broker fill guarantee.

The application pins the **v2 API**. `/latest` upstream documentation can advance
beyond rc8. Neither v1 examples nor a different installed engine version are
silently substituted. A prerelease passing these fixtures is not blanket
commercial-production acceptance.

**Real market histories are deliberately blocked.** The current OpenBB adapter
is limited to SEC filing metadata and does not ingest prices. General Nautilus
corporate-action accounting must not be assumed complete. Before enabling any
market-data integration or passing real histories to Nautilus, confirm provider
rights, identity, GBp/GBP, availability times, adjustment basis and complete
action coverage. Reject affected or unknown histories until positions, working
orders, cash and reports have supported, tested treatment.

The approved integration sequence remains:

1. Nautilus engine and reproducible evidence foundation (this milestone).
2. OpenBB SEC filing-metadata adapter (opt-in CLI and Python API implemented; real lookup not
   yet verified); separately, licensed market-data integration and Nautilus
   catalog acceptance remain gated.
3. Qlib research/signals in an isolated Python 3.12 worker; not a second simulator.
4. EDGE discovery, fuller separate swing/long-term workflows and manual holdings
   remain future work; the local user-authored swing/long-term journal is implemented.
5. skfolio target allocations; selective Vibe-Trading research workflow reuse.
6. Kronos as an isolated, independently evaluated experiment, not financial truth.

`openbb-core==2.0.1` and `openbb-sec==2.0.0` are included in the optional `sec`
extra and the accepted Windows lock; the explicit metadata lookup is available
in the dashboard's SEC workspace. Qlib,
skfolio, Kronos and Vibe-Trading are not claimed installed or operational here.
VectorBT and Backtesting.py remain excluded. Future sandbox/paper trading needs
separate authorization; live brokerage execution remains outside this implementation.

## Development boundaries

- Preserve the existing agents, skills, configuration and initial environment.
- Keep manual real-world holdings distinct from simulated positions.
- No silent edits to risk settings, stops, targets or position quantities.
- No network data fallback, fabricated missing observations or paid API usage.
- No destructive schema upgrades; unknown databases are rejected.
- Use exact upstream packages and supported interfaces, not vendored repositories.

See [third-party notices](THIRD_PARTY_NOTICES.md) for software and distribution
considerations. Market-data rights are separate from software licensing.

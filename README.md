# EDGE

Equity Discovery & Growth Engine is a local equity-research and decision-support
application. It is not an autonomous trading bot.

## Run EDGE

For normal use, launch **`dist\EDGE\EDGE.exe`** on Windows and keep the complete
`dist\EDGE` folder together. EDGE has one supported end-user form: a local
desktop window. Its embedded Streamlit UI uses a private loopback server; it is
not a hosted website, does not open a browser, and has no browser fallback.

The source checkout, `edge` CLI and `streamlit run app.py` commands below are
developer/test tooling only. They are not alternate consumer launch or
deployment instructions.

## What works in this milestone

- The real, pinned **NautilusTrader 2.0.0rc8** native engine on Windows / CPython 3.13.
- Explicitly fictional quote fixtures, a deterministic round trip and an engine
  rejection scenario. These are acceptance tests, **not market research**.
- Strict identity, source, timestamp, currency/unit, price and liquidity validation.
- SQLite run index with per-run input, manifest and result JSON evidence.
- A bundled Windows desktop workbench with native workspace navigation between
  engine acceptance (default), explicit SEC filing research and a local
  user-authored Research journal. The `edge` CLI is retained for developer
  diagnostics, simulation and local journal recovery.
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

## Developer setup and source-level testing (PowerShell)

The following commands are for developers validating a source checkout. They
are not required to run the desktop executable and do not provide a hosted or
browser-fallback product.

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
# Developer-only source UI check; normal use is dist\EDGE\EDGE.exe.
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
developers can verify those in the embedded WebView2 window on the installed
Streamlit version. No browser launch is part of the end-user workflow.

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
migrated. Startup, navigation, list/view, draft edits, reruns and exports do not
create entries or modify the journal database. There are no edit/delete controls.
Saved snapshots retain
their entry ID, user-authored provenance and UTC creation time independently of
draft edits; research/source dates remain user declarations, not retrieval times.
After a failed attempt any previous snapshot is labelled as previous, never a
new success. Saved entries persist across sessions; lists paginate ten at a time.
**Download saved entry (JSON)** exports the saved backend entry envelope without
changing the journal. EDGE opens a native Save As dialog; the file is written
only after you choose a destination and confirm. Existing files trigger the
dialog's overwrite confirmation. The exported file is not encrypted, synced or
tamper-proof; protect it and avoid storing sensitive information.

## Journal reliability / backup and recovery

The desktop **Backup and recovery** workspace uses native file dialogs to back
up the original journal or restore a separate copy. Backup refuses an existing
filename even if a dialog overwrite is approved. Restore refuses nonempty
destinations and never merges or replaces the active database. After successful
restore, select **Research journal → Open restored journal** to inspect that
copy for this session; **Return to original journal** returns to the unchanged
default. Other workspaces continue using the original application data root.

These commands are local and journal-only: no engine evidence, provider calls,
market data, credentials or other files are backed up. Choose an **absent** backup
filename in an existing directory (including a separate backup drive):

```powershell
& .\.venv-nautilus\Scripts\python.exe -m edge journal-backup C:\Backups\journal-2026-11-06.zip --journal-dir .\data
& .\.venv-nautilus\Scripts\python.exe -m edge journal-restore C:\Backups\journal-2026-11-06.zip --journal-dir .\recovered-journal
```

Backup uses SQLite's online backup API, not a raw copy of an open SQLite file.
It captures a consistent committed snapshot, including committed WAL content;
later saves are not included. It opens the source read-only and never initializes,
migrates or updates it. SQLite may coordinate read locks/WAL shared-memory state.
The ZIP contains only `research-journal.sqlite3` and a canonical format/version,
schema, record-count, byte-count and SHA-256 manifest. Every record and the exact
journal schema are validated. Snapshots are limited to 256 MiB; online backup
busy/restart retries have a ten-second deadline. Before ZIP parsing, restore
caps the raw archive at 256 MiB plus 80 KiB of manifest/header allowance; stored
member sizes are bounded separately before payload reads.

Restore checks the archive, hash, schema version, integrity and every record
before publishing. Its explicit destination must be a **new or empty directory**
whose parent exists. Existing journals (even empty/corrupt files), other directory
contents and stale SQLite sidecars are refused. There is no overwrite, merge,
import, migration or replacement option. Inspect a recovered journal separately;
the application continues using its original `data` directory. Keep the original
safe; switching an application to recovered storage is a separate manual action.
Paths containing symlinks or Windows reparse points (including junction roots
and ancestors) are refused, with checks repeated before publication. These are
path-based checks, not protection against a hostile concurrent filesystem actor.

Both operations publish only completed, closed files using an atomic no-clobber
hard link. Filesystems without hard-link support fail safely; no replacing-rename
fallback is used. Failed pre-publication attempts remove their own staging files,
not unrelated files. The archive is neither encrypted nor authenticated: SHA-256
detects accidental damage, not deliberate rewriting. Protect backups like the
journal, keep independent copies and periodically test recovery into a new root.
No automatic schedule, cloud sync or power-loss durability guarantee is provided.

## Local company watchlist

**Company watchlist** stores append-only ticker labels, company labels, optional
horizon and literal notes in the separate `watchlist.sqlite3` database. Reading
does not create storage; only **Save watchlist item** writes. Labels are
user-declared and do not establish verified company or security identity.

**Company view** groups saved local notes and existing original-journal entries
by exact ticker-label match only. Its SEC/journal buttons navigate and prefill
an unverified ticker; they do not fetch data or save a journal entry. The saved
list is a session snapshot until explicitly refreshed. There are no prices,
valuation claims, edit/delete controls or automatic research. Journal backups
do not include the separate watchlist database.

## Snapshot acceptance evidence

`edge.data.market_data.assess_acceptance(snapshot)` returns a frozen
`AcceptanceReport` with pass/unresolved/failed gates and per-use decisions.
It is deterministic, uses no wall clock or I/O, and does not promote snapshot
flags. An eligible private ephemeral display is not verified legal clearance,
endpoint entitlement, complete exchange-session coverage or accepted adjustment
semantics. Calculations, backtesting, storage, Nautilus and redistribution
remain ineligible; unresolved external evidence is explicitly retained.

## Desktop provider settings / historical research

**Provider settings** offers the implemented FMP adapter only. Plan names are
descriptive labels, not access grants. Explicit **Save non-secret settings** stores only
bounded non-secret settings in the sibling `settings/provider-v1.json`; missing
settings have visible first-run defaults and corrupt/unknown versions fail
closed. Credentials are never put in that file, browser fields, session state,
exports or logs. Native masked credential entry saves/removes only EDGE's fixed
FMP target in Windows Credential Manager after confirmation.

Only an explicit historical fetch after both private-use and endpoint-rights
declarations can read a stored key. Navigation and startup do not probe the vault
or call FMP. The defaults bound a snapshot to four requests, 366 calendar days,
10 seconds per request, 4 MiB per response, a 60-second cooldown and three
attempts per session. No automatic retry or fallback exists.

Accepted results are immutable, private local ephemeral historical snapshots,
not real-time prices or point-in-time verified observations. Exact Decimal
values, retrieval provenance, missing values and unresolved acceptance gates
remain visible. Draft/settings edits do not relabel a submitted result; failures
clear it. These market snapshots have no export, cache, storage or calculation
path. Account rights and live provider availability remain unverified here.

`.github/workflows/ci.yml` defines Windows x64 Python 3.13 acceptance: install
`requirements-windows-py313.lock.txt`, install EDGE editable with `--no-deps`,
run `pip check`, then the entire test suite. Tests run with
`EDGE_TEST_OFFLINE=1`: a Python-level socket guard blocks external DNS lookups,
TCP connects and addressed UDP/sendmsg sends in pytest and CLI subprocesses.
Loopback remains available for Windows/Streamlit internals. This is not an OS
sandbox: native libraries, external programs or code deliberately bypassing the
patches are not contained. Package installation
requires package-index access, but tests require no provider secrets or live data.
CI uses read-only repository permissions and no paid services or deployment steps.
The same offline acceptance can be run locally in the existing isolated env:

```powershell
$env:EDGE_TEST_OFFLINE = "1"
& .\.venv-nautilus\Scripts\python.exe -m pytest -q
Remove-Item Env:EDGE_TEST_OFFLINE
```

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

Results show validated ticker/CIK identity, plain-text SEC archive URLs for
selection/copying (EDGE never opens external pages), returned/provider/excluded
counts, submitted filters, response coverage and warnings, source,
provider/framework versions, UTC retrieval window and remaining provenance.
Unavailable dates/documents remain null in JSON and blank in the table.
The JSON download follows the CLI contract below (no dashboard envelope).
Results are tied to the submitted filters, never new draft edits; a failed attempt
clears previous success and its download. Results remain session-local with no
disk, database or cache persistence; another EDGE session starts empty.
This is metadata research only, not prices, financial statements, simulation
input, production acceptance or proof of a verified live SEC connection.

Install the extra in the isolated environment:

```powershell
& .\.venv-nautilus\Scripts\python.exe -m pip install -e '.[sec]'
```

Before an explicit SEC lookup, set `EDGE_SEC_USER_AGENT` in the process used to
launch the desktop executable, or in a developer's source/API process, to an
SEC-compliant identification string with a monitored contact email. Do not use
a placeholder contact. For example, PowerShell can prompt for it without adding
the value to a source file:

```powershell
$env:EDGE_SEC_USER_AGENT = Read-Host "SEC User-Agent: identifying name and monitored contact email"
```

For the desktop app, launch `dist\EDGE\EDGE.exe` from that configured process.
For a source-level developer check, launch/restart Streamlit from the configured
environment. Never enter this contact in the UI: it has no contact/credential input.
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

## FMP daily market-data research boundary

EDGE now contains an **opt-in, in-memory FMP daily-history adapter** for an
initial U.S.-listed USD equity/ETF scope. It is not a market-data UI, dataset-export CLI,
cache, local database, or accepted financial-calculation input. The user reported
one successful counts-only live check (details below); no raw response was
retained for independent inspection or calculation acceptance. The user must confirm private
personal-research rights and endpoint entitlement before any request; these are
explicit user declarations, not legal clearance or verified account access.
FMP's [terms/pricing notice](https://site.financialmodelingprep.com/developer/docs/pricing)
says display or redistribution requires a specific Data Display and Licensing
Agreement. The acceptance policy below records only the user's confirmed private
local research permission, not an independently verified display agreement.
Public/shared display, redistribution and retention are not authorized here.

Configure the API key privately in the process environment as
`EDGE_FMP_API_KEY`; do not place its value in source, shell history, URLs, logs,
or saved evidence. FMP's [API authorization documentation](https://site.financialmodelingprep.com/developer/docs)
documents the `apikey` request header, which this adapter uses instead of a
query-string key. No request occurs on import. An explicit call below makes four
bounded requests to the documented stable [profile](https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol),
[daily EOD history](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full),
[splits](https://site.financialmodelingprep.com/developer/docs/stable/splits-company),
and [dividends](https://site.financialmodelingprep.com/developer/docs/stable/dividends-company)
endpoints:

```python
from datetime import date

from edge.data.market_data import (
    DataUseAcknowledgement,
    MarketDataConfig,
    fetch_daily_snapshot,
)

snapshot = fetch_daily_snapshot(
    "AAPL",
    date(2024, 1, 2),
    date(2024, 1, 31),
    config=MarketDataConfig(
        provider="fmp",
        plan_label="starter",  # Description only; does not grant endpoint access.
        max_history_days=1_830,
        max_calls_per_snapshot=4,
        timeout_seconds=10,
        max_response_bytes=4 * 1024 * 1024,
    ),
    acknowledgement=DataUseAcknowledgement(
        personal_research_permitted=True,
        requested_endpoints_entitled=True,
    ),
)
```

Calling `fetch_daily_snapshot` explicitly contacts FMP; the sample has not been
run against a live service. The acknowledgement records the caller's
declaration only. The `plan_label` is descriptive and independent of request
limits; an FMP authorization/entitlement response remains authoritative. The
adapter makes no automatic retries, writes no files, and returns a typed
research-only snapshot or a sanitized error. Its initial limits bound requests
to at most 1,830 calendar days, four calls per snapshot, 30 seconds per call,
and 8 MiB per response (the shown settings are more conservative). Plan limits
and data rights are not inferred from these local ceilings.

The initial identity gate uses stable profile `exchange` (not legacy
`exchangeShortName` or descriptive `exchangeFullName`), a matching symbol,
company name, at least one reported CIK/ISIN/CUSIP, and explicit active/type
flags (`isActivelyTrading`, `isEtf`, `isFund`, `isAdr`). ADRs and non-ETF funds
are outside this slice. Nullable identifiers are not manufactured.
It accepts only profile-confirmed U.S. exchange listings
with explicit USD currency, and keeps share volume and per-share price units
explicit. It rejects missing/ambiguous currency, including `GBP`/`GBp`, rather
than guessing or converting. Bars are sorted and checked for duplicate dates,
date bounds, OHLC consistency, volume, and suspicious gaps/moves. The documented
stable full-history example is a flat array with `symbol`, `date`, OHLC,
`volume`, changes and VWAP; it does not require `adjClose`.
Absent/null optional VWAP or adjusted close stays unavailable, not copied from
close. JSON decimal volume is accepted only when finite, exactly integral and
within the share-count bounds; fractional volume is never rounded.
The explicit field-basis contract is conservative:

| Field | Accepted meaning/evidence | Still not established |
| --- | --- | --- |
| Equity `/full` `close` | Basis `unknown`; original validated value preserved. | Exact endpoint-specific split/dividend adjustment contract, anchor, factors, revisions and historical availability. |
| Whole OHLC, VWAP and volume | Basis remains `unknown`; original validated values are preserved. | An explicit, consistent adjustment contract across **all** price/volume fields. No inference from close alone. |
| Optional `/full` `adjClose` | Value preserved if supplied; basis `unknown`. Missing/null remains unavailable. | The stable `/full` example does not document this field. Do not import legacy endpoint semantics. |

Evidence reviewed on 2026-11-06: the official
[/full schema](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full),
[shared historical-price FAQ](https://site.financialmodelingprep.com/developer/docs/stable/index-historical-price-eod-light),
and [FMP historical-price guide](https://site.financialmodelingprep.com/how-to/fmp-historical-price-apis-from-light-charts-to-dividendadjusted-analysis).
Direct page fetching returned HTTP 403; review used publicly indexed official
excerpts, not authenticated API responses. The index-light/shared FAQ distinguishes
split-only `close` from split-and-dividend `adjClose`, but this is **indirect,
insufficient evidence for equity `/full`**. The earlier close-only
`split_adjusted_only` assertion has been withdrawn; no field is promoted on
that basis. Provenance records
`insufficient_endpoint_specific_adjustment_evidence`. The FAQ is not a universal mapping
of field names: the guide and
[non-split-adjusted schema](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-non-split-adjusted)
use **`adjOpen`, `adjHigh`, `adjLow`, `adjClose` for unadjusted OHLC**.
The separate
[dividend-adjusted endpoint](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-dividend-adjusted)
also uses `adj*` names and explicitly accounts for dividends. Neither alternative
endpoint is requested, enabled or aliased into this `/full` contract. These
sources do not justify promoting the entire bar to calculation-ready data.

Split and dividend records are fetched separately, but their history coverage is
not proven complete. Stable splits use positive numerator/denominator ratios.
Optional provider `splitType` is preserved, not interpreted as accepted accounting.
Stable dividends use `dividend` and optional `adjDividend`, not `amount`.
The [dividends documentation](https://site.financialmodelingprep.com/developer/docs/stable/dividends-company)
explicitly states “Currency is as Trading”; only the validated profile currency
supplies that context. No USD default or invented amount/date is used.
Optional record/payment/declaration dates may be null or empty and remain
unavailable. The adjustment method of `adjDividend` remains unknown; its
presence does not establish a reinvestment or cash-accounting model.
All returned action records, including out-of-window events, must pass identity,
date, amount/ratio and duplicate validation **before** local inclusive-window
filtering. Malformed old events cannot supply coverage evidence.
Action identity is keyed by validated **symbol, kind and event date**, not
amount, split ratio or optional metadata. With no unique event ID in the
supported schema, repeated same-kind/same-date records are rejected:
identical normalized events use `duplicate_action`; differing attributes use
`ambiguous_action`. This deliberately fails closed even when two same-date
distributions might be genuine: no guessing, selecting or combining is allowed.
Split and dividend events on the same date remain distinct kinds.

Dividend chronology compares only reported optional lifecycle dates:
declaration must not follow record or payment, and record must not follow
payment. Equal dates are accepted; missing dates remain missing. Reversals
produce `action_date_order` with `record_date` or `payment_date`.
No event/ex-date ordering against payment or record is imposed: special
dividend ex dates can follow payment. This is a sanity check, not certification
of announcement time or accepted corporate-action accounting.
Both action requests use `limit=1000`. The
[split](https://site.financialmodelingprep.com/developer/docs/stable/splits-company)
and dividend company documentation lists only `symbol` and `limit`, maximum
1,000 records, and no paging, date filters, exhaustion marker or completeness
guarantee. Calendar endpoints have different parameters and are outside this scope.
No undocumented paging/date parameters, extra calls or retries are added.

`snapshot.action_evidence` separately records each kind's full returned count,
retained in-window count, minimum/maximum observed dates, limit reached, whether
observed dates bracket the window, and in-window event dates without matching bars.
It handles unsorted lists and future/out-of-window events without treating them
as historical in-window actions. Bracketing is **not** completeness; empty is
**not** proof of no actions; below-limit is **not** proof of exhaustion.
Completeness stays `unverified`, with an exact outstanding reason:
`empty_response_no_completeness_metadata`,
`response_limit_reached_no_documented_paging`, or
`no_documented_completeness_or_exhaustion_metadata`.
Missing-bar event dates are unresolved, not inferred holidays.
No action-based price rewrite, split factor, dividend reinvestment or double
adjustment is performed.

A session date is not a publication timestamp; retrieval
times are recorded and publication/as-of time remains unavailable.
`snapshot.session_evidence` formalizes first/last observed dates, leading/trailing
**calendar-day** distances, calendar-day retrieval lag and possible internal
weekday gaps. Those are observations, not missing-session counts or freshness
certification. Boundary matches and Friday-to-Monday continuity do not prove
coverage. Without an exchange calendar, no holidays, latest completed exchange
session or publication deadline are inferred. Freshness remains unknown; EOD
bars must precede the retrieval UTC date. Point-in-time availability remains
unverified without publication times and revision history. Declaration **dates**
do not establish when an action became knowable to a backtest.

### Acceptance policy: private display is not calculation acceptance

Only the acknowledged `fetch_daily_snapshot` path, after strict snapshot
validation, sets `snapshot.use_policy.research_display_eligible=True`.
This permits only user-declared **private, local, ephemeral research display**
with source, request/retrieval window, historical/non-real-time labelling and
**all** limitations visible. It does not assert current data, point-in-time
verification, verified entitlement or a redistribution license.
Direct `normalize_snapshot` alone never records user permission and leaves
display eligibility false. Malformed snapshots are rejected, not downgraded
into displayable records.

`calculation_eligible`, `backtest_eligible`, `storage_allowed`, `nautilus_eligible`
and `redistribution_allowed` stay **false**, even for a display-eligible snapshot.
The policy exposes precise calculation blockers: whole-bar/volume/VWAP basis,
action completeness and accounting, expected sessions/freshness, publication and
revision history, and independently accepted calculation rights. Existing
top-level calculation/storage flags remain false. Nothing automatically saves
data, connects it to Nautilus or changes the application UI.

The adapter uses FMP stable REST directly rather than OpenBB: the current
[`openbb-fmp` release](https://pypi.org/project/openbb-fmp/) requires
`openbb-core<2`, incompatible with EDGE's OpenBB v5 `openbb-core==2.0.1`.
Changing to another provider requires another implementation of the small
`MarketDataProvider` protocol; only `fmp` is currently supported. This is not
an FMP market-data license or acceptance of any provider response.

### User-run, counts-only FMP check (PowerShell)

From the repository root in **your own local terminal**, run the block below.
Do not send the key in chat. Agent shells do not inherit environment changes
made inside your terminal. No installation, upgrade or saved secret is needed.
Only `EDGE_FMP_API_KEY` supplies the runtime credential.

Paste the block **unchanged** first. Wait until `Read-Host` displays its hidden
input prompt, then enter only the key there. The quoted text after `Read-Host`
is a prompt label, not a place to substitute the key; never paste the key at
the normal `PS ...>` command prompt. Do not print or inspect the environment
value to troubleshoot. If a key has been shared in chat or entered as a command,
revoke/rotate it with FMP before retrying; clearing the terminal does not revoke it.

The two flags record your declarations that private research is permitted and
the requested endpoints are accessible; **they do not verify entitlement**.
The check is deliberately restricted to MSFT, the last 14 completed UTC calendar
days (excluding today), and at most four HTTP attempts total, independent of plan.
There are no retries or redirects. Profile plus daily bars, splits and dividends
are requested; action endpoints return bounded lists, then events are
filtered to the window. Their historical completeness is not proven.

```powershell
$secureKey = $null
$bstr = [IntPtr]::Zero
try {
    $secureKey = Read-Host 'FMP API key (hidden; never paste into chat)' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $env:EDGE_FMP_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    & .\.venv-nautilus\Scripts\python.exe -B -m edge.data.fmp_check --symbol MSFT --confirm-private-research --confirm-endpoint-access
}
finally {
    Remove-Item Env:EDGE_FMP_API_KEY -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    if ($null -ne $secureKey) {
        $secureKey.Dispose()
    }
}
```

The key is never entered as a command, written to `.env`/secret files, or set in
registry/persistent environment settings. The temporary process environment
replaces any existing value and is removed, not restored, in `finally`.
**This is not memory-proof:** HTTP requires temporary plaintext; PowerShell,
Python, environment and HTTP buffers may retain copies that cannot be reliably
zeroed by this script. BSTR cleanup does not erase those copies. Use a trusted
terminal and host; `finally` cannot run after a forced process/host termination.

Output is only sanitized status, identity-validation status, row/action counts,
request/retrieval windows, observed date coverage, fixed warning categories and
actual HTTP-200 endpoint paths (not URLs). It also separates private-display
eligibility from calculation/backtest/storage/redistribution/Nautilus eligibility,
labels the partial documented basis and unresolved freshness/point-in-time gates,
and reports returned/in-window/unmatched-bar action counts by kind.
No financial records, JSON dataset,
cache or output files are emitted; `-B` also suppresses Python bytecode writes.
HTTP 200 is not proof of valid data, ongoing access or licensing. Partial
coverage is flagged without inferring an exchange calendar or fabricating rows.
Nonzero exits are typed: configuration/input 2, access 3, rate limit 4, not found
5, data validation 6, provider/transport 7, unexpected 8, interruption 130.
Configuration failures also emit fixed, allowlisted `configuration_reason` and
`recovery` codes, never exception messages or raw configuration values:

| `configuration_reason` | Safe corrective action |
| --- | --- |
| `missing_key` | The runtime variable is absent or empty. Rerun the whole block and enter the key at the hidden prompt in the same terminal. |
| `invalid_key_format` | Reenter only the key at the hidden prompt: no quotes, assignment, command, URL or surrounding whitespace. Local validation permits 1–512 ASCII letters/digits or `._~-`; this is not account authentication. |
| `missing_confirmations` | Use both documented confirmation flags only if you can truthfully make both declarations. |
| `invalid_arguments` | Run the documented command unchanged; only MSFT and the two confirmation flags are supported. |
| `credential_in_plan_label` | Keep descriptive labels non-secret; the runtime key must not occur in the plan label. |
| `invalid_configuration` | Other local contract checks failed (limits, clock, adapter contract or disclosure guard). Review local code/configuration; share only the sanitized summary, not values or exception text. |

These are local failures, not FMP rejections. With `requests_attempted=0` and
`endpoints_http_200=none`, no endpoint was attempted by the check; account access
has not been tested. The older generic `configuration_error` summary alone
cannot distinguish missing/invalid input, confirmations or another local check.
The documented defaults are covered by offline tests with synthetic credentials;
no live account validation is implied. Do not add a key argument or remove a
gate to bypass a configuration failure.

For data-validation/not-found failures, the checker now emits only allowlisted
`validation_stage`, `validation_reason`, `validation_field` and `recovery` codes.
No provider values, error strings, keys, URLs or response bodies are emitted.
`profile`, `history`, `splits`, and `dividends` identify the four documented
endpoints; `snapshot` and `provenance` identify local contract rules.
`identity_validation=passed` means the profile gate actually completed before
the failure, **not** that the failed snapshot is accepted. A later endpoint's
JSON decoding failure may still say `not_established`: profile normalization
has not run yet. HTTP 200 alone never establishes identity.

| Validation rules (`validation_reason`) | Safe next step |
| --- | --- |
| `json_invalid`, `response_shape`, `record_shape`, `collection_shape`, `payload_type`, `profile_count` | Review the named endpoint's public stable array/object schema and adapter offline. JSON constants, duplicate keys and legacy history envelopes are rejected. |
| `text_invalid`, `symbol_mismatch`, `name_length`, `listing_unsupported`, `currency_unsupported`, `inactive_security`, `type_flags_invalid`, `security_type_unsupported`, `identifier_invalid`, `identifiers_missing` | Review the named profile/record field rule offline. Never supply default identity, type or currency, or weaken eligibility gates. |
| `date_invalid`, `date_outside_window`, `duplicate_bar`, `history_empty`, `number_invalid`, `number_range`, `volume_invalid`, `ohlc_inconsistent`, `vwap_range` | Review history/action field rules and the requested window offline; do not round volume, replace missing prices/dates, or treat stale/out-of-window bars as valid. |
| `split_ratio_invalid`, `duplicate_action`, `ambiguous_action`, `record_limit` | Review action ratios, duplicate/unresolved same-symbol-kind-date events or documented limits offline. No combining, paging/retry or claim of complete action coverage is made. |
| `action_date_order` | Reported dividend lifecycle dates are reversed (`record_date` or `payment_date` identifies the later-date constraint). Missing dates are never filled. Reject the snapshot, including private display; review offline without logging provider values. |
| `timestamp_invalid`, `timestamp_order`, `api_version_missing`, `endpoint_mismatch`, `invalid_record` | Review the local adapter/provenance contract offline. Unknown diagnostic attributes reduce to static generic codes, never raw text. |

An all-200 `data_validation_failed` summary from the old checker cannot identify
the actual failed rule. Offline reproduction found a definite legacy-field bug:
stable `exchange` profiles were rejected for missing `exchangeShortName`.
Synthetic fixtures now exercise the stable schema and exact integral JSON
decimal volume. This does **not** establish the contents of any live response.
The user subsequently reported one successful MSFT check: requested window
2026-10-23..2026-11-05, ten validated bars, zero in-window actions, all four
endpoints HTTP 200, and calculation/storage/Nautilus gates false. This is a
user-reported sanitized result, **not** a retained raw response or proof of
complete action history, price adjustment factors, publication times or licensing.
No further live check is needed for this offline acceptance increment. A later
failure should stop rather than trigger retries; share only the sanitized
stage/reason/field summary. Never send raw records, inspect/print the key or
environment, or add diagnostic payload logging.

Check `$LASTEXITCODE` after the block. Storage, calculations and Nautilus remain
false even on success. Private-display eligibility in a future checker run
requires the two declarations and successful validation, not HTTP success alone.
Share only the sanitized summary; no calculation acceptance is claimed.

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

**Real market histories remain blocked for calculations and Nautilus.** The
FMP adapter above is only an explicitly invoked, in-memory research boundary;
no FMP account entitlement, data rights, or live response has been verified.
General Nautilus corporate-action accounting must not be assumed complete.
Before persisting/displaying data or passing real histories to calculations or
Nautilus, confirm provider rights, identity, currency/units, availability times,
adjustment basis and complete action coverage. Reject affected or unknown
histories until positions, working orders, cash and reports have supported,
tested treatment.

## Approved roadmap (ranked; desktop delivery is fixed)

All work below remains inside the standalone local Windows application. The
loopback Streamlit server and embedded web controls are implementation details,
not a hosted product or a planned browser-launch fallback.

**Completed foundation:** the Nautilus synthetic acceptance engine, reproducible
evidence, the local desktop runtime, the opt-in SEC metadata boundary, and the
local user-authored swing/long-term journal.

**Next, in order:**

1. Complete explicit data acceptance before using real observations in
   calculations: rights, identity, timestamps, currency/units, freshness and
   corporate actions. The SEC adapter is not live-verified; the FMP boundary is
   not live-verified, licensed, persisted, calculated or accepted by Nautilus.
2. Add Qlib research/signals in an isolated Python 3.12 worker; it is not a
   second simulator.
3. Build EDGE discovery, fuller separate swing/long-term research workflows and
   manual holdings analysis.
4. Evaluate skfolio target allocations, then selectively reuse Vibe-Trading
   research workflows.
5. Evaluate Kronos only as an isolated, independently tested experiment, never
   as financial truth.

`openbb-core==2.0.1` and `openbb-sec==2.0.0` are included in the optional `sec`
extra and the accepted Windows lock; the explicit metadata lookup is available
in the desktop app's SEC workspace. Qlib,
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

## Standalone Windows desktop application

The desktop target is **`dist\EDGE\EDGE.exe`**, a native pywebview/WebView2
window with a bundled CPython 3.13 runtime and Streamlit server. It is the only
end-user launch path and does not invoke `.venv`, Python on PATH, or
`python -m streamlit`. Keep **the entire `dist\EDGE` folder** together; copying
only `EDGE.exe` will not work. The native Tcl/Tk amber/black splash reports
actual startup stages, not financial readiness or fake progress. Skip/Escape
freezes motion; Windows reduced-motion settings and `--reduced-motion` are
respected. Cancel stops startup. A ready server is never delayed for animation.

Prerequisite: Windows x64 with **Microsoft Edge WebView2 Evergreen Runtime
100+** and the normal Windows .NET Framework runtime. EDGE checks for WebView2
and gives an actionable error if absent; it does not install/download runtimes,
request administrator rights, disable certificate validation, or change system
settings. Obtain WebView2 only from Microsoft's official
[WebView2 download page](https://developer.microsoft.com/microsoft-edge/webview2/).
The local executable is **unsigned**: SmartScreen may warn because it has no
signing reputation. No signing bypass is provided; paid code signing was not
requested.

### Storage and lifecycle

- Packaged research data defaults to **`%LOCALAPPDATA%\EDGE\data`**; the
  source dashboard keeps its existing repository `data` default for developer
  checks only. There is no browser path picker or browser-supplied override.
- Existing repository/user databases are **never copied, migrated, reset or
  deleted** by startup. The distribution contains no research data, `.env`,
  credentials, source checkout, raw Python source, Git tree or virtualenv.
  Streamlit's two-line runner is generated only in the disposable child runtime
  directory; the dashboard is compiled in the Python archive.
- Each launch creates its own OS-assigned `127.0.0.1` server port and private
  startup handshake; it never attaches to an existing server, including port
  8501. Multiple windows have separate servers/browser profiles, but the same
  per-user research store (normal SQLite contention is reported, not hidden).
- A kill-on-close Windows Job Object owns only that launch's server. Closing
  the window or crashing its owner terminates that server, not unrelated
  processes. Startup is bounded at 75 seconds.
- Temporary private browser state lives under
  `%LOCALAPPDATA%\EDGE\runtime\session-*`, separately from research data.
  Normal close removes only that session directory. A hard crash may leave
  an abandoned runtime cache; never delete the adjacent `data` directory.
- Streamlit telemetry/file watching and browser background-collection switches
  are disabled. Native resource/navigation requests and download sources are
  restricted to the owned local server. Exports open the native Save As dialog
  with overwrite confirmation; remote downloads are cancelled. SEC URLs are
  plain text for selection/copying and are never opened by EDGE. This is not an
  OS-wide network sandbox. Explicit SEC research still requires
  deliberate action and a valid process-environment User-Agent; startup never
  calls providers or persists API keys. Windows/WebView2 servicing is managed
  independently by the operating system.
- All financial acceptance gates remain unchanged: synthetic engine fixtures,
  user-authored journal, opt-in delayed SEC metadata, and blocked real-history
  calculations/live trading. The animation does not imply financial readiness.

### Rebuild (isolated, opt-in)

Do not install desktop tooling into the original `.venv`. Using the accepted
Windows x64 Python 3.13 interpreter:

```powershell
& .\.venv-nautilus\Scripts\python.exe -m venv .venv-desktop
& .\.venv-desktop\Scripts\python.exe -m pip install -r requirements-desktop-windows-py313.lock.txt
& .\.venv-desktop\Scripts\python.exe -m pip install --no-deps -e '.[desktop,simulation,sec]'
$env:EDGE_TEST_OFFLINE = '1'
& .\.venv-desktop\Scripts\python.exe -m pytest -q
Remove-Item Env:\EDGE_TEST_OFFLINE
& .\.venv-desktop\Scripts\python.exe scripts\build_desktop.py
```

The `desktop` extra pins pywebview 6.2.1, PyInstaller 6.22.3 and Streamlit
1.66.1; the desktop lock also fixes accepted Python.NET/CLR-loader/build
dependencies. The original accepted financial runtime lock is unchanged.
`EDGE.spec` explicitly collects Streamlit assets, Nautilus native modules,
OpenBB SEC optional dependencies and package metadata. It never includes the
repository wholesale. LGPL/native dependency notices and a conservative
build dependency inventory are retained in `_internal\licenses`. This is a
local build, not a licence-cleared external release; review corresponding-source,
replacement/relinking and all native redistribution obligations before sharing.

Offline packaged diagnostic (writes only a report plus disposable temporary
synthetic/journal test storage, including a JSON export round-trip; never the
existing user database):

```powershell
$p = Start-Process .\dist\EDGE\EDGE.exe -Wait -PassThru `
    -ArgumentList '--desktop-self-test', 'C:\Temp\edge-desktop-report.json'
Get-Content C:\Temp\edge-desktop-report.json
$p.ExitCode
```

Run from any working directory; Python and source environments are not required.

### Earlier local build verification (2026-11-06)

- Full offline suite in `.venv-desktop`: **831 passed, 1 skipped** (Windows
  symlink privilege unavailable); focused desktop lint and `pip check` pass.
- The frozen executable passed engine doctor, real native synthetic simulation,
  SEC fetcher imports without lookup, and temporary journal reopen/persistence.
- Actual native WebView2 windows rendered the dashboard from an unrelated
  working directory with Python/virtualenv paths removed from PATH and disposable
  user-profile directories. Two simultaneous launches used distinct servers;
  both closed and cleaned only their own servers/browser state.
- Force-terminating a test native parent killed its owned server through the
  Job Object; the existing unrelated port-8501 Streamlit process remained alive.
  Startup created no research data or copied database. Original `.venv` is
  still pip-only.
- This verifies the current Windows 11 machine, not a clean-VM installation,
  every Windows/WebView2 version, an OS-level network sandbox, or an external
  redistribution/signing review. No live FMP/SEC acceptance was performed.

### Desktop-only cleanup verification (2026-11-07)

- The full offline suite passes: **843 passed, 1 skipped** (Windows symlink
  privilege unavailable); Ruff and `pip check` pass.
- The rebuilt frozen executable's offline self-test passed the synthetic engine
  run, optional SEC module imports without lookup, temporary journal persistence,
  and temporary journal JSON export. It reported no external requests.
- The rebuilt executable opened a real native WebView2 window; its smoke report
  confirmed the local health check and rendered dashboard. The check used a
  disposable `LOCALAPPDATA` profile, which was removed afterward.
- The pinned pywebview download handler was checked locally: accepted downloads
  use its native Save As dialog, whose `OverwritePrompt` is enabled by default.
  Unit tests confirm only the owned loopback origin can reach that dialog;
  external navigation and remote download sources remain blocked.
- No live SEC/FMP request or credential was used. This is not a clean-VM,
  signature, or external redistribution review.

### Integrated desktop acceptance (2026-11-07)

The stable-source offline suite passed 1,076 tests with one Windows symlink
permission skip and one upstream OpenBB deprecation warning. Scoped Ruff and
`pip check` passed; repo-wide Ruff still reports 44 findings outside that
acceptance scope.

The rebuilt native window rendered all six workspaces without creating a data
directory in the disposable profile. Frozen diagnostics also passed temporary
journal persistence/export and real masked Tk Entry/dummy save/cancel checks.
The public spawned credential-save popup was cancelled with an owned-child
HWND `WM_CLOSE`, as were native backup Save As, backup Open and restore-folder
dialogs. Each returned cancellation, reaped its child and left disposable-profile
files unchanged. No user clipboard or real Windows vault key was read or saved.
Actual clipboard paste, real vault persistence and manual native confirmations
remain manual acceptance items; dummy/injected checks do not certify them.

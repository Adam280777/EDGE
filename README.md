# EDGE

Equity Discovery & Growth Engine is a local equity-research and decision-support
application. It is not an autonomous trading bot.

## Current source handoff — 7 November 2026

EDGE is for **one user's private personal investment research**, not public
customers, multiuser display or commercial redistribution. The source additions
below are implemented and tested in bounded scopes; the existing executable
must not be assumed to contain them until rebuilt and independently checked.
**This is not the final production-ready release.**

- Manual portfolio: explicit opening holdings, FIFO subsequent transactions,
  fees/corrections and separate GBP/USD cash; GBP reporting. US/USD and UK/GBP
  identities are user declarations. GBp is an explicit price unit, never cash.
  Missing/stale price or FX inputs yield incomplete/blocked valuations.
- Research/decisions: immutable swing/long-term research and plan revisions;
  deterministic scenarios and transparent manual-fundamental screening.
  Identity, source dates and claimed-reported figures remain user declarations,
  not independently verified vendor facts.
- Risk is disabled until explicitly configured and confirmed. Plans are
  long-only, whole-share, cost-aware estimates—not orders or stop-fill guarantees.
  No default risk percentage, silent FX cash exchange or automatic execution.
- Manual CSV observations are explicit, bounded, all-or-nothing and session-only.
  A local file does not establish vendor rights or calculation acceptance.
- Monitoring is opt-in only while EDGE is open and the Monitoring / Alerts
  task is active. Leaving it revokes opt-in; returning or starting a new session
  stays off. Checks append explainable local alerts, never alter rules/holdings.
- Eleven grouped workspaces retain stable underlying navigation values:
  Engine acceptance, SEC filing research, Research journal, Company watchlist,
  Research / decisions, Discovery, Manual portfolio, Monitoring, Backup and
  recovery, Application recovery and Provider settings.

### User-record bundle recovery

The existing journal archive remains **journal-only** and unchanged. The new
application bundle uses `edge.application_backup.backup_application(app_root,
archive_path)` and `restore_application(archive_path, absent_app_root)`.
The layout is `app_root/data` plus `app_root/settings`. Restore refuses even an
existing empty root, never merges/overwrites active stores and never implicitly
switches active storage. Native recovery chooses a parent and a new child name.

The default registry covers `research-journal.sqlite3`, `watchlist.sqlite3`,
`portfolio.sqlite3`, `decisions.sqlite3`, `manual-monitoring.sqlite3` and
non-secret `settings/provider-v1.json`. Credentials, vendor state, simulation
evidence and runtime files are excluded. Missing registered stores are explicit;
unknown SQLite stores fail closed. Bounds, hashes, schema/record/internal-link
validation and online SQLite snapshots precede no-clobber publication.

`cross_store_consistency_verified=true` means **only a cooperative locked cut
of the default registered user stores plus settings**. All registered writers
use `storage_write_lock(app_root)` through checks, commit and publication.
It does not suspend external SQL/file edits or establish authenticity, market
identity, provider rights, freshness or financial/production acceptance.
Custom store registrations remain uncertified. There are no cross-store IDs;
matching ticker labels are declarations, not referential links.

### Evidence and remaining acceptance

Independent targeted financial/recovery checks: **250 passed, 1 Windows symlink
privilege skip**. Separate source UI/desktop/import checks: **53 passed**.
The UI owner reports **72 passed** across its focused workflow tests. Independent
stable-source rerun passed **93 tests** (those 72 plus 21 financial acceptance
tests). These overlap the other counts. Changed-Python Ruff passed;
repository-wide Ruff still has 44 findings. These are overlapping targeted
runs, not a new full-suite total. The earlier main CI baseline was 1,077 passed,
1 skipped.

The UI owner reports 22 exact-window native source captures across all eleven
workspaces at 1440×960 and 960×640, without detected overflow; keyboard focus and
error states were inspected. **Actual native selected-file CSV and bundle
save/open/restore flows remain unverified**: the automation could not observe
the owned file dialog. This is an evidence gap, not proof the dialog is broken.
Controlled-path AppTests use real parsing/storage/recovery but do not certify
native file selection. No final frozen executable acceptance is claimed.
Independent QA also inspected the 22 saved captures and visually reviewed all
five new minimum-window workspaces. Correct markers/warnings and unchecked
monitoring were visible without obvious horizontal clipping in captured regions.
Content below the viewport, complete accessibility and actual native selection
are not certified by screenshots. Overflow/CDP/focus checks remain UI-owner
evidence. A full-suite/build run is assigned separately; no result is yet claimed.

### Research environment proposals — not installed

`requirements/research-*.in`, proposed hashed locks/reports/budgets and
`scripts/research/estimate_budget.py` describe isolated Python 3.12 candidates.
Selected environment/model/source downloads total **628,895,093 bytes (~629 MB)**,
excluding Python, datasets, cache and runtime outputs. Installed-size estimates
are heuristic; an 8 GB free-space planning reserve is not a guarantee.
At the estimate handoff only Python 3.13 was verified locally. Actual 3.12 marker
re-resolution, Qlib source builds, imports and model execution were pending.
No research environment installation or model download had then occurred; one earlier 25 kB
colorama wheel was fetched for missing metadata-sidecar inspection.
Subsequent user authorization permits approximately 629 MB of isolated research
downloads/installations and an official per-user Python 3.12 if needed, with a
**hard 8 GB additional environment/runtime/model/temp limit**, no changes to
existing environments or PATH. Provisioning is now assigned; completion,
measured size, import/model execution and acceptance have not yet been reported.
Provisioning update: the official per-user Python **3.12.10** installer was
verified against its published Sigstore digest and valid PSF Authenticode
signature, then installed without PATH, launcher, association or existing-env
changes. Three new isolated environments were created. This Windows installer
predates the later source-only 3.12.16 security release; its security-age
limitation remains disclosed, not production-certified. The initial target
skfolio dry-run fetched approximately 84 MB of temporary payload, then deleted
it; repeated network transfer therefore exceeds the headline download estimate.
The hard additional disk-use limit remains 8 GB. Package/model acceptance is
still pending; subsequent steps use a bounded local wheelhouse.
An initial full-suite invocation reported **1,446 passed, 2 skipped, 1 failed**.
Independent investigation found inherited `PYTHONPATH=src` made the preserved
original environment discover the checkout's editable package metadata. The
failing preservation test passed unchanged with that variable cleared; a clean
full-suite rerun is pending. This is not a full-suite-green result and does not
justify weakening the environment-preservation check.
The build owner subsequently reported a clean invocation with **1,510 passed,
2 Windows symlink-privilege skips and one OpenBB deprecation warning**, with
`PYTHONPATH` unset and the preservation assertion unchanged. Research tests were
added during the run, so this is attributed owner-run evidence, not an immutable
research/frozen certification. The eleven-route manual RC build is underway;
isolated research is not integrated into that RC.
Independent native-picker report review found both bounded selection attempts
unsuccessful: an owned dialog was observed and a path submitted, but the expected
path was not returned and UI application was not verified. Owned children exited.
Passing backend fixture checks are not native selection success. Actual native
CSV selection-to-apply and bundle save/open/restore-parent acceptance remain
blocked; no further workaround or relaunch is implied by these results.

Qlib remains research-only with Nautilus as sole simulation authority; skfolio
proposes targets, not holdings. Kronos remains experimental; selective Vibe
workflow reuse is still unimplemented. The user requires all intended research
and market-data integrations to be production-ready before finalization.
Manual functionality does not satisfy that full-release criterion.

### Current FMP evidence — private use, distinct accuracy gates

Official rendered terms, pricing and stable API documentation were reviewed
7 November 2026. [Terms](https://site.financialmodelingprep.com/developer/docs/terms-of-service)
(labelled updated 1 August 2023), §§1.1, 2.1 and 2.2.1 expressly support individual
personal API use. Commercial/multiuser clearance is not a prerequisite here;
public redistribution is an excluded non-goal.

Local caching/retention and persistent derived research remain **unresolved,
not categorically forbidden**: broad copy/download/derivative wording conflicts
with intended API functionality, retained-report language and termination/cache
deletion provisions (§§2.2, 5, 6.2–6.3). The linked ADUP returned an official 404.
Clarification is needed for this precise personal API use—not a blanket lawyer
or commercial-licence requirement. No financial/provider policy flags were
changed by this documentation handoff.
Subsequent narrow source correction now labels FMP EOD/full `close` as
`split_adjusted`, present optional `adjClose` as `split_and_dividend_adjusted`,
and missing `adjClose` as unavailable. Evidence is explicitly
`documentary_close_fields_only_not_provider_verified`; values are unchanged
and no adjustment is performed. The owner reports 183 targeted tests and scoped
Ruff passing. Whole OHLC/volume/VWAP and all existing calculation/storage/action/
calendar/PIT/Nautilus gates remain unchanged. This correction is not in the
historical CC93 executable and requires the next build/hash.

[Stable EOD/full documentation](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full)
explicitly describes `close` as split-adjusted and `adjClose` as split/dividend
adjusted when present. Missing `adjClose` is never fabricated. Whole OHLC,
volume/VWAP bases, complete actions/accounting, calendar sessions, historical
publication/revisions and UK coverage remain separately unaccepted. Starter
pricing advertises US/up-to-five-year/annual-fundamental coverage; charts/actions/
FX have limited coverage, not blanket endpoint or UK entitlement. Applicable
[personal exchange declarations](https://site.financialmodelingprep.com/how-to/what-is-the-user-declaration-form-and-why-do-you-need-to-fill-it-in)
are separate from plan names and successful HTTP responses.

The prior checkpoint records a user-reported successful MSFT FMP sample (ten
bars, four HTTP-200 endpoints, zero returned actions), not comprehensive source
acceptance. The current FMP implementation remains private ephemeral display
only. Older milestone statements below describe the acceptance at that time;
this section records newer evidence without silently unlocking calculations,
storage, backtesting, Nautilus or redistribution.

### Independently checked manual eleven-workspace executable

The manual RC at `dist/EDGE/EDGE.exe`, SHA-256
`9e339fc076f2f4b6e81069ab221908739ab04d1e8c2a935d13d613ae98617769`,
was independently checked approximately 15:56–15:57 UTC on 7 November 2026,
outside the repository with disposable profiles, Windows-only PATH and no
PYTHONPATH/PYTHONHOME/VIRTUAL_ENV. Self-test and native smoke exited zero:
synthetic financial/manual CSV/risk/monitoring/bundle checks and all eleven
workspaces passed their diagnostic scope; owned parent/server processes exited.

The first cancellation run correctly cancelled/reaped all eight native-dialog
cases but exited one because the broad profile-invariance check detected Windows
Explorer icon/thumbnail caches. A second run with reports separate from the
synthetic profiles exited zero, all eight cancellation checks and report-parent
invariance passed; OS cache effects were retained separately in the evidence.
No real vault or clipboard was accessed. Neither run proves native selected-file
CSV application or bundle save/open/restore selection.

Later source adds a twelfth **Research lab** route and first-party editable
research-template guidance. The latter is concept-only Vibe inspiration, not an
installed Vibe runtime/source integration or external LLM service. These changes
are **not in this manual RC hash**. Research-runtime/wrapper/resource integration
needs a new build/hash and independent frozen acceptance before finalization.
The later research UI source handoff reports **71 focused tests passed**, with
scoped Ruff passing, and 24 native source captures of all twelve workspaces at
the two window sizes. These captures show initial lab controls, not actual
running/completed experiments. Mock-worker tests cover explicit execution,
blocked/malformed responses, cancellation, navigation and duplicate-run limits;
they do not prove canonical-runtime or packaged execution.

Research lab uses fixed synthetic fixture IDs and a bounded 120-second worker
call after explicit readiness checks. Rendering never starts a worker. Session
cancellation is requested on navigation; browser-tab closure is not guaranteed
to cancel immediately, while frozen window-close acceptance remains pending.
Runtime/hash evidence is labelled consistency evidence, not signed authenticity.
First-party template questions and completeness checks are editable guidance
and presence/date checks, not a research score, save gate or Vibe/LLM runtime.

### Canonical research-runtime handoff — 8 November 2026

The provisioning owner reports actual canonical synthetic execution through the
pre-execution Windows Job transport: Qlib `qlib-linear-three-assets-v1`
31.51 seconds, skfolio `minimum-variance-three-assets` 10.54 seconds and Kronos
`kronos-mini-ohlcv-v1` 8.39 seconds. These are synthetic checks, not market
performance or independently verified frozen UI acceptance.

Fixed environments/models are under `%LOCALAPPDATA%/EDGE/research`; the fixed
base is `%LOCALAPPDATA%/Programs/EDGE-Research-Python312` (3.12.10). Reported pins
include pyqlib 0.9.7, skfolio 1.12.5 and CPU torch 2.7.1+cpu; all three `pip check`
runs passed. Owner-created staging environments were removed after canonical
verification; original environments were preserved. This is a local desktop
with installed research runtimes, not a portable all-in-one research bundle.

Evidence: `scripts/research/canonical-final-smoke-results.json`, SHA-256
`dbd3be601375b424014bbffe1ed2ea8db186cc2fae757b9aafc2bf09e871f89f`;
fixed `attestation-v1.json`, SHA-256
`2d194edcd07fa751ae50500595c9bfdc84ae08dfdf36589edaae643473515a6a`.
`scripts/research/runtime-inventory.json` records execution-resource/model hashes,
versions and measured sizes. This attestation checks local consistency, not
signed authenticity or a security boundary.

Final sampled owned logical size was **3,476,887,603 bytes**, below the hard
8,000,000,000-byte cap; the largest reported earlier sample was 4,997,104,741.
These samples are not continuous peak-allocation proof. Accounted payload
transfer was 656,809,404 bytes, excluding metadata/HTTP/public-page traffic—not
total wire usage. Focused owner tests: 26 passed; scoped Ruff passed.

The original canonical Kronos timeout remains recorded, with measured wall time
1,927.49 seconds; a bounded retry and final rerun succeeded. Its cause and
reliable cold-start bound remain **unverified**. Python 3.12.10's age relative to
later source-only security patches remains disclosed; no security certification
is claimed. A new twelve-route frozen build, independent actual UI execution,
resource/runtime refusal checks and cancellation/window-close acceptance are
still required. Native selected-file/recovery and financial rights, adjustments,
actions, calendars, PIT and UK coverage remain open. No market gates changed.
Retained-code classification: that wall interval measures the entire
`run_synthetic` call, including repeated synchronous attestation hashes before
Job launch and later cleanup; the initial separate readiness check was excluded.
The helper's 120-second deadline starts only after launch/assignment/reader
startup. No phase timestamps were retained, so the overrun cannot be attributed
to hashing, worker execution, scheduling or cleanup. The implementation does
**not establish an end-to-end 120-second deadline or prelaunch cancellation**.
Timeout/responsiveness acceptance stays blocked; warm successes and a timeout
status alone do not prove timely termination. Future scoped instrumentation and
independent frozen cold/cancel/timeout checks are required.

### Patched twelve-workspace candidate — 8 November 2026

The newer `dist/EDGE/EDGE.exe` has independently matched SHA-256
`cc93a36cc44c312e46c75a00076a063aefe8a80c5f2382610b83c8e6fa286008`.
It supersedes the manual eleven-workspace artifact above. The owner reports a
stable source suite of **1,563 passed, 2 Windows symlink-privilege skips**, plus
frozen self-test and twelve-workspace native smoke passing with owned processes
reaped and disposable data absent. These owner results are distinct from
independent actual research UI acceptance.

The canonical research base is now **Python 3.12.16**, replacing 3.12.10; the
earlier installer-age limitation describes the historical runtime, not this
candidate. Independent QA matched the new base/DLL, three environment Python/
configuration files and all package METADATA hashes (194 Qlib, 20 skfolio,
35 Kronos). Local consistency does not establish complete security certification.
Refreshed attestation SHA-256:
`7e01280fe6717c79cb1512ca4ffbd85b57fd97fd25bf9040eab18c6602d054b7`.
The v2 canonical smoke report SHA-256 is
`f57e8d6cac7516e9bd4dee9eaceec744bc646acc393e89e2a07e2fb56a42e17c`;
owner source runs passed in 110.202/84.544/55.693 seconds respectively.

One independent actual **frozen headless** `--research-verify` run, outside the
repository with Windows-only PATH, no secrets/PYTHONPATH and isolated noncanonical
profile/temp directories, passed all three real synthetic fixtures through the
Job transport: Qlib 21.619 seconds, skfolio 5.081 and Kronos 10.992. Runtime,
request hashes and input fixture/panel digests matched. Expired deadline and
pre-set cancellation returned with no stdout in 0.0011/0.0005 seconds. A
0.5-second cancellation returned in 0.5878 seconds, with reported cleanup
0.0774 seconds; empty stdout does not identify its execution phase.

These repaired-path results do not erase the historical 1,927-second timeout or
other retained elapsed failures, and do not prove universal outermost OS-bootstrap
timing. **Actual frozen UI experiments, cancellation/navigation and window-close
lifecycle remain unaccepted**: there is no existing safe process-local data/
WebView/runtime diagnostic override while preserving canonical research roots.
Headless verification is not a substitute. A narrowly authorized diagnostic
and new build/hash, or explicit manual acceptance, is needed; no real user
stores/vault should be touched to obtain evidence.

Native CSV selected-file application and actual bundle file-selection flows
also remain unresolved; no further picker automation retry occurred. Personal
FMP cache/derived-use clarification and separate adjustment/action/calendar/PIT/
UK accuracy gates remain open. This candidate is tested progress, **not the
complete production-ready EDGE requested by the user**. No commit/push or new CI
pass is claimed by this documentation update.

### Latest tested-progress candidate — 8 November 2026

The latest `dist/EDGE/EDGE.exe` SHA-256 is
`9aaf4dc8486966720e7efb9b0ce5c0ba846b853e28adc19e2bc13ba32076c581`.
It supersedes the earlier candidates above, includes the narrow documentary
FMP-field correction, and has matching packaged third-party notices. The owner
reports **1,569 source tests passed, 2 Windows symlink-privilege skips** after
the market metadata and runtime-cleanup changes; the subsequent diagnostic-only
harness change passed 45 targeted tests. The full suite was not rerun after that
harness-only change.

The approved direct-Research-Lab independent frozen diagnostic exited **2** after
59.807 seconds, timing out at `engine-select:skfolio`. Actual Qlib completed and
was accepted by the UI in 27.995 seconds, with matching runtime/request/fixture
digests and no automatic startup or Streamlit exception. Skfolio/Kronos UI runs
and cancellation/navigation/window-close-while-running were not reached.
Generated diagnostic application/data/runtime/WebView roots isolated user data
while canonical research roots remained fixed; user data stayed absent/unchanged,
and owned processes/temporary profiles were cleaned.

Earlier diagnostic failures remain evidence: candidate F140… exited 2 after
17.245 seconds without an attributed stage; candidate 65A… timed out after
49.285 seconds at the Watchlist route after three routes. Both cleaned their
owned state. The latest direct-Lab check is **not** an all-twelve-workspace
acceptance pass, nor does it establish that skfolio itself failed.

Automation is stopped; no further build/retry is planned to chase the harness.
One consolidated manual check on this candidate remains: skfolio and Kronos
Research Lab runs; Cancel, navigation cancellation and window close during a run;
native CSV selection-to-apply and bundle save/open/separate-root restore.
Earlier source/headless/backend passes do not substitute for these missing UI
flows. Private FMP retention/derived-use clarification and separate action,
calendar, PIT and UK data accuracy remain open. Research inventories distinguish
public artifact hashes from locally built Gym artifacts; hash-shaped/proposed
locks alone do not prove an independently reproduced build.

This is **tested progress, not final production-ready completion** of every
requested integration. Commit/push/CI status must be reported separately by the
publication owner; this documentation records no new publication or CI result.

### User-reported manual acceptance — 8 November 2026

For candidate SHA-256
`9aaf4dc8486966720e7efb9b0ce5c0ba846b853e28adc19e2bc13ba32076c581`,
the user confirmed at approximately 14:55 UTC:
**“Both engines and run/cancel/navigation/close work”** and
**“CSV and bundle save/restore/cancel work.”**
This closes the consolidated manual verification task for skfolio/Kronos UI,
run/cancellation/navigation/window-close and native CSV/bundle file flows.
It is **user-reported manual acceptance**, not automated evidence or an
independently captured execution trace. The earlier harness failures above remain
recorded; they are not reclassified as passes. No runtime rebuild is implied.

Private FMP retention/derived-use clarification and UK coverage, corporate-action,
calendar and PIT accuracy gates remain unchanged. Manual workflow acceptance
does not establish every requested market-data integration as production-ready.

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

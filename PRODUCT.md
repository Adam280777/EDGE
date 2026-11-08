# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Delivery

The only supported end-user distribution is the standalone local Windows
desktop executable (`dist\EDGE\EDGE.exe`). The Streamlit UI is rendered inside
its native WebView2 window and served only by that launch's loopback process.
There is no hosted web product, direct browser-launch mode, or browser fallback;
`web` describes the embedded UI technology, not a deployment target.

## Users

The primary user is a UK-based retail investor researching companies and making
their own investment decisions.
This is a private single-user personal-research product. Public customers,
multiuser display, commercial redistribution and autonomous execution are
non-goals, not requirements imposed on private financial calculations.

## Product Purpose

EDGE (Equity Discovery & Growth Engine) supports stock discovery, company
research, manual portfolio analysis, decision journaling and realistic strategy
evaluation. Success means helping the user make better-informed decisions with
traceable evidence and clearly stated uncertainty.

## Positioning

EDGE is an explainable decision-support platform, not an autonomous trading bot.
It separates swing-trading research from long-term investment research and
keeps deterministic financial calculations independent from AI-generated
narrative.

## Operating Context

The user researches opportunities across relevant equity markets, enters
executed holdings manually, reviews structured evidence and risk, and remains
responsible for decisions and broker execution.

## Capabilities and Constraints

- Current source adds manual FIFO portfolio accounting, GBP reporting with
  separate GBP/USD cash, explicit opening holdings, US/UK declarations and
  explicit GBp/GBP units. Missing inputs produce incomplete/blocked results.
- Structured research/scenarios, transparent manual discovery and saved plans
  remain declaration-based; they do not certify imported vendor observations.
  Risk is disabled until the user configures and confirms it; only long-only
  whole-share plans are supported, with explicit fees/cash/FX assumptions.
- Local monitoring requires session opt-in while EDGE is open and the Alerts
  task is active. Navigation revokes it. No rules, targets or holdings change
  automatically; no background catch-up/provider work is implied.
- Separate application bundles cover five user-owned databases and non-secret
  settings, restoring only to an absent separate root. Cooperative consistency
  is not authenticity or financial/source acceptance; journal archives retain
  their original journal-only format.
- Source/native capture checks are not certification of the current executable.
  Actual native selected-file/recovery flows and final frozen acceptance remain
  pending. All intended market-data/research integrations must meet the user's
  production-readiness requirement before finalization.
- FMP official evidence supports private personal API use. Cache/retention/
  persistent derived-use terms remain genuinely conflicting; redistribution is
  excluded, not a gate against private arithmetic. Adjustment/action/calendar/
  PIT/UK coverage evidence is independently required. Current provider policy
  has not been relaxed by these documentation updates.
- Canonical installed Python 3.12 Qlib/skfolio/Kronos runtimes now have
  owner-reported real synthetic smoke results and local version/hash consistency
  evidence. These do not establish independent frozen UI/production acceptance
  or real-market research validity. Kronos cold-start timeout reliability remains
  recorded historically; repaired Python 3.12.16 canonical and frozen headless
  synthetic checks have passed within their measured scope. Actual frozen
  research UI/cancel/window-close acceptance remains pending. First-party
  Vibe-inspired templates are concept-only guidance,
  not an installed upstream runtime; Nautilus is the sole simulation authority.

- The first implemented surface is a local, simulation-only NautilusTrader
  acceptance workbench using explicitly synthetic data.
- Native Workspace navigation also exposes explicit opt-in SEC filing metadata
  research (not yet verified against a live response) and a local Research journal.
- Backup and recovery restores only separate copies, with explicit session-only
  opening; original data is never replaced. The separate company watchlist stores
  user-declared labels and notes, not verified identity or company facts.
- Snapshot acceptance reports expose unresolved rights, coverage, adjustments
  and point-in-time evidence; they do not unlock calculations or live trading.
- Provider settings retain only bounded non-secret configuration. Native masked
  Windows credential editing is explicit; startup never reads credentials.
  Authorized historical fetches remain private, ephemeral research display
  only, with rights declarations and unresolved acceptance gates visible.
- The journal stores append-only user-authored swing/long-term notes with optional
  manually declared references, literal-text viewing and saved-entry JSON export.
  Company identity, citations and research/source dates are not verified; these
  notes are not recommendations or trade plans. Only an explicit valid form save
  writes its separate local database; no provider access or calculations occur.
- The initial data contract rejects real market data until source rights,
  identity, timestamps, currency and units, freshness, and corporate-action
  handling have an explicit acceptance path.
- No live broker, real-money trading or autonomous order execution is enabled.
  Explicit FMP research uses the user's existing subscription; no new paid
  service purchase or automatic provider activity is authorized.
- Financial calculations must be deterministic and tested; AI is not their
  source of truth.
- External data providers should remain replaceable, and missing or stale data
  must be surfaced rather than silently estimated.

## Brand Commitments

- The dashboard should have a retro 1980s market-terminal theme, as requested
  by the user.

## Evidence on Hand

- The local synthetic NautilusTrader acceptance workbench and its persisted
  fixture-run evidence.
- The approved EDGE master architecture reference in the user-scoped
  `edge-master-architecture` canvas.
- A prior user-reported FMP sample and official personal API-use documentation
  do not establish complete market datasets, UK/action/PIT coverage or a
  performance track record; none should be fabricated.

## Product Principles

- Prefer financial accuracy and traceable evidence over impressive-looking
  output.
- Separate facts, estimates and opinions and state uncertainty.
- Present strong supporting and opposing evidence for investment theses.
- Never silently alter user risk settings or execute trades.
- Keep upstream responsibilities and EDGE-owned functionality explicit.

## Accessibility & Inclusion

Dashboard data, controls and status must remain legible and usable with keyboard
navigation, clear contrast, and responsive layouts.

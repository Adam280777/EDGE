# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is a UK-based retail investor researching companies and making
their own investment decisions.

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

- The first implemented surface is a local, simulation-only NautilusTrader
  acceptance workbench using explicitly synthetic data.
- Native Workspace navigation also exposes explicit opt-in SEC filing metadata
  research (not yet verified against a live response) and a local Research journal.
- The journal stores append-only user-authored swing/long-term notes with optional
  manually declared references, literal-text viewing and saved-entry JSON export.
  Company identity, citations and research/source dates are not verified; these
  notes are not recommendations or trade plans. Only an explicit valid form save
  writes its separate local database; no provider access or calculations occur.
- The initial data contract rejects real market data until source rights,
  identity, timestamps, currency and units, freshness, and corporate-action
  handling have an explicit acceptance path.
- No live broker, real-money trading, paid data connection, or autonomous order
  execution is enabled.
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
- No validated real-market dataset, market-data entitlement, performance claim,
  or investment track record is available; none should be fabricated.

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

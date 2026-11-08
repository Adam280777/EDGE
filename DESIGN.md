# EDGE dashboard design

## Delivery boundary

EDGE is used as a standalone local Windows desktop application. The Streamlit
workspaces are embedded in a native WebView2 window and served only on that
launch's loopback address. There is no hosted site, direct browser-launch mode,
or browser fallback. Desktop downloads use a native Save As dialog; external
SEC URLs remain selectable text and are never opened by the application.

## Direction

A premium 1980s financial trading terminal rebuilt with modern rendering:
amber phosphor on near-black, Bloomberg-like information density, function-key
navigation and instrument-grade charts. It must read as a purpose-built
financial intelligence system, never as a decorative costume or a generic
Streamlit prototype.

The CRT layer is restrained and static: fine scanlines and a vignette drawn by a
fixed, non-interactive overlay, plus a subtle phosphor glow on headings and key
readouts. Scanlines and glow are removed under `prefers-contrast: more` and
`forced-colors: active`. The only motion is the blinking masthead cursor and the
ticker tape; both stop under `prefers-reduced-motion: reduce`. No sound, flicker,
boot sequences, fake terminal commands or invented market chatter.

## Shell and navigation

- **Masthead:** EDGE mark, product name, fixed status cells (Live trading
  disabled · Market feed: none / user-declared · Pre-production) and the render
  time in UTC. It is static chrome, not a live clock.
- **Function bar:** one native radio styled as function keys
  (`HOME · Command center`, `DSC`, `WCH`, `DEC`, `SEC`, `JRN`, `LAB`, `PRT`,
  `MON`, `ENG`, `PRV`, `BAK`, `APP`). Codes are visual labels only and never
  claim keyboard shortcuts. Option text is uppercased by CSS, so native
  automation must match labels case-insensitively.
- **Breadcrumb:** group › code and title │ purpose │ production status.
- **Command Center (landing page):** a read-only overview. Book overview tiles,
  a holdings ticker tape, allocation and currency-exposure donuts, a stop/target
  proximity track, recorded-quote price tracks, the research pipeline
  (counts plus cumulative records over time) and a system status board.
  Rendering never runs an engine, fetches data or creates files.

## Panes and readouts

Every workspace is composed of bordered panes (`edge_box_*` containers) with a
title bar: function code, title, dashed rule, provenance chips and meta text.
Inside panes, use KPI tiles for headline figures, label/value grids for field
readouts, status lights for states and evidence cards for records. Raw field
dumps and long `st.write` text are not used for primary information.

Provenance chips are mandatory wherever data appears: `MANUAL · USER-DECLARED`,
`SYNTHETIC · NOT MARKET DATA`, `DISPLAY ONLY · NOT PERSISTED`, local storage and
SEC metadata. Missing values render as `Unavailable`; nothing is estimated to
fill a gap. Panes either grow to fit their content or, when the user gives a
pane a fixed height, scroll inside it (nothing is clipped away); below 1050 CSS
pixels the grid stacks panes in reading order and releases fixed heights.

## Configurable layouts

Every workspace is a 12-column grid of panels (`edge.ui_grid`, geometry in
`edge.layout`). Layout is presentation only: panels receive the same data in
every arrangement and the grid never changes what a panel computes.

- **Profiles:** a selector under the breadcrumb chooses `Default`,
  `Swing trading`, `Long-term` (built in, read-only) or a saved custom profile.
  A profile stores, per workspace, each panel's column, row, width, height,
  visibility, display view and fit mode. `Swing trading` leads with risk
  monitoring, alert timelines, screen results and journal entries;
  `Long-term` leads with allocation, filings, valuation snapshots and
  cumulative journal history. Built-in templates never hide data-integrity
  panels (limitations, provenance, status). Placements of panels that only
  appear for some tasks (for example a Discovery task's form) are kept when a
  profile is saved from another task.
- **Edit layout:** a toggle reveals a grip bar on every panel (drag to move),
  a bottom-right corner handle (drag to resize; Escape cancels), and a
  `Panel options` popover that is the keyboard alternative: display view,
  width, height, column, grow-to-fit, move earlier/later and hide. Hidden
  panels are restored from the toolbar. Other panels re-flow upwards to close
  gaps; collisions push panels down, never over each other.
- **Views:** a panel offers alternative views only when they are honest
  presentations of the same data (for example donut vs table, chart vs list).
- **Saving is explicit.** Edits stay in a session draft (an `UNSAVED LAYOUT`
  chip shows) until `Save`, `Save as new`, `Discard` or `Reset workspace`.
  Rendering never writes files. Profiles are stored atomically in
  `settings/layouts-v1.json` beside `provider-v1.json`. A corrupt or
  unreadable file is reported, left untouched and never overwritten; the
  built-in layouts keep working. Layout profiles are user preferences, not
  user records: application recovery bundles do not include them.
- Panels load their data before layout so their order never changes their
  content. In view mode the grid adds no controls.

## Chart grammar

Charts are built only by `edge.ui_charts` (Altair / Vega-Lite, rendered locally
by Streamlit) and only from recorded values; builders return `None` and render
nothing rather than invent data. Prefer time-aware and price-aware forms:

- **Lines and areas:** recorded series and model outputs (`lines`,
  `sparkline`, `price_track`). Lines join recorded observations only; no
  interpolation beyond straight segments, no fetched prices.
- **Candles and execution tape:** OHLC bars (`candles`) and bid/ask step bands
  with recorded fills (`execution_tape`). Engine tape is drawn only when the run
  manifest checksum matches the synthetic fixture.
- **Step lines:** cumulative counts of dated records (`step_area`).
- **Price ladders, proximity tracks and bullets:** stop / entry / target
  geometry, quote position between stop and target, and value against a limit.
- **Donuts, funnels, heat maps, timelines, dot plots, waterfalls:** composition,
  screening attrition, coverage matrices, catalyst and filing dates, ranked
  values and valuation bridges.
- **Bars are a last resort** for purely categorical comparisons (at most one per
  workspace).

Never plot GBP and USD on one axis; split per currency. Quotes are plotted only
when their unit matches the rule unit (GBp vs GBP kept distinct). Decimal values
are passed to charts as exact strings. Every chart has alt text, and its caption
states what is and is not drawn.

## Palette and type

- Near-black `#050505` screen ground, `#10100d` panels and `#0a0a07` deep wells.
- Amber `#ffc15a` text and recorded numerical readouts; `#ffb000` primary keys,
  selected function and chart series; muted amber `#c6ad83` secondary copy.
- Cyan `#65dce8` section labels, pane codes, links, focus and secondary series.
- Green `#70e49a` and red `#ff817a` mark explicit success / error and target /
  stop levels; P&L tone follows sign, and missing values stay muted.
- Violet marks synthetic content. Flat 1px rules and square corners throughout.
- Tabular figures and a local monospace system-font stack; no remote assets.
## Research journal extension

The journal inherits the terminal theme and pane grammar without synthetic
readouts. Visible horizon and task radios separate Swing /
Long-term and Saved entries / New entry. A single-column native form keeps required
identity, date and thesis fields visible; optional narratives and up to three
manual references use disclosures. Enter-to-submit is disabled. Save is explicit
and append-only; draft navigation may discard unsaved edits.

Ten-entry pages use literal-text summaries, native entry selection and previous /
next controls. Saved snapshots show immutable entry identity, user-authored
provenance and UTC creation separately from declared research/source dates.
All user strings use native plain text, never raw HTML or Markdown/link syntax.
Missing sections and references are named as not supplied. Typed failures show
fixed recovery guidance without SQL or note content; previous saved evidence is
never relabelled as a new success. JSON exports do not submit or alter journal
data; a native Save As dialog writes only after the user confirms a destination.
The unverified-notes warning stays above the controls; no broker or risk settings
are added.

## Interaction and accessibility

Backup and recovery uses native file dialogs, not browser-supplied paths.
Restored journals require **Open restored journal** and remain session-only
selections with an explicit return to the original. Company/watchlist controls
inherit the existing plain-text, amber CRT composition; ticker-label matches
and navigation prefills never imply verified identity or provider retrieval.

- Keep all financial inputs, state labels, provenance and synthetic-data
  warnings explicit; never imply live connectivity.
- Preserve embedded Streamlit controls, visible keyboard focus, layout
  responsiveness and reduced-motion preferences.
- Retain native control names and stable action keys. Terminal-like saturated
  buttons do not claim F-key shortcuts; Tab, Enter and Space remain native.
- Status labels carry meaning independently of colour. Source and fixed fixture
  timestamps are visible, never described as live freshness.
- Scope CSS to Streamlit test-id/native widget selectors. Streamlit upgrades
  require checking those selectors in a rendered browser; AppTest alone cannot
  verify CSS layout, canvas-table colours or keyboard focus appearance.

## Native desktop startup

The standalone Windows startup is a separate stdlib Tk Operate surface, not a
replacement for any financial workspace. Near-black ground, amber local
`TkFixedFont` type and two fine cyan rules echo the dashboard without copied
branding, market statistics, sound or flashing (Tk has no glow or scanline layer).
The native title bar preserves move, minimise and close controls.

Real runtime stage text appears immediately, with wrapping rather than fake
percentages or invented boot logs. A small amber cursor traverses a muted
segmented track while readiness is unknown; it represents activity, not
completion. Only that bounded region moves, at 140 ms per step. No entrance
choreography, artificial minimum duration or exit delay holds up the workspace.

Skip animation and Escape leave a static status view while startup continues.
Windows `SPI_GETCLIENTAREAANIMATION` is honoured by default; unavailable
preferences fail static, and the constructor accepts an explicit override.
Minimising pauses the animation. Flat keyboard-operable controls have cyan
focus outlines. Explicit Cancel startup (when the runtime supplies a callback)
and window close request cancellation; Skip never does.

`DesktopSplash(on_cancel=None, reduced_motion=None)` is main-thread-only.
The runtime owns readiness work, stage queues, cancellation and WebView.
It calls `set_stage(text)` and `pump()` while waiting, then `close()` immediately
before opening the workspace. `pump()` is non-blocking and returns false once
closed; `cancelled` distinguishes user cancellation from programmatic close.
The splash performs no application-data, network, font-download or filesystem
operations and has no separate event loop or runtime process logic.

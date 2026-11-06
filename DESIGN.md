# EDGE dashboard design

## Direction

User-selected amber CRT on near-black, replacing the earlier navy/green theme.
The dense four-pane market-terminal reference governs the Streamlit workbench:
flat rules, compact readouts, cyan section labels and saturated action keys.
The physical 1980s terminal informs the local monospace typography, not an image,
hardware frame, scanline overlay, glow effect or faux command interface.

## Dashboard composition

This is an Operate surface: run a synthetic acceptance scenario, then inspect
traceable engine evidence. Two equal-width rows contain four native Streamlit
panes: engine acceptance / fixture provenance, then session output / persisted
history. Planning context is a disclosure in the fixture pane, not a competing
sidebar. The full-width simulation warning remains above all controls.

Pane contents grow naturally; no fixed-height clipping. Below 1050 CSS pixels,
the outer pane rows stack in reading order. Native tables retain horizontal
scrolling, tabs, selectors and evidence downloads. Smaller screens get wider
touch targets. Empty panes state what is unavailable rather than filling space
with fabricated market content.

## Palette and type

- Near-black `#050505` screen ground and `#10100d` control/alert ground.
- Amber `#ffc15a` text and recorded numerical readouts; muted amber `#c6ad83`
  for secondary copy. Primary action keys and selected tabs use `#ffb000`.
- Cyan `#65dce8` section labels, secondary keys, links and keyboard focus.
- Green `#70e49a` is reserved for explicit success, red `#ff817a` for errors.
  Neutral cash, quantity and P&L readouts stay amber, not automatic green gains.
- Flat 1px `#6e6045` rules and square corners; no rounded metric cards or shadows.
- Tabular figures and a local monospace system-font stack support dense
  instrument-like readouts without loading remote assets.

## Research journal extension

The third native Workspace inherits the amber CRT theme without decorative
panels or synthetic readouts. Visible horizon and task radios separate Swing /
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
never relabelled as a new success. JSON downloads do not submit or write files.
The unverified-notes warning stays above the controls; no broker or risk settings
are added.

## Interaction and accessibility

- Keep all financial inputs, state labels, provenance and synthetic-data
  warnings explicit; never imply live connectivity.
- Preserve native web controls, visible keyboard focus, responsive layout and
  reduced-motion preferences.
- Retain native control names and stable action keys. Terminal-like saturated
  buttons do not claim F-key shortcuts; Tab, Enter and Space remain native.
- Status labels carry meaning independently of colour. Source and fixed fixture
  timestamps are visible, never described as live freshness.
- Scope CSS to Streamlit test-id/native widget selectors. Streamlit upgrades
  require checking those selectors in a rendered browser; AppTest alone cannot
  verify CSS layout, canvas-table colours or keyboard focus appearance.

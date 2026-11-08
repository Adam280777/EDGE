"""Terminal design system stylesheet; static CSS only, no remote assets."""

BASE_CSS = """
:root {
  color-scheme: dark;
  --edge-void: #050505;
  --edge-panel: #10100d;
  --edge-rule: #6e6045;
  --edge-ink: #ffc15a;
  --edge-muted: #c6ad83;
  --edge-success: #70e49a;
  --edge-amber: #ffb000;
  --edge-cyan: #65dce8;
  --edge-alert: #ff817a;
  --edge-mono: "Cascadia Mono", "Cascadia Code", "Consolas", "Courier New", monospace;
}

html, body {
  font-family: var(--edge-mono);
}
[data-testid="stAppViewContainer"] {
  color: var(--edge-ink);
  background-color: var(--edge-void);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stAppDeployButton"] { display: none; }
[data-baseweb="input"], [data-baseweb="input"] > div,
[data-baseweb="textarea"], [data-baseweb="select"] > div {
  border-radius: 0 !important;
}
[data-testid="stMainBlockContainer"] {
  max-width: 1600px;
  padding: 2.5rem 1.5rem 1.5rem;
}
[data-testid="stVerticalBlock"], [data-testid="stHorizontalBlock"] {
  gap: 0.65rem;
}
[data-testid="stColumn"] { min-width: 0; }
.st-key-edge_workspace [role="radiogroup"] {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
}
.st-key-edge_workspace label { min-height: 2rem; }
.st-key-edge_pane_row > [data-testid="stHorizontalBlock"],
.st-key-edge_output_row > [data-testid="stHorizontalBlock"] {
  display: flex;
}
.st-key-edge_acceptance, .st-key-edge_fixture,
.st-key-edge_recent, .st-key-edge_history,
[data-testid="stForm"],
[data-testid="stExpander"] details {
  border: 1px solid var(--edge-rule);
  border-radius: 0;
  box-shadow: none;
  background: var(--edge-void);
}
.stApp h1, .stApp h2, .stApp h3,
.stApp button, .stApp input, .stApp textarea, .stApp select,
.stApp p, .stApp label, .stApp li {
  font-family: var(--edge-mono);
}
.stApp h1, .stApp h2, .stApp h3 {
  color: var(--edge-ink);
  font-variant-numeric: tabular-nums;
}
.stApp h1 {
  font-size: clamp(1.25rem, 2vw, 1.7rem);
  letter-spacing: 0.035em;
  text-transform: uppercase;
  padding-block-end: 0.4rem;
  border-block-end: 1px solid var(--edge-rule);
  overflow-wrap: normal;
  word-break: normal;
}
.stApp h2 {
  margin: 0;
  padding: 0 0 0.45rem;
  border-block-end: 1px solid var(--edge-rule);
  color: var(--edge-cyan);
  font-size: 0.95rem;
  letter-spacing: 0.055em;
  text-transform: uppercase;
}
.stApp h3 {
  font-size: 0.95rem;
  color: var(--edge-cyan);
  margin-block: 0.65rem 0.25rem;
}
.stApp p, .stApp label, .stApp li, [data-testid="stCaptionContainer"] {
  color: var(--edge-ink);
  font-size: 0.875rem;
  line-height: 1.4;
}
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
  color: var(--edge-muted);
  opacity: 1;
}
[data-testid="stCaptionContainer"] p { font-size: 0.8rem; }
[data-testid="stMetric"] {
  padding: 0.35rem 0;
  border-block-end: 1px solid var(--edge-rule);
}
[data-testid="stMetricLabel"] { color: var(--edge-muted); }
[data-testid="stMetricLabel"] p { white-space: normal; }
[data-testid="stMetricValue"] {
  color: var(--edge-ink);
  font-size: 1.4rem;
  font-family: var(--edge-mono);
  font-variant-numeric: tabular-nums;
}
[data-testid="stDataFrame"] {
  border: 1px solid var(--edge-rule);
  border-radius: 0;
}
[data-testid="stDataFrame"] [role="columnheader"] {
  color: var(--edge-cyan);
  font-family: var(--edge-mono);
}
[data-testid="stAlert"] {
  border: 1px solid var(--edge-rule);
  border-radius: 0;
}
[data-testid="stAlertContainer"] {
  color: var(--edge-ink) !important;
  background: var(--edge-panel) !important;
  border-radius: 0 !important;
  border: 0 !important;
  padding: 0.55rem 0.65rem;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) {
  border-color: var(--edge-cyan) !important;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {
  border-color: var(--edge-success) !important;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) {
  border-color: var(--edge-amber) !important;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {
  border-color: var(--edge-alert) !important;
}
[data-testid="stAlertContentInfo"] { color: var(--edge-cyan) !important; }
[data-testid="stAlertContentSuccess"] { color: var(--edge-success) !important; }
[data-testid="stAlertContentWarning"] { color: var(--edge-amber) !important; }
[data-testid="stAlertContentError"] { color: var(--edge-alert) !important; }
[data-testid="stAlertContentInfo"] p { color: var(--edge-cyan); }
[data-testid="stAlertContentSuccess"] p { color: var(--edge-success); }
[data-testid="stAlertContentWarning"] p { color: var(--edge-ink); }
[data-testid="stAlertContentError"] p { color: var(--edge-alert); }
.stButton > button, [data-testid="stDownloadButton"] > button,
[data-testid="stFormSubmitButton"] > button {
  min-height: 2.25rem;
  color: var(--edge-void);
  background: var(--edge-cyan);
  border: 1px solid var(--edge-cyan);
  border-radius: 0;
  font-family: var(--edge-mono);
}
.stButton > button p, [data-testid="stDownloadButton"] > button p,
[data-testid="stFormSubmitButton"] > button p { color: inherit; }
.stButton > button:hover, [data-testid="stDownloadButton"] > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
  color: var(--edge-void);
  background: var(--edge-ink);
  border-color: var(--edge-ink);
}
.stButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] {
  color: var(--edge-void);
  background: var(--edge-amber);
  border-color: var(--edge-amber);
}
.stButton > button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:hover {
  background: var(--edge-ink);
  border-color: var(--edge-ink);
}
.stButton > button:disabled,
[data-testid="stFormSubmitButton"] > button:disabled {
  color: var(--edge-muted);
  background: var(--edge-panel);
  border-color: var(--edge-rule);
}
:focus-visible {
  outline: 2px solid var(--edge-cyan) !important;
  outline-offset: 3px;
}
[data-testid="stTabs"] button[aria-selected="true"] {
  color: var(--edge-amber);
  border-block-end-color: var(--edge-amber);
}
[data-baseweb="tab-list"] { gap: 1rem; }
[data-baseweb="tab"] { min-height: 2rem; padding-inline: 0.25rem; }
input, textarea, [data-baseweb="select"] > div {
  color: var(--edge-ink) !important;
  background-color: var(--edge-panel) !important;
  border-color: var(--edge-rule) !important;
  border-radius: 0 !important;
  caret-color: var(--edge-amber);
}
a { color: var(--edge-cyan); text-underline-offset: 0.2em; }
a:hover { color: var(--edge-amber); }
::selection { color: var(--edge-void); background: var(--edge-amber); }
::-webkit-scrollbar { width: 12px; height: 12px; }
::-webkit-scrollbar-track { background: var(--edge-void); }
::-webkit-scrollbar-thumb {
  background: var(--edge-rule);
  border: 3px solid var(--edge-void);
  border-radius: 0;
}
/* Native columns retain reading order; stack panes before their copy gets cramped. */
@media (max-width: 1050px) {
  .st-key-edge_pane_row > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"],
  .st-key-edge_output_row > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"],
  .st-key-edge_pane_row > [data-testid="stHorizontalBlock"],
  .st-key-edge_output_row > [data-testid="stHorizontalBlock"] {
    flex-direction: column;
  }
  .st-key-edge_pane_row [data-testid="stColumn"],
  .st-key-edge_output_row [data-testid="stColumn"] {
    width: 100%;
    flex: 1 1 100%;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_acceptance),
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_recent) {
    flex-direction: column;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_acceptance) > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_recent) > [data-testid="stColumn"] {
    width: 100%;
    flex: 1 1 100%;
  }
}
@media (max-width: 700px) {
  [data-testid="stMainBlockContainer"] { padding: 1rem 1rem 2rem; }
  .stApp h1 { font-size: 1.25rem; }
  .stButton > button, [data-testid="stDownloadButton"] > button,
  [data-testid="stFormSubmitButton"] > button { min-height: 2.75rem; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto !important; }
  .stButton > button, input, textarea { transition: none !important; }
}
"""

TERMINAL_CSS = """
:root {
  --edge-violet: #b48cff;
  --edge-grid: #2a2418;
  --edge-deep: #0a0a07;
  --edge-glow-amber: 0 0 6px rgba(255, 176, 0, 0.45), 0 0 18px rgba(255, 176, 0, 0.18);
  --edge-glow-cyan: 0 0 6px rgba(101, 220, 232, 0.4);
}
/* CRT layer: static scanlines and vignette, never animated, never interactive. */
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(ellipse at 50% -10%, rgba(255, 176, 0, 0.07), transparent 55%),
    radial-gradient(ellipse at 50% 120%, rgba(101, 220, 232, 0.05), transparent 60%),
    var(--edge-void);
}
.stApp::after {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 999990;
  background:
    repeating-linear-gradient(to bottom, transparent 0, transparent 2px, rgba(0, 0, 0, 0.18) 3px),
    radial-gradient(ellipse at center, transparent 62%, rgba(0, 0, 0, 0.45) 100%);
}
@media (prefers-contrast: more), (forced-colors: active) {
  .stApp::after { display: none; }
  :root { --edge-glow-amber: none; --edge-glow-cyan: none; }
}
/* Units whose case carries meaning (GBp = pence) are exempt from uppercase labels. */
.edge-keepcase { text-transform: none !important; }
/* Selected multiselect values sit on amber; Streamlit's white tag text is unreadable there. */
.stApp [data-baseweb="tag"], .stApp [data-tag] { color: var(--edge-void); }
.stApp [data-baseweb="tag"] span, .stApp [data-tag] span { color: var(--edge-void); text-shadow: none; }
.stApp [data-tag] button svg { fill: var(--edge-void); }
[data-testid="stMainBlockContainer"] { padding-top: 1.25rem; }

/* Masthead */
.edge-mast {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: stretch;
  gap: 0;
  border: 1px solid var(--edge-rule);
  background: linear-gradient(180deg, #14120b, var(--edge-deep));
}
.edge-mast-brand {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  padding: 0.55rem 1rem;
  border-inline-end: 1px solid var(--edge-rule);
}
.edge-mast-logo {
  font-size: 1.9rem;
  font-weight: 700;
  letter-spacing: 0.28em;
  color: var(--edge-amber);
  text-shadow: var(--edge-glow-amber);
  line-height: 1;
}
.edge-mast-name { display: grid; line-height: 1.15; }
.edge-mast-name b {
  color: var(--edge-ink);
  font-size: 0.95rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.edge-mast-name span { color: var(--edge-muted); font-size: 0.7rem; letter-spacing: 0.14em; text-transform: uppercase; }
.edge-mast-cells { display: flex; flex-wrap: wrap; }
.edge-mast-cell {
  display: grid;
  align-content: center;
  padding: 0.35rem 0.9rem;
  border-inline-end: 1px solid var(--edge-grid);
  min-width: 9rem;
}
.edge-mast-cell small { color: var(--edge-cyan); font-size: 0.62rem; letter-spacing: 0.16em; text-transform: uppercase; }
.edge-mast-cell span { color: var(--edge-ink); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.edge-mast-cell .edge-off { color: var(--edge-alert); }
.edge-mast-clock {
  display: grid;
  align-content: center;
  justify-items: end;
  padding: 0.35rem 1rem;
  border-inline-start: 1px solid var(--edge-rule);
  color: var(--edge-amber);
  text-shadow: var(--edge-glow-amber);
  font-variant-numeric: tabular-nums;
}
.edge-mast-clock small { color: var(--edge-muted); font-size: 0.62rem; letter-spacing: 0.16em; text-shadow: none; }
.edge-cursor { display: inline-block; width: 0.55em; height: 1em; margin-inline-start: 0.2em; vertical-align: -0.12em; background: var(--edge-amber); animation: edge-blink 1.1s steps(1) infinite; }
@keyframes edge-blink { 50% { opacity: 0; } }
.edge-crumb {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 1rem;
  align-items: center;
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--edge-grid);
  border-block-start: 0;
  background: var(--edge-deep);
  color: var(--edge-muted);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.edge-crumb b { color: var(--edge-amber); font-weight: 600; }
.edge-crumb .edge-crumb-sep { color: var(--edge-rule); }

/* Function-key navigation and segmented radios */
.st-key-edge_workspace {
  padding: 0.45rem 0.5rem 0.5rem;
  border: 1px solid var(--edge-rule);
  border-block-start: 0;
  background: var(--edge-panel);
}
.st-key-edge_workspace > label, .st-key-edge_workspace [data-testid="stWidgetLabel"] {
  color: var(--edge-cyan);
  font-size: 0.66rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
[data-testid="stRadio"] [role="radiogroup"] { display: flex; flex-wrap: wrap; gap: 0.3rem; }
[data-testid="stRadio"] [data-testid="stRadioOption"] {
  margin: 0;
  padding: 0.28rem 0.65rem;
  min-height: 2rem;
  align-items: center;
  border: 1px solid var(--edge-rule);
  background: var(--edge-void);
  cursor: pointer;
}
[data-testid="stRadio"] [data-testid="stRadioOption"] > div > div:not([data-testid="stMarkdownContainer"]) { display: none; }
[data-testid="stRadio"] [data-testid="stRadioOption"] p {
  font-size: 0.78rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--edge-muted);
  white-space: nowrap;
}
[data-testid="stRadio"] [data-testid="stRadioOption"]:hover { border-color: var(--edge-cyan); }
[data-testid="stRadio"] [data-testid="stRadioOption"]:hover p { color: var(--edge-cyan); }
[data-testid="stRadio"] [data-testid="stRadioOption"][data-selected="true"] {
  background: var(--edge-amber);
  border-color: var(--edge-amber);
  box-shadow: 0 0 14px rgba(255, 176, 0, 0.35);
}
[data-testid="stRadio"] [data-testid="stRadioOption"][data-selected="true"] p { color: var(--edge-void); font-weight: 700; }
[data-testid="stRadio"] [data-testid="stRadioOption"]:has(input:focus-visible) {
  outline: 2px solid var(--edge-cyan);
  outline-offset: 2px;
}

/* Pane title bars and bordered boxes */
.edge-pane-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.55rem;
  margin: 0.2rem 0 0.35rem;
  min-height: 1.6rem;
}
.edge-pane-code {
  padding: 0.05rem 0.4rem;
  color: var(--edge-void);
  background: var(--edge-cyan);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.1em;
}
.edge-pane-title {
  color: var(--edge-cyan);
  font-size: 0.86rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  white-space: nowrap;
  text-shadow: var(--edge-glow-cyan);
}
.edge-pane-rule { flex: 1 1 2rem; height: 1px; background: repeating-linear-gradient(90deg, var(--edge-rule) 0 6px, transparent 6px 9px); }
.edge-pane-meta { color: var(--edge-muted); font-size: 0.7rem; letter-spacing: 0.06em; white-space: nowrap; }
[class*="st-key-edge_box_"] {
  position: relative;
  padding: 0.55rem 0.75rem 0.7rem;
  border: 1px solid var(--edge-rule);
  background: linear-gradient(180deg, rgba(255, 176, 0, 0.025), transparent 40%), var(--edge-deep);
}
[class*="st-key-edge_box_"]::before, [class*="st-key-edge_box_"]::after {
  content: "";
  position: absolute;
  width: 9px;
  height: 9px;
  pointer-events: none;
  border-color: var(--edge-amber);
  border-style: solid;
}
[class*="st-key-edge_box_"]::before { top: -1px; left: -1px; border-width: 2px 0 0 2px; }
[class*="st-key-edge_box_"]::after { bottom: -1px; right: -1px; border-width: 0 2px 2px 0; }

/* Provenance chips */
.edge-chip {
  display: inline-block;
  padding: 0.04rem 0.4rem;
  border: 1px solid currentColor;
  font-size: 0.62rem;
  letter-spacing: 0.1em;
  white-space: nowrap;
  text-transform: uppercase;
}
.edge-chip--cyan, .edge-tone--cyan { color: var(--edge-cyan); }
.edge-chip--amber, .edge-tone--amber { color: var(--edge-amber); }
.edge-chip--green, .edge-tone--green { color: var(--edge-success); }
.edge-chip--red, .edge-tone--red { color: var(--edge-alert); }
.edge-chip--violet, .edge-tone--violet { color: var(--edge-violet); }
.edge-chip--muted, .edge-tone--muted { color: var(--edge-muted); }
.edge-chip--ink, .edge-tone--ink { color: var(--edge-ink); }

/* KPI tiles: recorded values only; Unavailable is styled, never zero. */
.edge-tiles {
  display: grid;
  /* --edge-cols is a maximum: tiles wrap when a resized panel makes columns narrower than 9.5rem. */
  grid-template-columns: repeat(auto-fit, minmax(max(9.5rem, calc((100% - (var(--edge-cols, 99) - 1) * 1px) / var(--edge-cols, 99))), 1fr));
  /* Dividers are tile shadows in the 1px gap (clipped at the frame) so an unfilled last row stays dark. */
  gap: 1px;
  background: var(--edge-void);
  border: 1px solid var(--edge-rule);
  overflow: hidden;
  margin-block: 0.25rem 0.5rem;
}
.edge-tile { padding: 0.45rem 0.65rem 0.5rem; background: var(--edge-void); min-width: 0;
  box-shadow: 1px 0 0 var(--edge-rule), 0 1px 0 var(--edge-rule), 1px 1px 0 var(--edge-rule); }
.edge-tile-label { color: var(--edge-muted); font-size: 0.64rem; letter-spacing: 0.14em; text-transform: uppercase; }
.edge-tile-value {
  margin-top: 0.1rem;
  font-size: 1.32rem;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  overflow-wrap: anywhere;
  text-shadow: 0 0 8px currentColor;
}
.edge-tile-unit { margin-inline-start: 0.3rem; font-size: 0.72rem; color: var(--edge-muted); text-shadow: none; }
.edge-tile-note { color: var(--edge-muted); font-size: 0.66rem; margin-top: 0.15rem; }
.edge-tile--ink .edge-tile-value { color: var(--edge-ink); }
.edge-tile--amber .edge-tile-value { color: var(--edge-amber); }
.edge-tile--cyan .edge-tile-value { color: var(--edge-cyan); }
.edge-tile--green .edge-tile-value { color: var(--edge-success); }
.edge-tile--red .edge-tile-value { color: var(--edge-alert); }
.edge-tile--violet .edge-tile-value { color: var(--edge-violet); }
.edge-tile--muted .edge-tile-value, .edge-tile--na .edge-tile-value {
  color: var(--edge-muted);
  font-size: 0.95rem;
  text-shadow: none;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

/* Label/value readout grid */
.edge-kv {
  display: grid;
  grid-template-columns: repeat(var(--edge-kv-cols, 2), minmax(8rem, max-content) minmax(0, 1fr));
  gap: 1px 0;
  border-block: 1px solid var(--edge-grid);
  font-size: 0.8rem;
  margin-block: 0.25rem 0.5rem;
}
.edge-kv-k, .edge-kv-v { padding: 0.2rem 0.6rem 0.2rem 0; border-block-end: 1px dotted var(--edge-grid); }
.edge-kv-k { color: var(--edge-muted); text-transform: uppercase; font-size: 0.68rem; letter-spacing: 0.08em; align-self: center; }
.edge-kv-v { color: var(--edge-ink); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }

/* System status board */
.edge-board { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1px; background: var(--edge-grid); border: 1px solid var(--edge-rule); }
.edge-light { display: grid; grid-template-columns: auto 1fr auto; gap: 0.1rem 0.55rem; align-items: center; padding: 0.4rem 0.6rem; background: var(--edge-void); }
.edge-light-dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; background: var(--edge-muted); }
.edge-light--ok .edge-light-dot { background: var(--edge-success); box-shadow: 0 0 8px var(--edge-success); }
.edge-light--warn .edge-light-dot { background: var(--edge-amber); box-shadow: 0 0 8px var(--edge-amber); }
.edge-light--bad .edge-light-dot { background: var(--edge-alert); box-shadow: 0 0 8px var(--edge-alert); }
.edge-light-label { color: var(--edge-ink); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.06em; }
.edge-light-state { font-size: 0.66rem; letter-spacing: 0.12em; color: var(--edge-muted); }
.edge-light--ok .edge-light-state { color: var(--edge-success); }
.edge-light--warn .edge-light-state { color: var(--edge-amber); }
.edge-light--bad .edge-light-state { color: var(--edge-alert); }
.edge-light-detail { grid-column: 2 / 4; color: var(--edge-muted); font-size: 0.68rem; }

/* Tape */
.edge-tape { overflow: hidden; border: 1px solid var(--edge-rule); border-block-start: 0; background: #000; white-space: nowrap; }
.edge-tape-track { display: inline-flex; animation: edge-tape 48s linear infinite; }
.edge-tape:hover .edge-tape-track { animation-play-state: paused; }
.edge-tape-item { display: inline-flex; gap: 0.45rem; padding: 0.22rem 1.1rem; border-inline-end: 1px solid var(--edge-grid); font-size: 0.76rem; }
.edge-tape-item b { color: var(--edge-ink); letter-spacing: 0.06em; }
@keyframes edge-tape { from { transform: translateX(0); } to { transform: translateX(-50%); } }

/* Empty, legend and rule labels */
.edge-empty { display: flex; gap: 0.7rem; align-items: center; padding: 0.8rem; border: 1px dashed var(--edge-rule); color: var(--edge-muted); }
.edge-empty-glyph { font-size: 1.6rem; color: var(--edge-rule); }
.edge-empty-msg { color: var(--edge-ink); font-size: 0.82rem; }
.edge-empty-hint { font-size: 0.72rem; }
.edge-legend { display: flex; flex-wrap: wrap; gap: 0.3rem 1rem; font-size: 0.7rem; color: var(--edge-muted); }
.edge-pipe-counts { display: grid; grid-template-columns: repeat(auto-fill, minmax(6.5rem, 1fr)); gap: 0.25rem; margin-block-end: 0.4rem; }
.edge-pipe-counts span { display: grid; gap: 0.1rem; padding: 0.4rem 0.55rem; border: 1px solid var(--edge-rule); background: var(--edge-void); color: var(--edge-muted); font-size: 0.62rem; letter-spacing: 0.12em; text-transform: uppercase; }
.edge-pipe-counts b { color: var(--edge-amber); font-size: 1.15rem; font-weight: 700; letter-spacing: 0; font-variant-numeric: tabular-nums; }
.edge-swatch { display: inline-block; width: 0.7rem; height: 0.7rem; margin-inline-end: 0.35rem; vertical-align: -0.05rem; }
.edge-swatch--amber { background: var(--edge-amber); }
.edge-swatch--cyan { background: var(--edge-cyan); }
.edge-swatch--green { background: var(--edge-success); }
.edge-swatch--red { background: var(--edge-alert); }
.edge-swatch--violet { background: var(--edge-violet); }
.edge-swatch--muted, .edge-swatch--ink { background: var(--edge-muted); }
.edge-rule-label { display: flex; align-items: center; gap: 0.6rem; margin: 0.6rem 0 0.2rem; color: var(--edge-muted); font-size: 0.68rem; letter-spacing: 0.18em; text-transform: uppercase; }
.edge-rule-label::after { content: ""; flex: 1; height: 1px; background: var(--edge-grid); }

/* Charts and tables sit on the terminal ground */
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] { background: transparent; }
[data-testid="stExpander"] summary p { color: var(--edge-cyan); letter-spacing: 0.05em; }
.stApp h2 { text-shadow: var(--edge-glow-cyan); }

@media (max-width: 1050px) {
  .edge-mast { grid-template-columns: 1fr; }
  .edge-mast-brand, .edge-mast-clock { border: 0; border-block-end: 1px solid var(--edge-rule); }
  .edge-mast-clock { justify-items: start; }
  .edge-kv { grid-template-columns: minmax(7rem, max-content) minmax(0, 1fr); }
  /* Fixed tile counts reflow in narrow windows instead of overflowing sideways. */
  .edge-tiles { grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); }
  .edge-pane-title, .edge-pane-meta { white-space: normal; min-width: 0; }
  .stButton > button p, [data-testid="stFormSubmitButton"] > button p { white-space: normal; }
}
@media (prefers-reduced-motion: reduce) {
  .edge-cursor { animation: none; }
  .edge-tape { overflow-x: auto; }
  .edge-tape-track { animation: none; }
}
"""


def stylesheet() -> str:
    return f"<style>{BASE_CSS}{TERMINAL_CSS}</style>"
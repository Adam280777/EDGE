# Third-party components

EDGE integrates upstream software rather than implementing a competing trading
engine. This document identifies direct dependencies; it is not legal advice or
an exhaustive transitive dependency inventory.

EDGE's current scope is private single-user personal research, not commercial
distribution, public customers or multiuser display. External-distribution
obligations below apply only if that excluded scope changes; they are not
prerequisites invented for private financial arithmetic.

| Component | Role | Upstream licence |
|---|---|---|
| [NautilusTrader 2.0.0rc8](https://github.com/nautechsystems/nautilus_trader/tree/v2.0.0rc8) | Native simulation, orders, risk, account and position infrastructure | [GNU LGPLv3](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc8/LICENSE) |
| [Streamlit](https://github.com/streamlit/streamlit) | Local acceptance workbench | Apache-2.0 |
| [OpenBB Core 2.0.1](https://pypi.org/project/openbb-core/2.0.1/) | Optional SEC provider framework | Apache-2.0 |
| [OpenBB SEC 2.0.0](https://pypi.org/project/openbb-sec/2.0.0/) | Optional SEC filing-metadata provider | Apache-2.0 |
| [pytest](https://github.com/pytest-dev/pytest) | Development tests | MIT |
| [setuptools](https://github.com/pypa/setuptools) | Python packaging/build tooling | MIT |

Nautilus's inspected root licence is standard LGPLv3, not a bespoke proprietary
licence. Independent application code may remain proprietary subject to applicable
conditions. External distribution of covered modifications or combined works
requires appropriate source, notices and replacement/relinking compliance. Do not
assume a Python import, subprocess or API boundary automatically resolves those
requirements. Review packaging and the exact installed dependency set before
external delivery, including applicable bundled notices and patent conditions.

The integration calls installed public interfaces; it does not copy or modify
Nautilus source. The upstream copyright and licence materials remain in the
installed distribution. Do not remove them when packaging EDGE.

The OpenBB SEC integration is opt-in and limited to filing metadata. A lookup
sends the user-configured SEC User-Agent to SEC endpoints; EDGE does not log or
persist that value. The adapter depends on OpenBB's internal SEC fetcher and
shared-header modules and is pinned to the exact versions above, so upstream
changes require compatibility review and tests before updating.

No rights to financial feeds, exchange data, training datasets or model weights
are granted by these software licences. OpenBB market-data providers, Qlib,
skfolio, Kronos and Vibe-Trading integrations have not been adopted by this
milestone's original acceptance. Subsequent canonical personal research
environments now have owner-reported installed pyqlib 0.9.7, skfolio 1.12.5 and
CPU torch 2.7.1+cpu with actual synthetic Qlib/skfolio/Kronos smokes. Published
inventories and local hashes do not establish signed authenticity, model quality,
real-market validity or independent frozen production acceptance.
The later canonical base is Python 3.12.16; independently checked frozen
headless synthetic execution is not full UI, security or financial acceptance.
Published MIT source/model-card evidence for selected research artifacts was
inspected; runtime and financial acceptance remain separate. The Windows lock includes the transitive requirements
for this optional extra; review the exact installed dependency set before
external distribution.

VectorBT (Commons Clause addition) and Backtesting.py (AGPL-3.0) are intentionally
not dependencies of this application.

## Local Windows desktop build

The opt-in desktop extra adds pywebview 6.2.1 (BSD-3-Clause), Python.NET
(MIT), clr-loader (MIT), and PyInstaller 6.22.3 (GPL-2.0-or-later with the
bootloader exception). CPython uses the PSF licence. Tk/Tcl licences, native
wheel notices and Microsoft WebView2 SDK redistributable notices must also be
retained. WebView2 Evergreen Runtime is an independently installed prerequisite,
not bundled or automatically downloaded by EDGE.

`scripts/build_desktop.py` preserves installed distribution licence/notice
files and a conservative dependency inventory in `_internal/licenses` in the
folder distribution. These notices do not clear all external-distribution
obligations. In particular Nautilus LGPL corresponding-source and
replacement/relinking requirements need release review before sharing externally.
This unsigned local build contains unmodified upstream dependencies, not an
externally cleared or signed installer.

## Personal financial-data use

Official FMP rendered terms/pricing/API documentation were reviewed
7 November 2026. [Terms §§1.1, 2.1, 2.2.1](https://site.financialmodelingprep.com/developer/docs/terms-of-service)
support individual personal API use. Multiuser/commercial requirements are not
applied to this private product. Copy/download/derivative and retention versus
termination/cache-deletion clauses remain genuinely conflicting for local
storage and persistent derived research; clarification is needed for the precise
personal use. This is neither a categorical prohibition nor a lawyer prerequisite.
The linked ADUP returned an official 404. Software licences and successful HTTP
responses do not settle endpoint/exchange/data-use rights.

Stable EOD/full's documented split-adjusted close is positive field-specific
evidence, not proof of all OHLC/volume/VWAP bases, complete corporate actions,
historical availability or UK coverage. Current code remains private ephemeral
FMP display only; no provider gates changed in this documentation handoff.
Redistribution remains an excluded non-goal. See README's current evidence
section for research-budget assumptions and unverified runtime/native flows.

"""Session-only SEC metadata presentation. No provider import or disk persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime

import streamlit as st

from edge import ui_charts as charts
from edge.data import ALLOWED_FORM_TYPES, DEFAULT_FORM_TYPES
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    empty,
    kv_grid,
    status_board,
    status_light,
    tiles,
)

RESULT_KEY = "edge_sec_evidence"
ERROR_KEY = "edge_sec_error"
_COLUMN_ORDER = (
    "form_type",
    "filing_date",
    "report_date",
    "accepted_at",
    "accession_number",
    "primary_document",
    "filing_index_url",
    "primary_document_url",
)


def _json_date(value: object) -> str:
    """Encode the API's date fields using the CLI's UTC, null-preserving contract.

    The CLI helper is private and imports simulation/storage machinery. Only
    date and aware datetime values need conversion in this metadata dataclass.
    Unsupported types fail closed rather than being silently stringified.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("An aware timestamp is required.")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("Unsupported SEC metadata value.")


def _date_input(value: str) -> date | None:
    if not value.strip():
        return None
    try:
        parsed = date.fromisoformat(value.strip())
        if parsed.isoformat() == value.strip():
            return parsed
    except ValueError:
        pass
    raise ValueError("Use ISO calendar dates (YYYY-MM-DD), or leave the bounds blank.")


def _submit(ticker: str, forms: list[str], start: str, end: str) -> None:
    """The only lookup boundary; replace success with failure, never stale success."""
    from edge.data import (
        SecFilingConfigurationError,
        SecFilingDataError,
        SecFilingInputError,
        SecFilingNotFoundError,
        SecFilingProviderError,
        lookup_sec_filings,
    )

    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(ERROR_KEY, None)
    try:
        start_date, end_date = _date_input(start), _date_input(end)
    except ValueError as error:
        st.session_state[ERROR_KEY] = str(error)  # Fixed local text, never user input.
        return
    try:
        with st.spinner("Looking up SEC filing metadata…"):
            result = lookup_sec_filings(
                ticker,
                form_types=forms,
                start_date=start_date,
                end_date=end_date,
            )
            # Same dataclass field contract as `edge sec-filings`; no UI envelope.
            evidence = json.dumps(
                asdict(result), indent=2, allow_nan=False, default=_json_date
            )
        st.session_state[RESULT_KEY] = evidence
    except SecFilingConfigurationError:
        message = (
            "SEC lookup configuration unavailable. Set a valid EDGE_SEC_USER_AGENT "
            "on the server with an identifying name and monitored contact email, "
            "and install the project's optional [sec] extra in that environment. "
            "Restart the dashboard with that environment when ready."
        )
        st.session_state[ERROR_KEY] = message
    except SecFilingInputError:
        st.session_state[ERROR_KEY] = (
            "Invalid lookup inputs. Use a US ticker (1–5 letters, optional share class), "
            "at least one allowed form, and inclusive dates from 1993-01-01. "
            "The start cannot be in the future or after the end."
        )
    except SecFilingNotFoundError:
        st.session_state[ERROR_KEY] = (
            "SEC identity or filings not found. Check the ticker and SEC coverage; "
            "no validated result is available for this attempt."
        )
    except SecFilingDataError:
        st.session_state[ERROR_KEY] = (
            "SEC metadata failed validation. No result or evidence download is available. "
            "Do not infer company identity or filing coverage from this attempt."
        )
    except SecFilingProviderError:
        st.session_state[ERROR_KEY] = (
            "SEC provider request failed or timed out. Check connectivity and provider "
            "availability, then explicitly submit again; no automatic retry is made."
        )
    except (ImportError, OSError):
        st.session_state[ERROR_KEY] = (
            "SEC provider unavailable. Check the server's optional [sec] installation "
            "and connectivity, then explicitly submit again."
        )
    except Exception:  # noqa: BLE001 - prevent SDK exceptions leaking contact-bearing headers.
        # SDK/serialization failures can contain request headers or contact details.
        # Never pass exception text or a traceback to browser/session evidence.
        st.session_state[ERROR_KEY] = (
            "SEC lookup could not produce safe metadata evidence. No result is available "
            "for this attempt. Check the server installation before submitting again."
        )


def _filing_charts(filings: list[dict]) -> None:
    """Filing dates exactly as returned; counting only, nothing inferred."""
    events = [
        {"lane": f["form_type"], "when": f["filing_date"],
         "label": f"{f['form_type']} · {f['accession_number']}"}
        for f in filings
    ]
    left, right = st.columns([1.4, 1], gap="small")
    with left:
        charts.show(charts.timeline(events, title="Filing timeline · lane = exact form type"),
                    alt_text="Timeline of returned SEC filings by filing date, one lane per form type")
    with right:
        charts.show(charts.step_area(events, when="when", series="lane", y_title="Filings",
                                     title="Cumulative filings by form"),
                    alt_text="Stepped cumulative count of returned filings per form over filing date")


def _no_result() -> None:
    st.caption("No validated SEC result in this session ? nothing is shown or inferred.")


def _identity(result: dict | None, error: str | None) -> None:
    """Submitted lookup / validated identity, or the latest failure / empty state."""
    if error is not None:
        st.error(error)
        st.info(
            "The latest attempt failed. No previous successful result is shown as current."
        )
        return
    if result is None:
        empty(
            "No SEC lookup in this session. Choose a ticker and filters, then select "
            "Look up SEC filings to request metadata.",
            hint="Results appear here as tiles, a filing timeline and a tidy table.",
        )
        return
    identity = result["identity"]
    filings = result["filings"]
    tiles([
        Tile("Returned filings", str(len(filings)), "Match submitted forms/window", "amber"),
        Tile("Provider records", str(result["provider_record_count"]), "Recent submissions", "ink"),
        Tile("Excluded", str(result["excluded_record_count"]), "By forms / date window", "muted"),
        Tile("Coverage start", str(result["coverage_start"] or UNAVAILABLE), "Provider response", "cyan"),
        Tile("Coverage end", str(result["coverage_end"] or UNAVAILABLE), "Provider response", "cyan"),
        Tile("Coverage complete", "No" if result["coverage_may_be_incomplete"] else "Not flagged",
             "May be incomplete" if result["coverage_may_be_incomplete"] else "Provider flag only",
             "amber" if result["coverage_may_be_incomplete"] else "green"),
    ], columns=6)
    kv_grid([(name.replace("_", " ").capitalize(), value if value is not None else UNAVAILABLE)
             for name, value in identity.items()], columns=2)
    st.caption(
        f"Submitted ticker: {identity['requested_ticker']} ? "
        f"Exact forms: {', '.join(result['requested_form_types'])} ? "
        f"Inclusive filing-date window: {result['start_date'] or 'Unbounded'} "
        f"through {result['end_date'] or 'Unbounded'}"
    )
    st.caption(
        "This result belongs to the submitted filters above, not any draft edits. "
        "It is an as-of retrieval snapshot, not an automatically refreshed feed."
    )


def _filings(result: dict | None, ctx: PanelContext) -> None:
    """Filings / response coverage. Views only choose charts, table or both."""
    if result is None:
        _no_result()
        return
    filings = result["filings"]
    st.caption(
        f"Provider response coverage: {result['coverage_start']} through {result['coverage_end']} ? "
        f"Coverage may be incomplete: {'Yes' if result['coverage_may_be_incomplete'] else 'No'}"
    )
    if result["coverage_may_be_incomplete"]:
        st.warning(
            "Coverage is limited to the provider's recent submissions response. "
            "Earlier filings may be absent; this is not a complete filing history."
        )
    for warning in result["warnings"]:
        st.warning(warning)
    if filings:
        if ctx.view != "table":
            _filing_charts(filings)
        st.caption(
            "Missing report dates, acceptance timestamps and primary documents remain "
            "unavailable (blank); they are never inferred. SEC URLs are plain text for "
            "selection and copying; EDGE does not open external pages."
        )
        if ctx.view != "charts":
            st.dataframe(
                filings,
                hide_index=True,
                width="stretch",
                column_order=[name for name in _COLUMN_ORDER if name in filings[0]]
                + [name for name in filings[0] if name not in _COLUMN_ORDER],
                column_config={
                    "filing_index_url": st.column_config.TextColumn(
                        "SEC filing index URL"
                    ),
                    "primary_document_url": st.column_config.TextColumn(
                        "SEC primary document URL"
                    ),
                },
            )
    else:
        st.info(
            "No filings match these submitted forms and filing-date bounds. "
            "Review coverage above, change the filters, and explicitly submit again."
        )
    st.caption(
        "Financial-statement facts are not part of this metadata lookup, so no "
        "fact trend lines are drawn. Nothing is estimated or back-filled."
    )


def _provenance(result: dict | None, evidence: str | None) -> None:
    if result is None or evidence is None:
        _no_result()
        return
    kv_grid([
        (name.replace("_", " ").capitalize(),
         ", ".join(value) if isinstance(value, list) else
         UNAVAILABLE if value is None else value)
        for name, value in result["provenance"].items()
    ], columns=2)
    st.caption(
        "Provider/framework versions, source endpoints and UTC retrieval start/end are "
        "recorded above. Configuration status is a boolean only; no contact is retained. "
        "This session result is not production acceptance or live-provider verification."
    )
    with st.expander("JSON evidence ? session only"):
        st.json(evidence)
    st.download_button(
        "Download SEC metadata evidence (JSON)",
        data=evidence,
        file_name=f"edge-sec-{result['identity']['requested_ticker']}.json",
        mime="application/json",
        key="edge_sec_download",
        on_click="ignore",
    )


def _status() -> None:
    """Session-only lookup status lights; reads session state, never the network."""
    configured = "unknown"
    if RESULT_KEY in st.session_state:
        state, detail = "ok", "Validated snapshot held in this session"
        configured = "ok"
    elif ERROR_KEY in st.session_state:
        state, detail = "bad", "Latest attempt failed ? no result shown"
    else:
        state, detail = "off", "No lookup submitted this session"
    status_board([
        status_light("SEC lookup", state, detail),
        status_light("Server configuration", "ok" if configured == "ok" else "off",
                     "Confirmed by last success" if configured == "ok" else "Validated on submit only"),
        status_light("Automatic refresh", "off", "Never ? explicit submit only"),
        status_light("Disk persistence", "off", "None ? session memory only"),
    ])


def _head() -> None:
    st.warning(
        "FILING METADATA ONLY ? not prices, financial statements or simulation input. "
        "Optional external lookup; no production acceptance or live verification is implied."
    )
    st.caption(
        "Requests occur only on Look up SEC filings. Navigation, edits, reruns and downloads "
        "never request data. Results stay in this session only; no disk, database or cache writes."
    )
    _status()


def _lookup() -> None:
    with st.form("edge_sec_lookup", enter_to_submit=False):
        ticker_col, forms_col = st.columns([1, 2], gap="small")
        ticker = ticker_col.text_input(
            "US ticker", key="edge_sec_ticker", placeholder="US symbol"
        )
        forms = forms_col.multiselect(
            "Exact SEC forms",
            options=sorted(ALLOWED_FORM_TYPES),
            default=list(DEFAULT_FORM_TYPES),
            key="edge_sec_forms",
        )
        st.caption(
            "Optional bounds use YYYY-MM-DD and filter filing dates, not report or "
            "acceptance dates. Leave blank for an unbounded side. End dates may be future."
        )
        start_col, end_col = st.columns(2, gap="small")
        start = start_col.text_input("Start filing date (inclusive)", key="edge_sec_start")
        end = end_col.text_input("End filing date (inclusive)", key="edge_sec_end")
        submitted = st.form_submit_button("Look up SEC filings", type="primary")
    if submitted:
        _submit(ticker, forms, start, end)
        # Panels can be arranged in any order, so result panels may already have
        # drawn this run; rerun once so every panel shows the new attempt only.
        st.rerun()


def _limits() -> None:
    kv_grid([
        ("Source", "SEC EDGAR recent submissions"),
        ("Framework", "OpenBB v5"),
        ("Timeliness", "Delayed metadata"),
        ("Statements", "Not included"),
    ], columns=1)
    with st.expander("Server setup / source limitations"):
        st.write(
            "The server operator must install the optional [sec] extra and set "
            "EDGE_SEC_USER_AGENT in the dashboard process environment to an SEC-compliant "
            "identifying name and monitored contact email. Never enter contact details here. "
            "See README ? Optional SEC filing metadata. Configuration is validated on submit."
        )
        st.caption(
            "OpenBB v5 / SEC EDGAR recent submissions only; delayed filing metadata, "
            "not a real-time feed. Offline tests do not establish live SEC availability."
        )


def panels() -> list[Panel]:
    """SEC panels. Session results are read once, before layout, so any
    arrangement shows the same attempt; a submit reruns before results draw."""
    error = st.session_state.get(ERROR_KEY)
    evidence = None if error is not None else st.session_state.get(RESULT_KEY)
    result = json.loads(evidence) if evidence is not None else None
    if result is not None:
        incomplete = result["coverage_may_be_incomplete"]
        id_meta = f"Retrieved {result['provenance']['retrieved_at_end']}"
        fil_meta = (f"Returned: {len(result['filings'])} ? Provider records: "
                    f"{result['provider_record_count']} ? "
                    f"Excluded by forms/date window: {result['excluded_record_count']}")
    else:
        incomplete, id_meta, fil_meta = False, "", ""
    return [
        Panel("head", "SEC", "SEC filing research", lambda c: _head(), w=12, h=6, min_w=4,
              fit=True, meta="Explicit lookups only", badges=("sec", "display")),
        Panel("lookup", "QRY", "Lookup", lambda c: _lookup(), w=7, h=9, min_w=4, fit=True,
              meta="Filters filing dates only"),
        Panel("limits", "LIM", "Source limitations", lambda c: _limits(), w=5, h=9, min_w=3,
              fit=True, badges=("stale",)),
        Panel("identity", "ID", "Submitted lookup / validated identity",
              lambda c: _identity(result, error), w=12, h=8, min_w=4, fit=True,
              meta=id_meta, badges=("sec",)),
        Panel("filings", "FIL", "Filings / response coverage",
              lambda c: _filings(result, c), w=12, h=14, min_w=4, fit=True, meta=fil_meta,
              badges=("sec",) + (("stale",) if incomplete else ()),
              views=(("both", "Charts + table"), ("table", "Table"), ("charts", "Charts"))),
        Panel("provenance", "SRC", "Retrieval provenance / evidence",
              lambda c: _provenance(result, evidence), w=12, h=8, min_w=4, fit=True,
              badges=("sec",)),
    ]


def render() -> None:
    grid("sec", panels())

"""Session-only SEC metadata presentation. No provider import or disk persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime

import streamlit as st

from edge.data import ALLOWED_FORM_TYPES, DEFAULT_FORM_TYPES

RESULT_KEY = "edge_sec_evidence"
ERROR_KEY = "edge_sec_error"


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


def _results(evidence: str) -> None:
    result = json.loads(evidence)
    identity = result["identity"]
    st.header("Submitted lookup / validated identity")
    st.write(identity)
    st.caption(
        f"Submitted ticker: {identity['requested_ticker']} · "
        f"Exact forms: {', '.join(result['requested_form_types'])} · "
        f"Inclusive filing-date window: {result['start_date'] or 'Unbounded'} "
        f"through {result['end_date'] or 'Unbounded'}"
    )
    st.caption(
        "This result belongs to the submitted filters above, not any draft edits. "
        "It is an as-of retrieval snapshot, not an automatically refreshed feed."
    )
    st.header("Filings / response coverage")
    st.write(
        f"Returned: {len(result['filings'])} · Provider records: {result['provider_record_count']} · "
        f"Excluded by forms/date window: {result['excluded_record_count']}"
    )
    st.caption(
        f"Provider response coverage: {result['coverage_start']} through {result['coverage_end']} · "
        f"Coverage may be incomplete: {'Yes' if result['coverage_may_be_incomplete'] else 'No'}"
    )
    if result["coverage_may_be_incomplete"]:
        st.warning(
            "Coverage is limited to the provider's recent submissions response. "
            "Earlier filings may be absent; this is not a complete filing history."
        )
    for warning in result["warnings"]:
        st.warning(warning)
    if result["filings"]:
        st.caption(
            "Missing report dates, acceptance timestamps and primary documents remain "
            "unavailable (blank); they are never inferred. SEC links open external documents."
        )
        st.dataframe(
            result["filings"],
            hide_index=True,
            width="stretch",
            column_config={
                "filing_index_url": st.column_config.LinkColumn(
                    "SEC filing index", display_text="Open SEC index"
                ),
                "primary_document_url": st.column_config.LinkColumn(
                    "SEC primary document", display_text="Open SEC document"
                ),
            },
        )
    else:
        st.info(
            "No filings match these submitted forms and filing-date bounds. "
            "Review coverage above, change the filters, and explicitly submit again."
        )
    st.header("Retrieval provenance / evidence")
    st.write(result["provenance"])
    st.caption(
        "Provider/framework versions, source endpoints and UTC retrieval start/end are "
        "recorded above. Configuration status is a boolean only; no contact is retained. "
        "This session result is not production acceptance or live-provider verification."
    )
    with st.expander("JSON evidence · session only"):
        st.json(evidence)
    st.download_button(
        "Download SEC metadata evidence (JSON)",
        data=evidence,
        file_name=f"edge-sec-{identity['requested_ticker']}.json",
        mime="application/json",
        key="edge_sec_download",
        on_click="ignore",
    )


def render() -> None:
    st.header("SEC filing research")
    st.warning(
        "FILING METADATA ONLY — not prices, financial statements or simulation input. "
        "Optional external lookup; no production acceptance or live verification is implied."
    )
    st.caption(
        "Requests occur only on Look up SEC filings. Navigation, edits, reruns and downloads "
        "never request data. Results stay in this session only; no disk, database or cache writes."
    )
    with st.expander("Server setup / source limitations"):
        st.write(
            "The server operator must install the optional [sec] extra and set "
            "EDGE_SEC_USER_AGENT in the dashboard process environment to an SEC-compliant "
            "identifying name and monitored contact email. Never enter contact details here. "
            "See README → Optional SEC filing metadata. Configuration is validated on submit."
        )
        st.caption(
            "OpenBB v5 / SEC EDGAR recent submissions only; delayed filing metadata, "
            "not a real-time feed. Offline tests do not establish live SEC availability."
        )
    with st.form("edge_sec_lookup", enter_to_submit=False):
        ticker = st.text_input(
            "US ticker", key="edge_sec_ticker", placeholder="US symbol"
        )
        forms = st.multiselect(
            "Exact SEC forms",
            options=sorted(ALLOWED_FORM_TYPES),
            default=list(DEFAULT_FORM_TYPES),
            key="edge_sec_forms",
        )
        st.caption(
            "Optional bounds use YYYY-MM-DD and filter filing dates, not report or "
            "acceptance dates. Leave blank for an unbounded side. End dates may be future."
        )
        start = st.text_input("Start filing date (inclusive)", key="edge_sec_start")
        end = st.text_input("End filing date (inclusive)", key="edge_sec_end")
        submitted = st.form_submit_button("Look up SEC filings", type="primary")
    if submitted:
        _submit(ticker, forms, start, end)
    if ERROR_KEY in st.session_state:
        st.error(st.session_state[ERROR_KEY])
        st.info(
            "The latest attempt failed. No previous successful result is shown as current."
        )
    elif RESULT_KEY in st.session_state:
        _results(st.session_state[RESULT_KEY])
    else:
        st.info(
            "No SEC lookup in this session. Choose a ticker and filters, then select "
            "Look up SEC filings to request metadata."
        )

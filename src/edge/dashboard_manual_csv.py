"""Native manual-only CSV selection, complete validation and ephemeral preview."""

# ruff: noqa: BLE001 -- sanitize all native/file errors before presentation

from dataclasses import dataclass

import streamlit as st

from edge.dashboard_manual import failure, readout
from edge.data.manual_observations import (
    FUNDAMENTAL_CSV_HEADERS,
    FX_CSV_HEADERS,
    MAX_CSV_BYTES,
    PRICE_CSV_HEADERS,
    ManualDataDeclaration,
    ManualObservationError,
    parse_fundamental_csv,
    parse_fx_csv,
    parse_price_csv,
)
from edge.native_dialogs_manual import choose, read_csv
from edge.portfolio import Instrument


@dataclass(frozen=True)
class ImportedPreview:
    kind: str
    observations: tuple
    instrument: Instrument | None
    exchange: str
    company_name: str


def render_import(prefix: str, kinds: tuple[str, ...]) -> ImportedPreview | None:
    st.subheader("Import user-authored CSV / session preview")
    st.caption(
        "Native file selection only. UTF-8, at most 1 MiB / 5,000 rows / 256 characters "
        "per field. All rows validate before a preview replaces the previous import. "
        "No persistence, vendor retention, provider access, automatic application or ranking."
    )
    kind = st.selectbox("CSV observation type", kinds, key=f"{prefix}_kind")
    headers = {"Prices": PRICE_CSV_HEADERS, "FX": FX_CSV_HEADERS,
               "Fundamentals": FUNDAMENTAL_CSV_HEADERS}[kind]
    with st.expander("Exact CSV schema / required header names"):
        st.text(",".join(headers))
        st.caption(
            "Each row source_origin must be user-authored. as_of / declared_at need "
            "explicit UTC offsets. GBP/GBp/USD units and identity must match your declarations. "
            "Blank/unknown fundamental periods or statuses are not silently inferred."
        )
        st.download_button(
            "Save empty CSV header template…", ",".join(headers) + "\n",
            file_name=f"edge-manual-{kind.lower()}-headers.csv", mime="text/csv",
            on_click="ignore", key=f"{prefix}_headers_{kind}",
        )
    with st.form(f"{prefix}_form", enter_to_submit=False):
        if kind != "FX":
            symbol = st.text_input("CSV ticker / user-declared", max_chars=32)
            market = st.selectbox("CSV declared market", ["UK", "US"])
            currency = st.selectbox("CSV declared currency", ["GBP", "USD"])
            exchange = st.text_input("CSV exchange / exact declared label", max_chars=200)
            company = st.text_input("CSV company name / user-declared", max_chars=200)
        authored = st.checkbox("I authored these observations; this is not a vendor export", value=False)
        retention = st.checkbox("I declare permission for local manual-data use", value=False)
        selected = st.form_submit_button("Choose native CSV and validate all rows…", type="primary")
    if selected:
        if not (authored and retention):
            st.error("Import not started. Both manual authorship and local-use declarations are required.")
        else:
            try:
                instrument = Instrument(symbol.strip().upper(), market, currency) if kind != "FX" else None
                if kind != "FX" and not company.strip():
                    raise ValueError
                path = choose("csv")
                if path is not None:
                    content = read_csv(path, max_bytes=MAX_CSV_BYTES)
                    declaration = ManualDataDeclaration("user-authored", authored, retention)
                    if kind == "FX":
                        observations = parse_fx_csv(content, declaration=declaration)
                    else:
                        parser = parse_price_csv if kind == "Prices" else parse_fundamental_csv
                        observations = parser(
                            content, instrument=instrument, exchange=exchange, declaration=declaration,
                        )
                    preview = ImportedPreview(
                        kind, observations, instrument, exchange if kind != "FX" else "",
                        company if kind != "FX" else "",
                    )
                    st.session_state[f"{prefix}_preview"] = preview
                    st.success("All rows validated as manual declarations. Preview only; not applied.")
            except ManualObservationError as error:
                st.error(f"Manual CSV rejected: {error.code}. Review the exact schema and declarations.")
                st.caption("No partial import applied. Any previous preview remains its previous validated import.")
            except Exception:
                failure("Native CSV import")
    preview = st.session_state.get(f"{prefix}_preview")
    if preview is not None:
        st.caption(f"Last fully validated import: {preview.kind} / {len(preview.observations)} rows / session-only")
        with st.expander("Imported observations / source, dates, units and field provenance"):
            for observation in preview.observations[:20]:
                readout(observation)
            if len(preview.observations) > 20:
                st.caption("Preview shows first 20 rows; all rows were validated. Choose a row below to inspect/apply.")
    return preview

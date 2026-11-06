"""Deterministic fictional data for engine tests, never a market-data fallback."""

from edge.domain import Dataset
from edge.validation import validate_dataset


def demo_dataset() -> Dataset:
    return validate_dataset({
        "format": "edge.quote-dataset",
        "version": 1,
        "instrument_id": "EDGE_TEST.SIM",
        "company_name": "EDGE Fictional Acceptance Instrument (not a listed company)",
        "currency": "USD",
        "price_unit": "USD",
        "source": "EDGE synthetic acceptance fixture v1; not financial market data",
        "retrieved_at": "2026-11-05T00:00:00Z",
        "synthetic": True,
        "corporate_action_status": "not-applicable-synthetic",
        "quotes": [
            {
                "timestamp_ns": 1704205800000000000 + index * 60_000_000_000,
                "bid": f"{99 + index}.90",
                "ask": f"{100 + index}.10",
                "bid_size": 1000,
                "ask_size": 1000,
            }
            for index in range(6)
        ],
    })

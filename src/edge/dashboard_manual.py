"""Shared literal rendering and exact manual-input parsing; no provider access."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

import streamlit as st

from edge.decision_contracts import DecisionInputError


def decimal_input(value: str) -> Decimal:
    try:
        result = Decimal(value.strip())
        if not result.is_finite() or len(value) > 64:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise DecisionInputError("Enter an exact finite decimal amount.") from None


def timestamp_input(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.strip())
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        result = result.astimezone(UTC)
        if result > datetime.now(UTC):
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise DecisionInputError(
            "Use a past ISO timestamp with an explicit UTC offset."
        ) from None


def literal(value):
    if is_dataclass(value):
        return {field.name: literal(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (Decimal, datetime, date)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): literal(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [literal(item) for item in value]
    return value


NOT_RECORDED = "Not recorded"
_MAX_ROWS = 300


def _label(path: tuple[str, ...]) -> str:
    return " › ".join(part.replace("_", " ") for part in path)


def _cell(value) -> str:
    """One-line display text. Newlines are folded so escaped user text can never
    terminate the surrounding HTML block and be re-parsed as Markdown."""
    if value is None:
        return NOT_RECORDED
    text = str(value)
    return " ⏎ ".join(part for part in text.replace("\r", "\n").split("\n")) if text else "—"


def readout_rows(value) -> list[tuple[str, str]]:
    """Flatten ``literal(value)`` into exact label/value pairs (display only)."""
    rows: list[tuple[str, str]] = []

    def walk(item, path: tuple[str, ...]) -> None:
        if isinstance(item, dict):
            if not item:
                rows.append((_label(path) or "value", "None declared"))
            for name, child in item.items():
                walk(child, (*path, name))
        elif isinstance(item, list):
            if not item:
                rows.append((_label(path) or "value", "None declared"))
            elif all(not isinstance(child, (dict, list)) for child in item):
                rows.append((_label(path) or "value", "; ".join(_cell(child) for child in item)))
            else:
                for index, child in enumerate(item, 1):
                    walk(child, (*path[:-1], f"{path[-1] if path else 'item'} #{index}"))
        else:
            rows.append((_label(path) or "value", _cell(item)))

    walk(literal(value), ())
    return rows


def readout(value) -> None:
    """Escaped label/value grid; no user-authored string is parsed as Markdown/HTML."""
    from edge.ui_kit import kv_grid

    data = literal(value)
    if not isinstance(data, (dict, list)):
        st.text(str(data))
        return
    rows = readout_rows(value)
    kv_grid(rows[:_MAX_ROWS], columns=2)
    if len(rows) > _MAX_ROWS:
        st.caption(f"Showing the first {_MAX_ROWS} of {len(rows)} recorded fields.")


def boundary(title: str) -> None:
    st.header(title)
    st.warning(
        "USER-DECLARED INPUTS — not verified identity, prices or investment advice. "
        "No provider requests, broker actions or automatic changes to risk settings."
    )


def failure(action: str) -> None:
    st.error(
        f"{action} failed. Check required fields, exact units, timestamps and local "
        "storage permissions. Nothing was reset; correct the inputs and explicitly retry."
    )

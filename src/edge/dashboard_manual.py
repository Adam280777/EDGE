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


def readout(value) -> None:
    """No user-authored string passes through Markdown or raw HTML."""
    data = literal(value)
    if isinstance(data, dict):
        for name, item in data.items():
            st.text(f"{name}: {item}")
    else:
        st.text(str(data))


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

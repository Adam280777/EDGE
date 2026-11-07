"""Immutable user-declared watchlist models; ticker identity is never verified."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from edge.errors import EdgeError

WatchlistHorizon = Literal["both", "swing", "longterm"]


class WatchlistError(EdgeError):
    """A watchlist operation failed safely."""


class WatchlistInputError(WatchlistError):
    """Watchlist input does not satisfy the public contract."""


class WatchlistNotFoundError(WatchlistError):
    """The requested watchlist item does not exist."""


class WatchlistStorageError(WatchlistError):
    """The watchlist cannot be safely read or written."""


@dataclass(frozen=True)
class WatchlistDraft:
    symbol: str
    company_name: str
    notes: str = ""
    horizon: WatchlistHorizon | None = None


@dataclass(frozen=True)
class WatchlistEntry:
    watchlist_id: str
    created_at: datetime
    provenance: Literal["user-authored"]
    draft: WatchlistDraft


@dataclass(frozen=True)
class WatchlistSummary:
    watchlist_id: str
    created_at: datetime
    symbol: str
    company_name: str
    horizon: WatchlistHorizon | None


def _text(value: object, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise WatchlistInputError(f"{field} must be text.")
    value = value.replace("\r\n", "\n")
    if any(
        unicodedata.category(char) in ("Cc", "Cf", "Cs") and char not in "\n\t"
        for char in value
    ):
        raise WatchlistInputError(f"{field} contains prohibited control characters.")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise WatchlistInputError(
            f"{field} must contain {minimum} to {maximum} characters."
        )
    return value


def validate_draft(draft: WatchlistDraft) -> WatchlistDraft:
    """Return a normalized immutable draft without resolving its declared identity."""
    if not isinstance(draft, WatchlistDraft):
        raise WatchlistInputError("Expected a WatchlistDraft.")
    if draft.horizon is not None and (
        not isinstance(draft.horizon, str)
        or draft.horizon not in ("both", "swing", "longterm")
    ):
        raise WatchlistInputError("horizon must be both, swing, longterm, or omitted.")
    if not isinstance(draft.symbol, str) or not draft.symbol.strip().isascii():
        raise WatchlistInputError("symbol must contain only ASCII characters.")
    symbol = _text(draft.symbol, "symbol", 1, 32).upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9.^:/-]{0,31}", symbol, flags=re.ASCII) is None:
        raise WatchlistInputError(
            "symbol must be an ASCII identifier of at most 32 characters."
        )
    return replace(
        draft,
        symbol=symbol,
        company_name=_text(draft.company_name, "company_name", 1, 200),
        notes=_text(draft.notes, "notes", 0, 10000),
    )


def normalize_created_at(value: object) -> datetime:
    """Validate a timezone-aware timestamp and normalize it to UTC."""
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise WatchlistInputError("created_at must be a timezone-aware datetime.")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise WatchlistInputError("created_at cannot be represented in UTC.") from None

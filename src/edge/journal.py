"""User-authored research notes, not provider evidence or trading instructions."""

import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import urlsplit

from edge.errors import EdgeError
from edge.validation import canonical_json

Horizon = Literal["swing", "long-term"]


class JournalError(EdgeError):
    """A journal operation failed safely."""


class JournalInputError(JournalError):
    """Journal input does not satisfy the public contract."""


class JournalNotFoundError(JournalError):
    """The requested journal entry does not exist."""


class JournalStorageError(JournalError):
    """The journal cannot be safely read or written."""


@dataclass(frozen=True)
class SourceReference:
    label: str
    url: str | None = None
    source_date: date | None = None


@dataclass(frozen=True)
class JournalDraft:
    horizon: Horizon
    symbol: str
    company_name: str
    title: str
    research_date: date
    thesis: str
    bull_case: str
    bear_case: str
    catalysts: str
    invalidation: str
    sources: tuple[SourceReference, ...] = ()


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    created_at: datetime
    provenance: Literal["user-authored"]
    draft: JournalDraft


@dataclass(frozen=True)
class JournalSummary:
    entry_id: str
    created_at: datetime
    horizon: Horizon
    symbol: str
    title: str
    research_date: date


def _check_id(entry_id: object) -> None:
    if not isinstance(entry_id, str) or re.fullmatch(r"[a-f0-9]{32}", entry_id) is None:
        raise JournalInputError(
            "entry_id must be a lowercase hexadecimal 32-character identifier."
        )


def _check_horizon(horizon: object) -> None:
    if not isinstance(horizon, str) or horizon not in ("swing", "long-term"):
        raise JournalInputError("horizon must be swing or long-term.")


def _text(value: object, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise JournalInputError(f"{field} must be text.")
    value = value.replace("\r\n", "\n")
    if any(
        unicodedata.category(char) in ("Cc", "Cf", "Cs") and char not in "\n\t"
        for char in value
    ):
        raise JournalInputError(f"{field} contains prohibited control characters.")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise JournalInputError(
            f"{field} must contain {minimum} to {maximum} characters."
        )
    return value


def _date(value: object, field: str, today: date) -> date:
    if type(value) is not date or value > today:
        raise JournalInputError(
            f"{field} must be a date, not a datetime, and not in the future."
        )
    return value


def _url(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and any(
        unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value
    ):
        raise JournalInputError("source URL contains prohibited control characters.")
    value = _text(value, "source URL", 0, 2048)
    if not value:
        return None
    if any(char.isspace() for char in value) or "\\" in value:
        raise JournalInputError(
            "source URL must be an absolute HTTPS URL without whitespace."
        )
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        # Accessing port also rejects malformed or out-of-range ports.
        port = parts.port
        if (
            parts.scheme != "https"
            or not hostname
            or parts.username is not None
            or parts.password is not None
            or port == 0
        ):
            raise ValueError
    except ValueError:
        raise JournalInputError(
            "source URL must be an absolute HTTPS URL with a hostname and no credentials."
        ) from None
    return value


def validate_draft(draft: JournalDraft) -> JournalDraft:
    """Return a normalized immutable draft; never fetch or infer company information."""
    if not isinstance(draft, JournalDraft):
        raise JournalInputError("Expected a JournalDraft.")
    _check_horizon(draft.horizon)
    symbol = _text(draft.symbol, "symbol", 1, 32).upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9.^:/-]{0,31}", symbol, flags=re.ASCII) is None:
        raise JournalInputError(
            "symbol must be an ASCII instrument identifier of at most 32 characters."
        )
    # Check before uppercasing too: Unicode characters must not become ASCII aliases.
    if not draft.symbol.strip().isascii():
        raise JournalInputError("symbol must contain only ASCII characters.")
    today = datetime.now(UTC).date()
    research_date = _date(draft.research_date, "research_date", today)
    if not isinstance(draft.sources, tuple) or len(draft.sources) > 20:
        raise JournalInputError(
            "sources must be a tuple containing at most 20 references."
        )
    sources = []
    for source in draft.sources:
        if not isinstance(source, SourceReference):
            raise JournalInputError("sources must contain SourceReference values.")
        sources.append(
            SourceReference(
                label=_text(source.label, "source label", 1, 200),
                url=_url(source.url),
                source_date=(
                    _date(source.source_date, "source_date", today)
                    if source.source_date is not None
                    else None
                ),
            )
        )
    return replace(
        draft,
        symbol=symbol,
        company_name=_text(draft.company_name, "company_name", 1, 200),
        title=_text(draft.title, "title", 1, 200),
        research_date=research_date,
        thesis=_text(draft.thesis, "thesis", 1, 10000),
        bull_case=_text(draft.bull_case, "bull_case", 0, 10000),
        bear_case=_text(draft.bear_case, "bear_case", 0, 10000),
        catalysts=_text(draft.catalysts, "catalysts", 0, 10000),
        invalidation=_text(draft.invalidation, "invalidation", 0, 10000),
        sources=tuple(sources),
    )


def _utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise JournalInputError("created_at must be a timezone-aware datetime.")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise JournalInputError("created_at cannot be represented in UTC.") from None


def _draft_document(draft: JournalDraft) -> dict[str, object]:
    document = asdict(draft)
    document["research_date"] = draft.research_date.isoformat()
    document["sources"] = [
        {
            "label": source.label,
            "url": source.url,
            "source_date": source.source_date.isoformat()
            if source.source_date
            else None,
        }
        for source in draft.sources
    ]
    return document


def export_entry_json(entry: JournalEntry) -> str:
    """Export the exact versioned entry envelope with explicit ISO dates and nulls."""
    if not isinstance(entry, JournalEntry):
        raise JournalInputError("Expected a JournalEntry.")
    _check_id(entry.entry_id)
    if entry.provenance != "user-authored":
        raise JournalInputError("provenance must be user-authored.")
    draft = validate_draft(entry.draft)
    return canonical_json(
        {
            "format": "edge.research-journal-entry",
            "version": 1,
            "entry": {
                "entry_id": entry.entry_id,
                "created_at": _utc(entry.created_at).isoformat(),
                "provenance": entry.provenance,
                "draft": _draft_document(draft),
            },
        }
    )

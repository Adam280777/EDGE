"""Offline contract tests for user-authored research, with no financial fixtures."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from edge.errors import EdgeError
from edge.journal import (
    JournalDraft,
    JournalEntry,
    JournalError,
    JournalInputError,
    JournalNotFoundError,
    JournalStorageError,
    JournalSummary,
    SourceReference,
    export_entry_json,
    validate_draft,
)
from edge.validation import canonical_json


@pytest.fixture
def draft() -> JournalDraft:
    return JournalDraft(
        horizon="swing",
        symbol="note",
        company_name="User-entered company",
        title="Research note",
        research_date=date(2020, 1, 2),
        thesis="A user hypothesis, not verified market data.",
        bull_case="",
        bear_case="",
        catalysts="",
        invalidation="",
    )


def test_normalization_preserves_internal_content(draft: JournalDraft) -> None:
    original = replace(
        draft,
        symbol=" brk.b ",
        title="  My title  ",
        thesis=" \r\nFirst  paragraph.\r\n\tSecond paragraph.\r\n ",
        sources=(SourceReference(" Label ", "  ", date(2020, 1, 1)),),
    )
    normalized = validate_draft(original)
    assert normalized.symbol == "BRK.B"
    assert normalized.title == "My title"
    assert normalized.thesis == "First  paragraph.\n\tSecond paragraph."
    assert normalized.sources == (SourceReference("Label", None, date(2020, 1, 1)),)
    assert original.symbol == " brk.b "
    assert validate_draft(normalized) == normalized


@pytest.mark.parametrize(
    ("field", "minimum", "maximum"),
    [
        ("company_name", 1, 200),
        ("title", 1, 200),
        ("thesis", 1, 10000),
        ("bull_case", 0, 10000),
        ("bear_case", 0, 10000),
        ("catalysts", 0, 10000),
        ("invalidation", 0, 10000),
    ],
)
def test_text_boundaries(
    draft: JournalDraft, field: str, minimum: int, maximum: int
) -> None:
    for length in (minimum, maximum):
        assert (
            getattr(validate_draft(replace(draft, **{field: "x" * length})), field)
            == "x" * length
        )
    for invalid in (None, True, 17, "x" * (maximum + 1)):
        with pytest.raises(JournalInputError):
            validate_draft(replace(draft, **{field: invalid}))
    if minimum:
        with pytest.raises(JournalInputError):
            validate_draft(replace(draft, **{field: " \t\n "}))


@pytest.mark.parametrize(
    "symbol", ["A", "123", "BRK.B", "^INDEX", "A/B", "A:B", "A-B", "A" * 32]
)
def test_symbol_validity(draft: JournalDraft, symbol: str) -> None:
    if symbol.startswith("^"):
        with pytest.raises(JournalInputError):
            validate_draft(replace(draft, symbol=symbol))
    else:
        assert validate_draft(replace(draft, symbol=symbol)).symbol == symbol


@pytest.mark.parametrize(
    "symbol",
    ["", "A" * 33, ".A", "/A", "a b", "A_B", "ß", "ı", "é", "Ａ", None, True, 1],
)
def test_invalid_symbols(draft: JournalDraft, symbol: object) -> None:
    with pytest.raises(JournalInputError):
        validate_draft(replace(draft, symbol=symbol))


@pytest.mark.parametrize("horizon", ["Swing", "long_term", "", None, True, 1])
def test_horizon_is_exact(draft: JournalDraft, horizon: object) -> None:
    with pytest.raises(JournalInputError):
        validate_draft(replace(draft, horizon=horizon))


@pytest.mark.parametrize(
    "control", ["\x00", "\x01", "\r", "\x7f", "\x85", "\u202e", "\ud800"]
)
def test_prohibited_controls_are_not_silently_removed(
    draft: JournalDraft, control: str
) -> None:
    for field in (
        "symbol",
        "company_name",
        "title",
        "thesis",
        "bull_case",
        "bear_case",
        "catalysts",
        "invalidation",
    ):
        with pytest.raises(JournalInputError):
            validate_draft(replace(draft, **{field: f"x{control}y"}))
    with pytest.raises(JournalInputError):
        validate_draft(replace(draft, sources=(SourceReference(f"x{control}y"),)))


@pytest.mark.parametrize(
    "invalid",
    [datetime(2020, 1, 1), "2020-01-01", True, None, date.max],
)
def test_actual_nonfuture_dates_only(draft: JournalDraft, invalid: object) -> None:
    with pytest.raises(JournalInputError):
        validate_draft(replace(draft, research_date=invalid))
    if invalid is not None:
        with pytest.raises(JournalInputError):
            validate_draft(
                replace(draft, sources=(SourceReference("label", source_date=invalid),))
            )


def test_today_is_accepted_in_utc(draft: JournalDraft) -> None:
    today = datetime.now(UTC).date()
    assert validate_draft(replace(draft, research_date=today)).research_date == today


def test_source_boundaries(draft: JournalDraft) -> None:
    sources = (SourceReference("x" * 200),) * 20
    assert validate_draft(replace(draft, sources=sources)).sources == sources
    for sources in (
        (SourceReference("x"),) * 21,
        (SourceReference("x" * 201),),
        (SourceReference(""),),
        (SourceReference(True),),
        ("not a source",),
        [],
        None,
    ):
        with pytest.raises(JournalInputError):
            validate_draft(replace(draft, sources=sources))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test",
        "https://example.test:443/a?q=1#fragment",
        "https://localhost/research",
        "https://[::1]/research",
        "https://example.test/" + "a" * (2048 - len("https://example.test/")),
    ],
)
def test_https_references_are_not_fetched(draft: JournalDraft, url: str) -> None:
    assert (
        validate_draft(replace(draft, sources=(SourceReference("label", url),)))
        .sources[0]
        .url
        == url
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "//example.test",
        "/local",
        "file:///local",
        "https://",
        "https:///path",
        "https://user@example.test",
        "https://user:password@example.test",
        "https://@example.test",
        "https://example.test/a b",
        "https://exam\tple.test",
        "https://example.test/\npath",
        "\nhttps://example.test",
        "https://example.test\r\n",
        "\thttps://example.test\t",
        "https://example.test/\x00",
        "https://example.test:invalid",
        "https://example.test:65536",
        "https://example.test:0",
        "https://[broken",
        "https://example.test\\path",
        "https://example.test/" + "a" * 2048,
        True,
        1,
    ],
)
def test_unsafe_urls_rejected(draft: JournalDraft, url: object) -> None:
    with pytest.raises(JournalInputError):
        validate_draft(replace(draft, sources=(SourceReference("label", url),)))


def test_export_exact_envelope_and_no_implicit_conversions(draft: JournalDraft) -> None:
    entry = JournalEntry(
        "9f89fbf8424a4910ad1a79aef1855964",
        datetime(2020, 1, 2, 4, 5, tzinfo=timezone(timedelta(hours=2))),
        "user-authored",
        replace(draft, sources=(SourceReference("User's source"),)),
    )
    document = json.loads(export_entry_json(entry))
    assert set(document) == {"format", "version", "entry"}
    assert document["format"] == "edge.research-journal-entry"
    assert document["version"] == 1
    assert set(document["entry"]) == {"entry_id", "created_at", "provenance", "draft"}
    assert document["entry"]["created_at"] == "2020-01-02T02:05:00+00:00"
    assert document["entry"]["draft"] == {
        "horizon": "swing",
        "symbol": "NOTE",
        "company_name": draft.company_name,
        "title": draft.title,
        "research_date": "2020-01-02",
        "thesis": draft.thesis,
        "bull_case": "",
        "bear_case": "",
        "catalysts": "",
        "invalidation": "",
        "sources": [{"label": "User's source", "url": None, "source_date": None}],
    }
    assert export_entry_json(entry) == canonical_json(document)
    assert export_entry_json(entry) == export_entry_json(entry)
    for invalid in (
        replace(entry, entry_id="../path"),
        replace(entry, provenance="provider"),
        replace(entry, created_at=datetime(2020, 1, 1)),
        replace(entry, created_at="2020-01-01"),
        None,
    ):
        with pytest.raises(JournalInputError):
            export_entry_json(invalid)


def test_models_are_frozen_and_errors_are_presentable(draft: JournalDraft) -> None:
    entry = JournalEntry("a" * 32, datetime.now(UTC), "user-authored", draft)
    summary = JournalSummary(
        entry.entry_id, entry.created_at, "swing", "NOTE", "Title", date(2020, 1, 1)
    )
    for value, field in (
        (draft, "title"),
        (entry, "entry_id"),
        (summary, "title"),
        (SourceReference("source"), "label"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, "changed")
    assert issubclass(JournalError, EdgeError)
    for error in (JournalInputError, JournalNotFoundError, JournalStorageError):
        assert issubclass(error, JournalError)
    with pytest.raises(JournalInputError):
        validate_draft(None)

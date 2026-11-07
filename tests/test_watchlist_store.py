"""Offline tests for isolated, append-only watchlist storage."""

import re
import socket
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from edge.watchlist import (
    WatchlistDraft,
    WatchlistInputError,
    WatchlistNotFoundError,
    WatchlistStorageError,
    validate_draft,
)
from edge.watchlist_store import APPLICATION_ID, SCHEMA_VERSION, WatchlistStore

NOW = datetime(2026, 11, 7, 14, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def draft() -> WatchlistDraft:
    return WatchlistDraft(
        symbol=" abcd ",
        company_name="User-declared <Company>",
        notes="Local note: literal **text** and <script> tags.",
        horizon="longterm",
    )


@pytest.fixture
def store(tmp_path: Path) -> WatchlistStore:
    return WatchlistStore(tmp_path / "data", clock=lambda: NOW)


def test_missing_reads_never_create_files(store: WatchlistStore) -> None:
    assert store.list_entries() == []
    with pytest.raises(WatchlistNotFoundError):
        store.get("a" * 32)
    assert not store.root.exists()


def test_round_trip_separate_database_and_no_network(
    store: WatchlistStore,
    draft: WatchlistDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("A local watchlist must never call a network/provider.")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    entry = store.create(draft)
    assert re.fullmatch(r"[a-f0-9]{32}", entry.watchlist_id)
    assert UUID(entry.watchlist_id).version == 4
    assert entry.created_at == NOW
    assert entry.provenance == "user-authored"
    assert entry.draft == validate_draft(draft)
    with pytest.raises(FrozenInstanceError):
        entry.draft.symbol = "OTHER"  # type: ignore[misc]
    assert WatchlistStore(store.root).get(entry.watchlist_id) == entry
    summaries = store.list_entries()
    assert len(summaries) == 1
    assert summaries[0].watchlist_id == entry.watchlist_id
    assert summaries[0].symbol == "ABCD"
    assert summaries[0].horizon == "longterm"
    assert not (store.root / "research-journal.sqlite3").exists()
    assert {path.name for path in store.root.iterdir()} == {"watchlist.sqlite3"}
    with sqlite3.connect(store.database) as connection:
        assert (
            connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")


@pytest.mark.parametrize(
    "draft",
    [
        WatchlistDraft("", "Company"),
        WatchlistDraft("☃", "Company"),
        WatchlistDraft("GOOD", ""),
        WatchlistDraft("GOOD", "Company", "bad\x00note"),
        WatchlistDraft("GOOD", "Company", horizon="Long-term"),  # type: ignore[arg-type]
        WatchlistDraft("GOOD", "Company", horizon="long-term"),  # type: ignore[arg-type]
        WatchlistDraft("GOOD", "Company", notes="x" * 10001),
        WatchlistDraft("GOOD", "Company", notes=42),  # type: ignore[arg-type]
    ],
)
def test_invalid_create_does_not_initialize(
    store: WatchlistStore, draft: WatchlistDraft
) -> None:
    with pytest.raises(WatchlistInputError):
        store.create(draft)
    assert not store.root.exists()


def test_exact_horizons_and_normalization() -> None:
    assert validate_draft(WatchlistDraft(" ab ", " Name ", horizon="both")) == (
        WatchlistDraft("AB", "Name", "", "both")
    )
    assert (
        validate_draft(WatchlistDraft("ab", "Name", horizon="swing")).horizon == "swing"
    )
    assert (
        validate_draft(WatchlistDraft("ab", "Name", horizon="longterm")).horizon
        == "longterm"
    )


def test_clock_is_sampled_once_and_normalized_to_utc(
    store: WatchlistStore, draft: WatchlistDraft
) -> None:
    calls = []

    def clock() -> datetime:
        calls.append(True)
        return NOW.astimezone(timezone(timedelta(hours=-7)))

    entry = WatchlistStore(store.root, clock=clock).create(draft)
    assert calls == [True]
    assert entry.created_at.tzinfo == UTC
    assert entry.created_at == NOW
    assert store.get(entry.watchlist_id) == entry


@pytest.mark.parametrize(
    "watchlist_id",
    ["", "a" * 31, "A" * 32, "g" * 32, "../file", "' OR 1=1", True, None, 1],
)
def test_bad_ids_fail_without_io(store: WatchlistStore, watchlist_id: object) -> None:
    with pytest.raises(WatchlistInputError):
        store.get(watchlist_id)  # type: ignore[arg-type]
    assert not store.root.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.0},
        {"limit": "1"},
        {"offset": -1},
        {"offset": True},
        {"offset": 1.0},
        {"offset": None},
    ],
)
def test_pagination_validation_without_io(
    store: WatchlistStore, arguments: dict[str, object]
) -> None:
    with pytest.raises(WatchlistInputError):
        store.list_entries(**arguments)  # type: ignore[arg-type]
    assert not store.root.exists()


def test_append_only_order_and_pagination(
    store: WatchlistStore, draft: WatchlistDraft
) -> None:
    entries = [
        store.create(replace(draft, company_name=f"Company {index}"))
        for index in range(5)
    ]
    expected = sorted(
        entries,
        key=lambda entry: (entry.created_at, entry.watchlist_id),
        reverse=True,
    )
    assert [row.watchlist_id for row in store.list_entries()] == [
        entry.watchlist_id for entry in expected
    ]
    assert [row.watchlist_id for row in store.list_entries(limit=2, offset=1)] == [
        entry.watchlist_id for entry in expected[1:3]
    ]
    earlier = WatchlistStore(
        store.root, clock=lambda: NOW - timedelta(seconds=1)
    ).create(draft)
    assert store.list_entries()[-1].watchlist_id == earlier.watchlist_id
    with pytest.raises(WatchlistNotFoundError):
        store.get("f" * 32)


@pytest.mark.parametrize(
    "kind", ["empty", "foreign", "corrupt", "wrong-version", "wrong-app"]
)
def test_unrecognized_databases_are_untouched(
    store: WatchlistStore, draft: WatchlistDraft, kind: str
) -> None:
    store.root.mkdir()
    if kind == "empty":
        store.database.touch()
    elif kind == "foreign":
        with sqlite3.connect(store.database) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
    elif kind == "corrupt":
        store.database.write_bytes(b"not sqlite")
    else:
        with sqlite3.connect(store.database) as connection:
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            from edge.watchlist_store import _SCHEMA

            connection.execute(_SCHEMA)
            if kind == "wrong-version":
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
            else:
                connection.execute(f"PRAGMA application_id={APPLICATION_ID + 1}")
    before = store.database.read_bytes()
    with pytest.raises(WatchlistStorageError):
        store.list_entries()
    with pytest.raises(WatchlistStorageError):
        store.create(draft)
    assert store.database.read_bytes() == before
    assert {path.name for path in store.root.iterdir()} == {"watchlist.sqlite3"}


def test_existing_reads_are_byte_preserving(
    store: WatchlistStore, draft: WatchlistDraft
) -> None:
    entry = store.create(draft)
    before = store.database.read_bytes()
    paths = set(store.root.iterdir())
    store.get(entry.watchlist_id)
    store.list_entries()
    assert store.database.read_bytes() == before
    assert set(store.root.iterdir()) == paths

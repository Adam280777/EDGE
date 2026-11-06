"""SQLite lifecycle, integrity and append-only contract tests; entirely offline."""

import json
import re
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

import edge.journal_store as storage
from edge.journal import (
    JournalDraft,
    JournalInputError,
    JournalNotFoundError,
    JournalStorageError,
    SourceReference,
    export_entry_json,
    validate_draft,
)
from edge.journal_store import APPLICATION_ID, SCHEMA_VERSION, JournalStore

NOW = datetime(2021, 2, 3, 4, 5, 6, 123456, tzinfo=UTC)


@pytest.fixture
def draft() -> JournalDraft:
    return JournalDraft(
        "long-term",
        " note ",
        "User-entered company",
        "Thesis '); DROP TABLE journal_entries; --",
        date(2020, 1, 2),
        "A hypothesis\r\nwith an internal  space.",
        "Bull",
        "Bear",
        "",
        "",
        (
            SourceReference("Personal notes"),
            SourceReference("Source", "https://example.test", date(2020, 1, 1)),
        ),
    )


@pytest.fixture
def store(tmp_path: Path) -> JournalStore:
    return JournalStore(tmp_path / "data", clock=lambda: NOW)


def test_missing_reads_never_create_files(store: JournalStore) -> None:
    assert not store.root.exists()
    assert store.list_entries() == []
    with pytest.raises(JournalNotFoundError):
        store.get("a" * 32)
    assert not store.root.exists()


def test_round_trip_separate_database_and_no_network(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("A local journal must never call a network/provider.")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    entry = store.create(draft)
    assert re.fullmatch("[a-f0-9]{32}", entry.entry_id)
    assert UUID(entry.entry_id).version == 4
    assert entry.created_at == NOW
    assert entry.provenance == "user-authored"
    assert entry.draft == validate_draft(draft)
    assert JournalStore(store.root).get(entry.entry_id) == entry
    summaries = store.list_entries()
    assert len(summaries) == 1
    assert summaries[0].entry_id == entry.entry_id
    assert summaries[0].symbol == "NOTE"
    assert summaries[0].horizon == "long-term"
    assert not (store.root / "edge.sqlite3").exists()
    assert {path.name for path in store.root.iterdir()} == {"research-journal.sqlite3"}
    with sqlite3.connect(store.database) as connection:
        assert (
            connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = connection.execute("SELECT sources_json FROM journal_entries").fetchone()[
            0
        ]
        assert json.loads(row)[0] == {
            "label": "Personal notes",
            "url": None,
            "source_date": None,
        }
    assert export_entry_json(entry) == export_entry_json(store.get(entry.entry_id))
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")


def test_invalid_create_and_naive_clock_never_initialize(
    store: JournalStore, draft: JournalDraft
) -> None:
    with pytest.raises(JournalInputError):
        store.create(replace(draft, thesis=""))
    assert not store.root.exists()
    for value in (datetime(2020, 1, 1), None, "not a datetime"):
        with pytest.raises(JournalInputError):
            JournalStore(store.root, clock=lambda value=value: value).create(draft)
        assert not store.root.exists()


def test_clock_once_and_utc_normalization(
    store: JournalStore, draft: JournalDraft
) -> None:
    calls = []

    def clock() -> datetime:
        calls.append(True)
        return NOW.astimezone(timezone(timedelta(hours=-7)))

    entry = JournalStore(store.root, clock=clock).create(draft)
    assert calls == [True]
    assert entry.created_at.tzinfo == UTC
    assert store.get(entry.entry_id) == entry


@pytest.mark.parametrize(
    "entry_id", ["", "a" * 31, "A" * 32, "g" * 32, "../file", "' OR 1=1", True, None, 1]
)
def test_malformed_id_is_input_error_without_io(
    store: JournalStore, entry_id: object
) -> None:
    with pytest.raises(JournalInputError):
        store.get(entry_id)
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
        {"horizon": "Swing"},
        {"horizon": True},
        {"horizon": 1},
    ],
)
def test_pagination_validation_without_io(
    store: JournalStore, arguments: dict[str, object]
) -> None:
    with pytest.raises(JournalInputError):
        store.list_entries(**arguments)
    assert not store.root.exists()


def test_deterministic_order_filter_and_pagination(
    store: JournalStore, draft: JournalDraft
) -> None:
    entries = [
        store.create(replace(draft, horizon="swing" if i % 2 else "long-term"))
        for i in range(5)
    ]
    expected = sorted(
        entries, key=lambda entry: (entry.created_at, entry.entry_id), reverse=True
    )
    assert [row.entry_id for row in store.list_entries()] == [
        entry.entry_id for entry in expected
    ]
    assert [row.entry_id for row in store.list_entries(limit=2, offset=1)] == [
        entry.entry_id for entry in expected[1:3]
    ]
    assert [
        row.entry_id for row in store.list_entries(horizon="swing", limit=1, offset=1)
    ] == [entry.entry_id for entry in expected if entry.draft.horizon == "swing"][1:2]
    assert store.list_entries(limit=100, offset=2**100) == []
    earlier = JournalStore(store.root, clock=lambda: NOW - timedelta(seconds=1)).create(
        draft
    )
    assert store.list_entries()[-1].entry_id == earlier.entry_id
    with pytest.raises(JournalNotFoundError):
        store.get("f" * 32)


def test_zero_fraction_and_nonzero_fraction_order_correctly(
    store: JournalStore, draft: JournalDraft
) -> None:
    first = JournalStore(store.root, clock=lambda: NOW.replace(microsecond=0)).create(
        draft
    )
    second = JournalStore(store.root, clock=lambda: NOW.replace(microsecond=1)).create(
        draft
    )
    assert [row.entry_id for row in store.list_entries()] == [
        second.entry_id,
        first.entry_id,
    ]


def test_existing_read_is_byte_preserving(
    store: JournalStore, draft: JournalDraft
) -> None:
    entry = store.create(draft)
    before = store.database.read_bytes()
    paths = set(store.root.iterdir())
    store.get(entry.entry_id)
    store.list_entries()
    assert store.database.read_bytes() == before
    assert set(store.root.iterdir()) == paths


@pytest.mark.parametrize(
    "kind", ["empty", "foreign", "corrupt", "wrong-version", "wrong-app"]
)
def test_unrecognized_databases_untouched(
    store: JournalStore, draft: JournalDraft, kind: str
) -> None:
    store.root.mkdir()
    if kind == "empty":
        store.database.touch()
    elif kind == "corrupt":
        store.database.write_bytes(b"not a sqlite database")
    elif kind == "foreign":
        with sqlite3.connect(store.database) as connection:
            connection.execute("CREATE TABLE private_data (content TEXT)")
            connection.execute("INSERT INTO private_data VALUES ('preserve me')")
    else:
        store.create(draft)
        with sqlite3.connect(store.database) as connection:
            connection.execute(
                "PRAGMA user_version=2"
                if kind == "wrong-version"
                else "PRAGMA application_id=1"
            )
    before = store.database.read_bytes()
    paths = set(store.root.iterdir())
    for operation in (
        store.list_entries,
        lambda: store.get("a" * 32),
        lambda: store.create(draft),
    ):
        with pytest.raises(JournalStorageError):
            operation()
        assert store.database.read_bytes() == before
        assert set(store.root.iterdir()) == paths


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE extra (value TEXT)",
        "CREATE INDEX unexpected ON journal_entries(title)",
        "CREATE VIEW unexpected AS SELECT * FROM journal_entries",
        "CREATE TRIGGER unexpected AFTER INSERT ON journal_entries BEGIN DELETE FROM journal_entries; END",
        "ALTER TABLE journal_entries ADD COLUMN extra TEXT",
        "DROP TABLE journal_entries",
    ],
)
def test_schema_drift_is_rejected_without_writes(
    store: JournalStore, draft: JournalDraft, sql: str
) -> None:
    entry = store.create(draft)
    with sqlite3.connect(store.database) as connection:
        connection.execute(sql)
    before = store.database.read_bytes()
    for operation in (
        store.list_entries,
        lambda: store.get(entry.entry_id),
        lambda: store.create(draft),
    ):
        with pytest.raises(JournalStorageError):
            operation()
        assert store.database.read_bytes() == before


def test_matching_metadata_is_not_enough(
    store: JournalStore, draft: JournalDraft
) -> None:
    store.root.mkdir()
    with sqlite3.connect(store.database) as connection:
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.execute("CREATE TABLE journal_entries (entry_id TEXT)")
    before = store.database.read_bytes()
    with pytest.raises(JournalStorageError):
        store.create(draft)
    assert store.database.read_bytes() == before


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sources_json", "{"),
        ("sources_json", "{}"),
        ("sources_json", '[{"label":"x"}]'),
        ("sources_json", '[{"label":"x","url":null,"source_date":"2020-02-31"}]'),
        (
            "sources_json",
            '[{"label":"x","url":"http://example.test","source_date":null}]',
        ),
        ("sources_json", '[{"label":"x","url":null,"source_date":null,"extra":1}]'),
        ("sources_json", '[{"label":true,"url":null,"source_date":null}]'),
        ("sources_json", '[{"label":"x","url":null,"source_date":"9999-01-01"}]'),
        ("symbol", " note "),
        ("thesis", ""),
        ("thesis", b"bytes are not text"),
        ("title", "bad\x00content"),
        ("research_date", "20200102"),
        ("research_date", "9999-01-01"),
        ("created_at", "2021-02-03T04:05:06"),
        ("created_at", "2021-02-03T04:05:06.123456+01:00"),
        ("created_at", "not a timestamp"),
        ("entry_id", "../bad"),
    ],
)
def test_persisted_data_fails_closed(
    store: JournalStore, draft: JournalDraft, column: str, value: object
) -> None:
    entry = store.create(draft)
    with sqlite3.connect(store.database) as connection:
        connection.execute(f"UPDATE journal_entries SET {column}=?", (value,))
    before = store.database.read_bytes()
    with pytest.raises(JournalStorageError) as error:
        store.list_entries()
    assert "bad" not in str(error.value)
    if column != "entry_id":
        with pytest.raises(JournalStorageError):
            store.get(entry.entry_id)
    assert store.database.read_bytes() == before


def test_constraint_disabled_corruption_is_detected(
    store: JournalStore, draft: JournalDraft
) -> None:
    entry = store.create(draft)
    for column, value in (("horizon", "other"), ("provenance", "provider")):
        with sqlite3.connect(store.database) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(f"UPDATE journal_entries SET {column}=?", (value,))
        with pytest.raises(JournalStorageError):
            store.get(entry.entry_id)


def test_insert_failure_rolls_back_whole_entry_and_sources(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = store.create(draft)
    before = store.database.read_bytes()
    monkeypatch.setattr(
        storage,
        "_INSERT",
        storage._INSERT.replace("INSERT INTO", "INSERT OR ABORT INTO"),
    )
    monkeypatch.setattr(storage, "uuid4", lambda: UUID(entry.entry_id))
    with pytest.raises(JournalStorageError) as error:
        store.create(
            replace(draft, thesis="must not persist", sources=(SourceReference("new"),))
        )
    assert draft.title not in str(error.value)
    assert "UNIQUE" not in str(error.value)
    assert store.database.read_bytes() == before
    assert store.get(entry.entry_id) == entry
    assert len(store.list_entries()) == 1


def test_failed_initial_insert_leaves_no_target_and_valid_retry_succeeds(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_INSERT", "INSERT INTO nonexistent VALUES (?)")
        with pytest.raises(JournalStorageError):
            store.create(draft)
    assert not store.database.exists()
    assert list(store.root.iterdir()) == []
    assert store.list_entries() == []
    entry = store.create(draft)
    assert store.get(entry.entry_id) == entry
    assert len(store.list_entries()) == 1


def test_publication_sees_only_complete_closed_database(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = storage.os.link
    observed = []

    def inspect_link(source: Path, target: Path) -> None:
        assert source.parent == target.parent == store.root
        assert source != target
        assert not target.exists()
        # A separate read-only connection can see the committed metadata/entry.
        with closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        ) as connection:
            assert (
                connection.execute("PRAGMA application_id").fetchone()[0]
                == APPLICATION_ID
            )
            assert (
                connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
                == 1
            )
        assert not Path(str(source) + "-journal").exists()
        link(source, target)
        # The public target is immediately readable, before temp cleanup.
        observed.extend(store.list_entries())

    monkeypatch.setattr(storage.os, "link", inspect_link)
    entry = store.create(draft)
    assert [row.entry_id for row in observed] == [entry.entry_id]
    assert set(store.root.iterdir()) == {store.database}


def test_two_concurrent_first_saves_preserve_both_entries(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = storage.os.link
    ready = threading.Barrier(2)

    def competing_link(source: Path, target: Path) -> None:
        ready.wait(timeout=10)
        link(source, target)

    monkeypatch.setattr(storage.os, "link", competing_link)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.create, replace(draft, title=f"Concurrent note {i}"))
            for i in range(2)
        ]
        entries = [future.result(timeout=20) for future in futures]
    assert {row.entry_id for row in store.list_entries()} == {
        entry.entry_id for entry in entries
    }
    for entry in entries:
        assert store.get(entry.entry_id) == entry
    assert set(store.root.iterdir()) == {store.database}


@pytest.mark.parametrize("kind", ["empty", "corrupt", "foreign"])
def test_target_appearing_at_publication_is_never_clobbered(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    link = storage.os.link
    preserved = []

    def foreign_winner(source: Path, target: Path) -> None:
        if kind == "foreign":
            with sqlite3.connect(target) as connection:
                connection.execute("CREATE TABLE private_data (value TEXT)")
                connection.execute("INSERT INTO private_data VALUES ('preserve me')")
        else:
            target.write_bytes(b"" if kind == "empty" else b"not SQLite")
        preserved.append(target.read_bytes())
        link(source, target)  # Real Windows no-clobber behavior, not a simulated error.

    monkeypatch.setattr(storage.os, "link", foreign_winner)
    with pytest.raises(JournalStorageError):
        store.create(draft)
    assert store.database.read_bytes() == preserved[0]
    assert set(store.root.iterdir()) == {store.database}


def test_publication_failure_cleans_only_own_files_and_allows_retry(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.root.mkdir()
    unrelated = store.root / ".research-journal-other.sqlite3"
    unrelated.write_bytes(b"another attempt's file")

    def unsupported_link(source: Path, target: Path) -> None:
        # Include owned sidecars in the cleanup assertion.
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(source) + suffix).write_bytes(b"owned temporary sidecar")
        raise OSError("hard links unavailable: private path information")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "link", unsupported_link)
        with pytest.raises(JournalStorageError) as error:
            store.create(draft)
        assert "private" not in str(error.value)
    assert not store.database.exists()
    assert set(store.root.iterdir()) == {unrelated}
    assert unrelated.read_bytes() == b"another attempt's file"
    entry = store.create(draft)
    assert store.get(entry.entry_id) == entry
    assert set(store.root.iterdir()) == {unrelated, store.database}


@pytest.mark.parametrize("stage", ["schema", "commit"])
def test_initialization_failure_before_publication_allows_retry(
    store: JournalStore,
    draft: JournalDraft,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(
            self, sql: str, parameters: tuple[object, ...] = ()
        ) -> sqlite3.Cursor:
            if stage == "schema" and sql == storage._SCHEMA:
                raise sqlite3.OperationalError("injected schema failure")
            return super().execute(sql, parameters)

        def commit(self) -> None:
            if stage == "commit":
                raise sqlite3.OperationalError("injected commit failure")
            super().commit()

    def failing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=FailingConnection)

    with monkeypatch.context() as patch:
        patch.setattr(storage.sqlite3, "connect", failing_connect)
        with pytest.raises(JournalStorageError):
            store.create(draft)
    assert not store.database.exists()
    assert list(store.root.iterdir()) == []
    entry = store.create(draft)
    assert store.get(entry.entry_id) == entry


def test_connections_short_lived_timeout_and_read_only(
    store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = sqlite3.connect
    connections = []
    modes = []

    def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        assert kwargs["timeout"] == 10
        assert kwargs["uri"] is True
        modes.append(args[0])
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(storage.sqlite3, "connect", tracked_connect)
    entry = store.create(draft)
    store.get(entry.entry_id)
    store.list_entries()
    assert str(modes[0]).endswith("?mode=rw")
    assert all(str(uri).endswith("?mode=ro") for uri in modes[1:])
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_filesystem_failure_is_sanitized(tmp_path: Path, draft: JournalDraft) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("private user content")
    with pytest.raises(JournalStorageError) as error:
        JournalStore(root).create(draft)
    assert str(root) not in str(error.value)
    assert "private" not in str(error.value)

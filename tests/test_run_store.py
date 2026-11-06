import json
import sqlite3
from pathlib import Path

import pytest

from edge.errors import EdgeError
from edge.run_store import RunStore


def begin(store: RunStore) -> str:
    return store.begin({"scenario": "round-trip", "dataset_checksum": "abc"}, {"synthetic": True})


def test_evidence_and_index_survive_new_store_instance(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = begin(store)
    assert store.list_runs()[0]["status"] == "running"
    result = {"status": "succeeded", "outcome": {"ending_cash": "10026.00"}}
    store.finish(run_id, result)
    restored = RunStore(tmp_path).get(run_id)
    assert restored["status"] == "succeeded"
    assert restored["result"] == result
    artifacts = tmp_path / "runs" / run_id
    assert json.loads((artifacts / "manifest.json").read_text()) == restored["manifest"]
    assert json.loads((artifacts / "result.json").read_text()) == restored["result"]
    assert json.loads((artifacts / "dataset.json").read_text()) == {"synthetic": True}
    with pytest.raises(EdgeError, match="already finalized"):
        store.finish(run_id, result)


def test_failure_is_explicitly_persisted(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = begin(store)
    store.finish(run_id, {"status": "failed", "error": "Native engine failed"})
    assert store.list_runs()[0]["error"] == "Native engine failed"
    assert store.get(run_id)["status"] == "failed"


def test_run_ids_are_not_paths_or_sql_fragments(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    for invalid in ("../manifest", "' OR 1=1", "", "a" * 31):
        with pytest.raises(EdgeError, match="identifier"):
            store.get(invalid)


def test_foreign_database_is_not_modified(tmp_path: Path) -> None:
    db = tmp_path / "edge.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")
        connection.execute("INSERT INTO user_data VALUES ('preserve me')")
    with pytest.raises(EdgeError, match="unrecognized"):
        begin(RunStore(tmp_path))
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT value FROM user_data").fetchone()[0] == "preserve me"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_missing_store_does_not_create_database(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "absent")
    with pytest.raises(EdgeError, match="No EDGE run database"):
        store.list_runs()
    assert not store.root.exists()


def test_corrupt_json_is_explicit_and_never_reinitialized(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = begin(store)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE runs SET manifest_json='{' WHERE run_id=?", (run_id,))
    with pytest.raises(EdgeError, match="Corrupt JSON"):
        store.get(run_id)
    assert store.list_runs()[0]["status"] == "running"

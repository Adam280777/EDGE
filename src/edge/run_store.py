"""Local run index and immutable per-run evidence. No portfolio ledger."""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from edge.errors import EdgeError
from edge.validation import canonical_json

APPLICATION_ID = 0x45444745
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical_json(value) + "\n")


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.database = self.root / "edge.sqlite3"

    @contextmanager
    def _connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if not create and not self.database.exists():
            raise EdgeError(f"No EDGE run database at {self.database}. Run a simulation first.")
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if app_id == 0 and version == 0 and not tables and create:
                with connection:
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    connection.execute("""
                        CREATE TABLE runs (
                            run_id TEXT PRIMARY KEY,
                            started_at TEXT NOT NULL,
                            finished_at TEXT,
                            scenario TEXT NOT NULL,
                            status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
                            dataset_checksum TEXT NOT NULL,
                            manifest_json TEXT NOT NULL,
                            result_json TEXT,
                            error TEXT
                        )
                    """)
            elif app_id != APPLICATION_ID or version != SCHEMA_VERSION:
                raise EdgeError("Refusing to modify an unrecognized EDGE database or schema version.")
            yield connection
        finally:
            connection.close()

    def begin(self, manifest: dict[str, object], dataset: dict[str, object]) -> str:
        run_id = uuid4().hex
        manifest = {**manifest, "run_id": run_id, "started_at": utc_now()}
        with self._connect(create=True) as connection:
            directory = self.root / "runs" / run_id
            directory.mkdir(parents=True, exist_ok=False)
            _write_new(directory / "dataset.json", dataset)
            _write_new(directory / "manifest.json", manifest)
            with connection:
                connection.execute(
                    """INSERT INTO runs
                    (run_id, started_at, scenario, status, dataset_checksum, manifest_json)
                    VALUES (?, ?, ?, 'running', ?, ?)""",
                    (run_id, manifest["started_at"], manifest["scenario"],
                     manifest["dataset_checksum"], canonical_json(manifest)),
                )
        return run_id

    def finish(self, run_id: str, result: dict[str, object]) -> None:
        self._check_id(run_id)
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise EdgeError("Cannot finish an unknown or already finalized run.")
            status = result.get("status")
            if status not in ("succeeded", "failed"):
                raise EdgeError("A finalized run requires an explicit succeeded or failed status.")
            _write_new(self.root / "runs" / run_id / "result.json", result)
            with connection:
                connection.execute(
                    """UPDATE runs SET status=?, finished_at=?, result_json=?, error=?
                    WHERE run_id=? AND status='running'""",
                    (status, utc_now(), canonical_json(result), result.get("error"), run_id),
                )

    @staticmethod
    def _check_id(run_id: str) -> None:
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise EdgeError("run_id must be a 32-character lowercase hexadecimal EDGE run identifier.")

    def list_runs(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id, started_at, finished_at, scenario, status,
                dataset_checksum, error FROM runs ORDER BY started_at DESC, run_id DESC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def get(self, run_id: str) -> dict[str, object]:
        self._check_id(run_id)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise EdgeError(f"Unknown run {run_id}.")
            try:
                manifest = json.loads(row["manifest_json"])
                result = json.loads(row["result_json"]) if row["result_json"] else None
            except json.JSONDecodeError as error:
                raise EdgeError(f"Corrupt JSON evidence in run {run_id}; the database has not been reset.") from error
            if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
                raise EdgeError(f"Invalid manifest identity in run {run_id}.")
            if result is not None and (
                not isinstance(result, dict) or result.get("status") != row["status"]
            ):
                raise EdgeError(f"Invalid result/status evidence in run {run_id}.")
            return {
                "manifest": manifest, "status": row["status"], "result": result,
                "artifact_directory": str(self.root / "runs" / run_id),
            }

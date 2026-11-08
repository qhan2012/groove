"""Durable single-writer run store; generated artifacts are content-addressed."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from groove.contracts import RunContext, RunOutcome


class StoreError(RuntimeError):
    pass


class SqliteRunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "groove.sqlite3"
        self._create_schema()

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self) -> None:
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, goal TEXT NOT NULL, criteria_json TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'RUNNING'
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    iteration INTEGER NOT NULL, stage TEXT NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(run_id, iteration, stage)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT NOT NULL REFERENCES runs(id), ref TEXT NOT NULL,
                    name TEXT NOT NULL, relative_path TEXT NOT NULL,
                    media_type TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(run_id, ref)
                );
            """)

    def create_run(self, goal: str, acceptance_criteria: list[str]) -> str:
        if not goal.strip() or not acceptance_criteria or any(not c.strip() for c in acceptance_criteria):
            raise ValueError("A nonempty goal and at least one nonempty acceptance criterion are required")
        run_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO runs(id, goal, criteria_json, state_json, status) VALUES(?,?,?,?,?)",
                (run_id, goal, json.dumps(acceptance_criteria), "{}", "RUNNING"),
            )
        return run_id

    def _get_run(self, db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(f"Run not found: {run_id}")
        return row

    def load_context(self, run_id: str) -> RunContext:
        with self._db() as db:
            run = self._get_run(db, run_id)
            rows = db.execute("SELECT iteration, stage, payload_json FROM events WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        history = [{"iteration": r["iteration"], "stage": r["stage"], "payload": json.loads(r["payload_json"])} for r in rows]
        judgments = sum(e["stage"] == "judgment" for e in history)
        return RunContext(
            run_id=run_id, goal=run["goal"], acceptance_criteria=json.loads(run["criteria_json"]),
            iteration=judgments + 1, state=json.loads(run["state_json"]), history=history,
        )

    def append_event(self, run_id: str, iteration: int, stage: str, payload: dict[str, Any]) -> None:
        if stage not in {"plan", "execution", "judgment", "error"}:
            raise ValueError(f"Unknown stage {stage}")
        with self._db() as db:
            run = self._get_run(db, run_id)
            if run["status"] != "RUNNING":
                raise StoreError(f"Cannot add event to run with status {run['status']}")
            try:
                db.execute("INSERT INTO events(run_id, iteration, stage, payload_json) VALUES(?,?,?,?)",
                           (run_id, iteration, stage, json.dumps(payload, sort_keys=True)))
            except sqlite3.IntegrityError as exc:
                raise StoreError(f"Stage already recorded: {run_id}/{iteration}/{stage}") from exc

    def commit_judgment(self, run_id: str, iteration: int, payload: dict[str, Any], delta: dict[str, Any], status: str) -> None:
        protected = {"run_id", "goal", "acceptance_criteria", "status", "iteration"}
        if protected.intersection(delta):
            raise StoreError("Judger state delta may not alter immutable run metadata")
        if status not in {"RUNNING", "COMPLETED", "STOPPED"}:
            raise ValueError("Invalid judgment status")
        with self._db() as db:
            run = self._get_run(db, run_id)
            if run["status"] != "RUNNING":
                raise StoreError(f"Run is {run['status']}")
            state = json.loads(run["state_json"])
            state.update(delta)
            try:
                db.execute("INSERT INTO events(run_id, iteration, stage, payload_json) VALUES(?,?,?,?)",
                           (run_id, iteration, "judgment", json.dumps(payload, sort_keys=True)))
            except sqlite3.IntegrityError as exc:
                raise StoreError("Judgment already committed") from exc
            db.execute("UPDATE runs SET state_json=?, status=? WHERE id=?", (json.dumps(state), status, run_id))

    def set_status(self, run_id: str, status: str) -> None:
        if status not in {"ERROR", "LIMIT_REACHED"}:
            raise ValueError("Only runtime errors and iteration limits may be set directly")
        with self._db() as db:
            self._get_run(db, run_id)
            db.execute("UPDATE runs SET status=? WHERE id=? AND status='RUNNING'", (status, run_id))

    def outcome(self, run_id: str) -> RunOutcome:
        with self._db() as db:
            row = self._get_run(db, run_id)
            count = db.execute("SELECT count(*) FROM events WHERE run_id=? AND stage='judgment'", (run_id,)).fetchone()[0]
        return RunOutcome(run_id=run_id, status=row["status"], completed_iterations=count,
                          state=json.loads(row["state_json"]))

    def write_artifact(self, run_id: str, name: str, content: bytes, media_type: str = "text/plain") -> str:
        # name is display metadata only; never used as a disk path.
        with self._db() as db:
            self._get_run(db, run_id)
        digest = hashlib.sha256(content).hexdigest()
        ref = f"sha256:{digest}"
        folder = self.root / "runs" / run_id / "artifacts"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / digest
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as tmp:
                tmp.write(content)
                temporary = tmp.name
            os.replace(temporary, target)
        relative_path = str(target.relative_to(self.root))
        with self._db() as db:
            db.execute("""INSERT OR IGNORE INTO artifacts(run_id, ref, name, relative_path, media_type, sha256)
                          VALUES(?,?,?,?,?,?)""", (run_id, ref, name, relative_path, media_type, digest))
        return ref

    def has_artifact(self, run_id: str, ref: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT relative_path, sha256 FROM artifacts WHERE run_id=? AND ref=?", (run_id, ref)).fetchone()
        if row is None:
            return False
        path = self.root / row["relative_path"]
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]

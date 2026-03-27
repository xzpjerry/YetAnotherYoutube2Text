from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            youtube_url TEXT NOT NULL,
            display_title TEXT NOT NULL,
            language_hint TEXT,
            status TEXT NOT NULL,
            progress_stage TEXT NOT NULL,
            status_message TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            last_heartbeat_at TEXT,
            worker_id TEXT,
            last_error_code TEXT,
            last_error_message TEXT,
            artifact_dir TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
        ON jobs (status, created_at)
        """
    )
    conn.commit()

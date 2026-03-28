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
            display_title TEXT,
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
    _migrate_nullable_display_title(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
        ON jobs (status, created_at)
        """
    )
    conn.commit()


def _migrate_nullable_display_title(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(jobs)").fetchall()
    display_title_column = next(
        (column for column in columns if column["name"] == "display_title"),
        None,
    )
    if display_title_column is None or display_title_column["notnull"] == 0:
        return

    conn.execute("ALTER TABLE jobs RENAME TO jobs__legacy_display_title_not_null")
    conn.execute(
        """
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            youtube_url TEXT NOT NULL,
            display_title TEXT,
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
        INSERT INTO jobs (
            job_id,
            youtube_url,
            display_title,
            language_hint,
            status,
            progress_stage,
            status_message,
            attempt_count,
            created_at,
            started_at,
            finished_at,
            last_heartbeat_at,
            worker_id,
            last_error_code,
            last_error_message,
            artifact_dir
        )
        SELECT
            job_id,
            youtube_url,
            display_title,
            language_hint,
            status,
            progress_stage,
            status_message,
            attempt_count,
            created_at,
            started_at,
            finished_at,
            last_heartbeat_at,
            worker_id,
            last_error_code,
            last_error_message,
            artifact_dir
        FROM jobs__legacy_display_title_not_null
        """
    )
    conn.execute("DROP TABLE jobs__legacy_display_title_not_null")

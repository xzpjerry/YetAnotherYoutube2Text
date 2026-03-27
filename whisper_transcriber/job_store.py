from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from whisper_transcriber.db import connect_db, ensure_schema


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    youtube_url: str
    display_title: str
    language_hint: str | None
    status: str
    progress_stage: str
    status_message: str
    attempt_count: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    last_heartbeat_at: str | None
    worker_id: str | None
    last_error_code: str | None
    last_error_message: str | None
    artifact_dir: str | None


class JobStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with connect_db(self.db_path) as conn:
            ensure_schema(conn)

    def create_job(
        self,
        youtube_url: str,
        display_title: str,
        language_hint: str | None,
    ) -> JobRecord:
        now = utc_now().isoformat()
        job_id = uuid.uuid4().hex
        with connect_db(self.db_path) as conn:
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
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    youtube_url,
                    display_title,
                    language_hint,
                    "queued",
                    "queued",
                    "Queued",
                    0,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row_to_job(row)

    def list_recent_jobs(self, limit: int = 20) -> list[JobRecord]:
        with connect_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def get_job(self, job_id: str) -> JobRecord | None:
        with connect_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def claim_next_job(
        self,
        worker_id: str,
        heartbeat_timeout_seconds: int,
    ) -> JobRecord | None:
        now = utc_now().isoformat()
        with connect_db(self.db_path) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                _requeue_stale_jobs(conn, heartbeat_timeout_seconds)
                row = conn.execute(
                    """
                    SELECT job_id
                    FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None

                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?,
                        progress_stage = ?,
                        status_message = ?,
                        attempt_count = attempt_count + 1,
                        started_at = ?,
                        finished_at = NULL,
                        last_heartbeat_at = ?,
                        worker_id = ?,
                        last_error_code = NULL,
                        last_error_message = NULL
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (
                        "running",
                        "claimed",
                        f"Claimed by worker {worker_id}",
                        now,
                        now,
                        worker_id,
                        row["job_id"],
                    ),
                )
                claimed = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (row["job_id"],),
                ).fetchone()
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
        if claimed is None:
            return None
        return _row_to_job(claimed)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        stage: str,
        message: str,
    ) -> bool:
        now = utc_now().isoformat()
        with connect_db(self.db_path) as conn:
            return _update_owned_running_job(
                conn,
                job_id=job_id,
                worker_id=worker_id,
                fields={
                    "progress_stage": stage,
                    "status_message": message,
                    "last_heartbeat_at": now,
                },
            )

    def mark_completed(
        self,
        job_id: str,
        worker_id: str,
        artifact_dir: str | None,
        message: str = "Completed",
    ) -> bool:
        now = utc_now().isoformat()
        with connect_db(self.db_path) as conn:
            return _update_owned_running_job(
                conn,
                job_id=job_id,
                worker_id=worker_id,
                fields={
                    "status": "completed",
                    "progress_stage": "completed",
                    "status_message": message,
                    "finished_at": now,
                    "last_heartbeat_at": now,
                    "artifact_dir": artifact_dir,
                },
            )

    def mark_failed(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        message: str,
    ) -> bool:
        now = utc_now().isoformat()
        with connect_db(self.db_path) as conn:
            return _update_owned_running_job(
                conn,
                job_id=job_id,
                worker_id=worker_id,
                fields={
                    "status": "failed",
                    "progress_stage": "failed",
                    "status_message": message,
                    "finished_at": now,
                    "last_heartbeat_at": now,
                    "last_error_code": error_code,
                    "last_error_message": message,
                },
            )

    def requeue_stale_jobs(self, heartbeat_timeout_seconds: int) -> int:
        with connect_db(self.db_path) as conn:
            requeued = _requeue_stale_jobs(conn, heartbeat_timeout_seconds)
            conn.commit()
        return requeued


def _requeue_stale_jobs(conn: sqlite3.Connection, heartbeat_timeout_seconds: int) -> int:
    cutoff = (utc_now() - timedelta(seconds=heartbeat_timeout_seconds)).isoformat()
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = ?,
            progress_stage = ?,
            status_message = ?,
            started_at = NULL,
            finished_at = NULL,
            last_heartbeat_at = NULL,
            worker_id = NULL
        WHERE status = 'running'
          AND COALESCE(last_heartbeat_at, started_at, created_at) < ?
        """,
        (
            "queued",
            "queued",
            "Requeued after stale worker heartbeat",
            cutoff,
        ),
    )
    return cursor.rowcount


def _update_owned_running_job(
    conn: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    fields: dict[str, str | None],
) -> bool:
    assignments = ", ".join(f"{column} = ?" for column in fields)
    values = list(fields.values()) + [job_id, worker_id]
    cursor = conn.execute(
        f"""
        UPDATE jobs
        SET {assignments}
        WHERE job_id = ?
          AND status = 'running'
          AND worker_id = ?
        """,
        values,
    )
    conn.commit()
    return cursor.rowcount == 1


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=row["job_id"],
        youtube_url=row["youtube_url"],
        display_title=row["display_title"],
        language_hint=row["language_hint"],
        status=row["status"],
        progress_stage=row["progress_stage"],
        status_message=row["status_message"],
        attempt_count=row["attempt_count"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
        worker_id=row["worker_id"],
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        artifact_dir=row["artifact_dir"],
    )

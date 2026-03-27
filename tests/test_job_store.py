from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from whisper_transcriber.config import load_settings
from whisper_transcriber.db import connect_db, ensure_schema
from whisper_transcriber.job_store import JobStore


def test_load_settings_uses_expected_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in (
        "ARTIFACTS_DIR",
        "DB_PATH",
        "MAX_QUEUE_DEPTH",
        "MAX_ARTIFACT_AGE_HOURS",
        "MAX_ARTIFACTS",
        "WORKER_POLL_SECONDS",
        "HEARTBEAT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings()

    assert str(settings.artifacts_dir) == "artifacts"
    assert str(settings.db_path) == "data/transcriber.sqlite3"
    assert settings.max_queue_depth == 5
    assert settings.max_artifact_age_hours == 168
    assert settings.max_artifacts == 100
    assert settings.worker_poll_seconds == 2
    assert settings.heartbeat_seconds == 5


def test_load_settings_with_explicit_empty_mapping_uses_defaults(monkeypatch):
    monkeypatch.setenv("DB_PATH", "/tmp/ambient.sqlite3")

    settings = load_settings({})

    assert str(settings.db_path) == "data/transcriber.sqlite3"


def test_ensure_schema_creates_jobs_table(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"

    with connect_db(db_path) as conn:
        ensure_schema(conn)

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }

    assert columns >= {
        "job_id",
        "youtube_url",
        "display_title",
        "language_hint",
        "status",
        "progress_stage",
        "status_message",
        "attempt_count",
        "created_at",
        "started_at",
        "finished_at",
        "last_heartbeat_at",
        "worker_id",
        "last_error_code",
        "last_error_message",
        "artifact_dir",
    }


def test_create_job_and_read_it_back(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")

    created = store.create_job(
        youtube_url="https://youtu.be/example",
        display_title="Example Video",
        language_hint="en",
    )

    fetched = store.get_job(created.job_id)
    recent = store.list_recent_jobs(limit=10)

    assert created.status == "queued"
    assert created.progress_stage == "queued"
    assert created.status_message == "Queued"
    assert created.attempt_count == 0
    assert created.youtube_url == "https://youtu.be/example"
    assert created.display_title == "Example Video"
    assert created.language_hint == "en"
    assert fetched == created
    assert recent == [created]


def test_claim_next_job_marks_oldest_queued_job_running_atomically(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.create_job(
        youtube_url="https://youtu.be/first",
        display_title="First",
        language_hint=None,
    )
    second = store.create_job(
        youtube_url="https://youtu.be/second",
        display_title="Second",
        language_hint="fr",
    )

    claimed = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    claimed_again = store.claim_next_job(
        worker_id="worker-2",
        heartbeat_timeout_seconds=30,
    )
    third_claim = store.claim_next_job(
        worker_id="worker-3",
        heartbeat_timeout_seconds=30,
    )

    assert claimed is not None
    assert claimed.job_id == first.job_id
    assert claimed.status == "running"
    assert claimed.progress_stage == "claimed"
    assert claimed.status_message == "Claimed by worker worker-1"
    assert claimed.worker_id == "worker-1"
    assert claimed.attempt_count == 1
    assert claimed.started_at is not None
    assert claimed.last_heartbeat_at is not None

    assert claimed_again is not None
    assert claimed_again.job_id == second.job_id
    assert claimed_again.worker_id == "worker-2"
    assert third_claim is None


def test_requeue_stale_running_job_returns_it_to_queue(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(
        youtube_url="https://youtu.be/stale",
        display_title="Stale",
        language_hint=None,
    )

    claimed = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    assert claimed is not None

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as conn:
        conn.execute(
            """
            UPDATE jobs
            SET last_heartbeat_at = ?, status_message = ?
            WHERE job_id = ?
            """,
            (stale_time.isoformat(), "Worker disappeared", created.job_id),
        )
        conn.commit()

    requeued = store.requeue_stale_jobs(heartbeat_timeout_seconds=30)
    recovered = store.get_job(created.job_id)
    claimed_again = store.claim_next_job(
        worker_id="worker-2",
        heartbeat_timeout_seconds=30,
    )

    assert requeued == 1
    assert recovered is not None
    assert recovered.status == "queued"
    assert recovered.progress_stage == "queued"
    assert recovered.status_message == "Requeued after stale worker heartbeat"
    assert recovered.worker_id is None
    assert recovered.started_at is None
    assert recovered.last_heartbeat_at is None
    assert recovered.attempt_count == 1
    assert claimed_again is not None
    assert claimed_again.job_id == created.job_id
    assert claimed_again.attempt_count == 2


def test_heartbeat_updates_stage_message_and_timestamp(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(
        youtube_url="https://youtu.be/heartbeat",
        display_title="Heartbeat",
        language_hint=None,
    )
    claimed = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    assert claimed is not None

    updated = store.heartbeat(
        created.job_id,
        worker_id="worker-1",
        claim_attempt_count=claimed.attempt_count,
        stage="transcribing",
        message="50% complete",
    )

    refreshed = store.get_job(created.job_id)

    assert updated is True
    assert refreshed is not None
    assert refreshed.progress_stage == "transcribing"
    assert refreshed.status_message == "50% complete"
    assert refreshed.last_heartbeat_at is not None


def test_mark_completed_records_artifact_dir_and_finish_time(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(
        youtube_url="https://youtu.be/completed",
        display_title="Completed",
        language_hint=None,
    )
    claimed = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    assert claimed is not None

    updated = store.mark_completed(
        created.job_id,
        worker_id="worker-1",
        claim_attempt_count=claimed.attempt_count,
        artifact_dir="artifacts/completed-job",
        message="Done",
    )

    refreshed = store.get_job(created.job_id)

    assert updated is True
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.progress_stage == "completed"
    assert refreshed.status_message == "Done"
    assert refreshed.artifact_dir == "artifacts/completed-job"
    assert refreshed.finished_at is not None


def test_mark_failed_records_error_details_and_finish_time(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(
        youtube_url="https://youtu.be/failed",
        display_title="Failed",
        language_hint=None,
    )
    claimed = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    assert claimed is not None

    updated = store.mark_failed(
        created.job_id,
        worker_id="worker-1",
        claim_attempt_count=claimed.attempt_count,
        error_code="download_error",
        message="Could not fetch media",
    )

    refreshed = store.get_job(created.job_id)

    assert updated is True
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.progress_stage == "failed"
    assert refreshed.status_message == "Could not fetch media"
    assert refreshed.last_error_code == "download_error"
    assert refreshed.last_error_message == "Could not fetch media"
    assert refreshed.finished_at is not None


def test_stale_worker_cannot_mutate_job_after_requeue_and_reclaim(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(
        youtube_url="https://youtu.be/reclaimed",
        display_title="Reclaimed",
        language_hint=None,
    )

    claimed_by_a = store.claim_next_job(
        worker_id="worker-a",
        heartbeat_timeout_seconds=30,
    )
    assert claimed_by_a is not None

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as conn:
        conn.execute(
            "UPDATE jobs SET last_heartbeat_at = ? WHERE job_id = ?",
            (stale_time.isoformat(), created.job_id),
        )
        conn.commit()

    assert store.requeue_stale_jobs(heartbeat_timeout_seconds=30) == 1

    claimed_by_b = store.claim_next_job(
        worker_id="worker-b",
        heartbeat_timeout_seconds=30,
    )
    assert claimed_by_b is not None
    assert claimed_by_b.job_id == created.job_id

    completed = store.mark_completed(
        created.job_id,
        worker_id="worker-a",
        claim_attempt_count=claimed_by_a.attempt_count,
        artifact_dir="artifacts/reclaimed",
        message="stale worker completion",
    )
    heartbeated = store.heartbeat(
        created.job_id,
        worker_id="worker-a",
        claim_attempt_count=claimed_by_a.attempt_count,
        stage="transcribing",
        message="stale heartbeat",
    )
    failed = store.mark_failed(
        created.job_id,
        worker_id="worker-a",
        claim_attempt_count=claimed_by_a.attempt_count,
        error_code="stale",
        message="stale failure",
    )

    refreshed = store.get_job(created.job_id)

    assert completed is False
    assert heartbeated is False
    assert failed is False
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.progress_stage == "claimed"
    assert refreshed.status_message == "Claimed by worker worker-b"
    assert refreshed.worker_id == "worker-b"


def test_same_worker_id_cannot_mutate_job_with_stale_claim_attempt(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(
        youtube_url="https://youtu.be/same-worker",
        display_title="Same Worker",
        language_hint=None,
    )

    first_claim = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    assert first_claim is not None

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as conn:
        conn.execute(
            "UPDATE jobs SET last_heartbeat_at = ? WHERE job_id = ?",
            (stale_time.isoformat(), created.job_id),
        )
        conn.commit()

    assert store.requeue_stale_jobs(heartbeat_timeout_seconds=30) == 1

    second_claim = store.claim_next_job(
        worker_id="worker-1",
        heartbeat_timeout_seconds=30,
    )
    assert second_claim is not None
    assert second_claim.job_id == created.job_id
    assert second_claim.attempt_count == first_claim.attempt_count + 1

    stale_completion = store.mark_completed(
        created.job_id,
        worker_id="worker-1",
        claim_attempt_count=first_claim.attempt_count,
        artifact_dir="artifacts/same-worker",
        message="stale same worker completion",
    )

    refreshed = store.get_job(created.job_id)

    assert stale_completion is False
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.progress_stage == "claimed"
    assert refreshed.status_message == "Claimed by worker worker-1"
    assert refreshed.worker_id == "worker-1"
    assert refreshed.attempt_count == second_claim.attempt_count

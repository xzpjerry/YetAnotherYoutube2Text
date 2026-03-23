# Durable Job Queue and Progress UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synchronous proof-of-concept flow with durable SQLite-backed jobs, a single worker, and dedicated job-status pages that survive refreshes and restarts.

**Architecture:** Keep FastAPI and Jinja2 for the web layer, but move queue state into SQLite and long-running transcription into a separate worker process. Split the current monolithic `app.py` into focused modules for config, storage, pipeline, and web routes so failures, retries, and progress updates can be tested without real network or model execution.

**Tech Stack:** FastAPI, Jinja2 templates, SQLite via `sqlite3`, `yt-dlp`, `ffmpeg-python`, `mlx-whisper`, pytest, httpx/TestClient

---

## File Structure

### Existing files to modify

- `app.py:1-396`
  Thin application entrypoint that imports `create_app()` instead of containing queue, UI, and pipeline logic directly.
- `.gitignore:1-5`
  Ignore persistent local state such as `data/` and brainstorming artifacts.

### Files to create

- `worker.py`
  Thin worker entrypoint for `python worker.py`.
- `requirements-dev.txt`
  Test-only dependencies such as `pytest` and `httpx`.
- `README.md`
  Operator and developer guide covering prerequisites, startup flow, worker process, VPN/proxy expectations, and common failures.
- `whisper_transcriber/__init__.py`
  Package marker and minimal version surface if needed.
- `whisper_transcriber/config.py`
  Environment-backed settings for model path, artifact path, DB path, queue limits, cleanup windows, and worker timing.
- `whisper_transcriber/environment.py`
  Startup checks for `ffmpeg`, model path, DB parent directory, and artifact writability.
- `whisper_transcriber/db.py`
  SQLite connection factory, schema bootstrap, and transaction helpers.
- `whisper_transcriber/job_store.py`
  CRUD and queue operations for jobs, including claim, heartbeat, completion, failure, and stale-job recovery.
- `whisper_transcriber/errors.py`
  Typed pipeline errors with user-safe messages and internal error codes.
- `whisper_transcriber/formatters.py`
  Pure helpers for slugging, timestamp formatting, SRT generation, and VTT generation.
- `whisper_transcriber/artifacts.py`
  Job-specific artifact directories, metadata serialization, and retention cleanup.
- `whisper_transcriber/media.py`
  Thin wrappers around `yt_dlp`, `ffmpeg`, and `mlx_whisper`.
- `whisper_transcriber/pipeline.py`
  Job orchestration that runs download, convert, transcribe, and persist while emitting progress updates.
- `whisper_transcriber/web.py`
  FastAPI app factory, HTML routes, JSON polling routes, health endpoint, and artifact/static mounts.
- `templates/base.html`
  Shared layout shell for the three HTML pages.
- `templates/index.html`
  Submission form with a recent-jobs link.
- `templates/job_detail.html`
  Durable job page with progress state, auto-polling hooks, transcript preview, and artifact links.
- `templates/job_list.html`
  Shared recent-jobs view for the trusted VPN group.
- `static/app.css`
  Basic shared styles for the submit page, job cards, state badges, and artifact links.
- `static/job-status.js`
  Polling logic for job detail pages plus transcript copy-to-clipboard behavior.
- `tests/conftest.py`
  Shared fixtures for temp SQLite DBs, temp artifacts directories, and FastAPI TestClient setup.
- `tests/test_formatters.py`
  Unit tests for subtitle and slug helpers.
- `tests/test_job_store.py`
  Unit tests for schema init, queueing, claiming, and stale-job recovery.
- `tests/test_pipeline.py`
  Mocked integration tests for successful and failed job execution.
- `tests/test_worker.py`
  Worker loop tests for claim/run/heartbeat/requeue behavior.
- `tests/test_web.py`
  Route tests for form submission, JSON polling, recent jobs, queue-depth rejection, and health checks.
- `tests/test_retention.py`
  Unit tests for artifact cleanup by age/count.

## Implementation Notes

- Do not introduce an ORM. Use `sqlite3` directly; the system is small and predictable.
- Keep one worker only. Avoid concurrency features that imply multiple simultaneous transcriptions.
- Keep reverse-proxy basic auth as an operational concern documented in `README.md`; do not build a user-account system.
- Preserve the current copy-to-clipboard convenience, but move it into `static/job-status.js`.
- Keep `/api/transcribe` as a compatibility JSON alias if it is cheap, but change its behavior to “enqueue and return job metadata,” not “run synchronously.”

### Task 1: Extract Pure Helpers and Test Harness

**Files:**
- Create: `requirements-dev.txt`
- Create: `whisper_transcriber/__init__.py`
- Create: `whisper_transcriber/formatters.py`
- Create: `tests/conftest.py`
- Create: `tests/test_formatters.py`

- [ ] **Step 1: Add test dependencies**

```txt
# requirements-dev.txt
-r requirements.txt
pytest==8.3.5
httpx==0.27.2
```

- [ ] **Step 2: Install dev dependencies**

Run: `python -m pip install -r requirements-dev.txt`
Expected: install completes with `pytest` and `httpx` available.

- [ ] **Step 3: Write the failing formatter tests**

```python
from whisper_transcriber.formatters import safe_slug, format_srt_ts, to_srt, to_vtt

def test_safe_slug_normalizes_and_falls_back():
    assert safe_slug("Hello / world") == "Hello_world"
    assert safe_slug("   ") == "audio"

def test_format_srt_ts_clamps_negative_values():
    assert format_srt_ts(-2) == "00:00:00,000"

def test_to_srt_and_vtt_render_segments():
    segments = [{"start": 0, "end": 1.25, "text": " hi "}]
    assert "00:00:00,000 --> 00:00:01,250" in to_srt(segments)
    assert "00:00:00.000 --> 00:00:01.250" in to_vtt(segments)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_formatters.py -v`
Expected: FAIL with `ModuleNotFoundError` for `whisper_transcriber.formatters`.

- [ ] **Step 5: Implement the pure helper module**

```python
# whisper_transcriber/formatters.py
import re
import unicodedata
from datetime import timedelta

def safe_slug(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[^\w\s\-().]", "_", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "_", value)
    return value.strip("_") or "audio"

def format_srt_ts(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    td = timedelta(seconds=seconds)
    hours, rem = divmod(td.seconds, 3600)
    hours += td.days * 24
    minutes, secs = divmod(rem, 60)
    millis = int(td.microseconds / 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def to_srt(segments: list[dict]) -> str:
    ...

def to_vtt(segments: list[dict]) -> str:
    ...
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_formatters.py -v`
Expected: PASS for all formatter cases.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt whisper_transcriber/__init__.py whisper_transcriber/formatters.py tests/conftest.py tests/test_formatters.py
git commit -m "test: extract formatter helpers"
```

### Task 2: Add SQLite Schema, Config, and Job Store

**Files:**
- Create: `whisper_transcriber/config.py`
- Create: `whisper_transcriber/environment.py`
- Create: `whisper_transcriber/db.py`
- Create: `whisper_transcriber/job_store.py`
- Create: `tests/test_job_store.py`
- Modify: `.gitignore:1-5`

- [ ] **Step 1: Write the failing job-store tests**

```python
from whisper_transcriber.db import init_db
from whisper_transcriber.job_store import JobStore

def test_create_and_claim_job(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    init_db(db_path)
    store = JobStore(db_path)
    job_id = store.create_job("https://youtu.be/example", language_hint="en")
    claimed = store.claim_next_job(worker_id="worker-1", heartbeat_timeout_seconds=30)
    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.progress_stage == "downloading"

def test_requeue_stale_running_job(tmp_path):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_job_store.py -v`
Expected: FAIL because `db.py` and `job_store.py` do not exist yet.

- [ ] **Step 3: Implement settings and schema bootstrap**

```python
# whisper_transcriber/config.py
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    artifacts_dir: str = os.environ.get("ARTIFACTS_DIR", "./artifacts")
    db_path: str = os.environ.get("DB_PATH", "./data/transcriber.sqlite3")
    max_queue_depth: int = int(os.environ.get("MAX_QUEUE_DEPTH", "5"))
    max_artifact_age_hours: int = int(os.environ.get("MAX_ARTIFACT_AGE_HOURS", "168"))
    max_artifacts: int = int(os.environ.get("MAX_ARTIFACTS", "100"))
    worker_poll_seconds: float = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
    heartbeat_seconds: float = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "5"))

def load_settings() -> Settings:
    return Settings()
```

```sql
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
);
CREATE INDEX IF NOT EXISTS jobs_status_created_idx ON jobs(status, created_at);
```

- [ ] **Step 4: Implement queue operations in `JobStore`**

```python
class JobStore:
    def create_job(self, youtube_url: str, language_hint: str | None) -> str: ...
    def list_recent_jobs(self, limit: int = 20) -> list[JobRecord]: ...
    def get_job(self, job_id: str) -> JobRecord | None: ...
    def claim_next_job(self, worker_id: str, heartbeat_timeout_seconds: int) -> JobRecord | None: ...
    def heartbeat(self, job_id: str, stage: str, message: str) -> None: ...
    def mark_completed(self, job_id: str, artifact_dir: str, display_title: str) -> None: ...
    def mark_failed(self, job_id: str, error_code: str, message: str) -> None: ...
    def requeue_stale_jobs(self, heartbeat_timeout_seconds: int) -> int: ...
```

Use `BEGIN IMMEDIATE` around `claim_next_job()` so the queue claim is atomic even if a second worker accidentally starts later.

- [ ] **Step 5: Ignore persistent local state**

```gitignore
data/
.superpowers/
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_job_store.py -v`
Expected: PASS for create/claim/list/requeue cases.

- [ ] **Step 7: Commit**

```bash
git add .gitignore whisper_transcriber/config.py whisper_transcriber/environment.py whisper_transcriber/db.py whisper_transcriber/job_store.py tests/test_job_store.py
git commit -m "feat: add sqlite job store"
```

### Task 3: Build Job Artifacts, Error Types, and Pipeline Orchestration

**Files:**
- Create: `whisper_transcriber/errors.py`
- Create: `whisper_transcriber/artifacts.py`
- Create: `whisper_transcriber/media.py`
- Create: `whisper_transcriber/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing pipeline tests**

```python
from whisper_transcriber.pipeline import run_job
from whisper_transcriber.errors import DownloadError

def test_run_job_writes_expected_artifacts(tmp_path):
    result = run_job(
        job_id="job-123",
        youtube_url="https://youtu.be/example",
        language_hint="en",
        artifacts_root=tmp_path,
        media_client=FakeMediaClient(),
        progress=events.append,
    )
    assert (tmp_path / "job-123" / "transcript.txt").exists()
    assert result["language"] == "en"

def test_download_errors_are_classified(tmp_path):
    failing = FakeMediaClient(download_error=RuntimeError("video unavailable"))
    with pytest.raises(DownloadError):
        run_job(..., media_client=failing, progress=lambda *_: None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL because the pipeline modules do not exist yet.

- [ ] **Step 3: Implement typed errors and artifact helpers**

```python
class PipelineError(RuntimeError):
    error_code = "pipeline_error"
    user_message = "Job failed."

class DownloadError(PipelineError):
    error_code = "download_failed"
    user_message = "Unable to download audio from the provided URL."

def job_artifact_dir(artifacts_root: str, job_id: str) -> Path:
    return Path(artifacts_root) / job_id
```

- [ ] **Step 4: Implement the media wrapper and `run_job()`**

```python
def run_job(*, job_id, youtube_url, language_hint, settings, media_client, progress):
    progress("downloading", "Downloading audio from YouTube")
    source_audio, display_title = media_client.download_best_audio(youtube_url)
    progress("converting", "Converting audio for Whisper")
    mp3_path = media_client.convert_to_mp3(source_audio)
    progress("transcribing", "Running Whisper transcription")
    result = media_client.transcribe(mp3_path, language_hint=language_hint)
    progress("writing", "Writing transcript artifacts")
    return write_job_artifacts(job_id=job_id, display_title=display_title, result=result, artifacts_root=settings.artifacts_dir)
```

Map raw exceptions to `DownloadError`, `ConversionError`, `TranscriptionError`, or `PersistenceError` close to the failing boundary so the worker can store a clean user-safe message.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS for both success and classified-failure cases.

- [ ] **Step 6: Commit**

```bash
git add whisper_transcriber/errors.py whisper_transcriber/artifacts.py whisper_transcriber/media.py whisper_transcriber/pipeline.py tests/test_pipeline.py
git commit -m "feat: add transcription pipeline"
```

### Task 4: Add the Worker Process and Crash Recovery

**Files:**
- Create: `whisper_transcriber/worker.py`
- Create: `worker.py`
- Create: `tests/test_worker.py`
- Modify: `whisper_transcriber/job_store.py`

- [ ] **Step 1: Write the failing worker tests**

```python
from whisper_transcriber.worker import process_one_job, recover_stale_jobs

def test_process_one_job_marks_job_completed(tmp_path):
    store = make_store(tmp_path)
    job_id = store.create_job("https://youtu.be/example", None)
    processed = process_one_job(store=store, settings=make_settings(tmp_path), media_client=FakeMediaClient(), worker_id="worker-1")
    assert processed is True
    assert store.get_job(job_id).status == "completed"

def test_recover_stale_jobs_requeues_old_running_jobs(tmp_path):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL because the worker implementation does not exist yet.

- [ ] **Step 3: Implement a single-job worker loop**

```python
def process_one_job(*, store, settings, media_client, worker_id: str) -> bool:
    job = store.claim_next_job(worker_id=worker_id, heartbeat_timeout_seconds=int(settings.heartbeat_seconds * 3))
    if job is None:
        return False
    try:
        result = run_job(..., progress=lambda stage, message: store.heartbeat(job.job_id, stage, message))
        store.mark_completed(job.job_id, artifact_dir=result["artifact_dir"], display_title=result["display_title"])
    except PipelineError as exc:
        store.mark_failed(job.job_id, exc.error_code, exc.user_message)
    return True
```

Also add:

```python
def main() -> None:
    settings = load_settings()
    init_db(settings.db_path)
    recovered = store.requeue_stale_jobs(heartbeat_timeout_seconds=int(settings.heartbeat_seconds * 3))
    while True:
        processed = process_one_job(...)
        if not processed:
            time.sleep(settings.worker_poll_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker.py -v`
Expected: PASS for completion and stale-job recovery cases.

- [ ] **Step 5: Commit**

```bash
git add worker.py whisper_transcriber/worker.py whisper_transcriber/job_store.py tests/test_worker.py
git commit -m "feat: add background worker"
```

### Task 5: Replace the Synchronous Web Flow With Durable Job Pages

**Files:**
- Create: `whisper_transcriber/web.py`
- Create: `templates/base.html`
- Create: `templates/index.html`
- Create: `templates/job_detail.html`
- Create: `templates/job_list.html`
- Create: `static/app.css`
- Create: `static/job-status.js`
- Create: `tests/test_web.py`
- Modify: `app.py:1-396`

- [ ] **Step 1: Write the failing web tests**

```python
def test_form_submission_creates_job_and_redirects(client):
    response = client.post("/jobs", data={"youtube_url": "https://youtu.be/example", "language": "en"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs/")

def test_api_transcribe_enqueues_instead_of_blocking(client):
    response = client.post("/api/transcribe", json={"youtube_url": "https://youtu.be/example", "language": "en"})
    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"

def test_job_detail_page_renders_progress(client, seeded_job):
    response = client.get(f"/jobs/{seeded_job.job_id}")
    assert "queued" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web.py -v`
Expected: FAIL because the web app factory and new routes do not exist yet.

- [ ] **Step 3: Implement the app factory and routes**

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="YouTube -> Whisper")
    templates = Jinja2Templates(directory="templates")
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.mount("/artifacts", StaticFiles(directory=settings.artifacts_dir), name="artifacts")

    @app.get("/")
    def index(request: Request): ...

    @app.post("/jobs")
    def create_job_from_form(...): ...

    @app.get("/jobs/{job_id}")
    def job_detail(...): ...

    @app.get("/jobs")
    def job_list(...): ...

    @app.get("/api/jobs/{job_id}")
    def job_status(...): ...

    @app.post("/api/transcribe")
    def create_job_json(payload: dict): ...

    @app.get("/healthz")
    def healthz(): ...
```

Keep `app.py` extremely small:

```python
from whisper_transcriber.web import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

- [ ] **Step 4: Build the templates and polling script**

```html
<!-- templates/job_detail.html -->
<section data-job-id="{{ job.job_id }}" data-status-api="{{ url_for('job_status', job_id=job.job_id) }}">
  <span class="job-badge">{{ job.progress_stage }}</span>
  <p id="status-message">{{ job.status_message }}</p>
  <div id="artifact-links">{% if artifacts %}...{% endif %}</div>
</section>
<script src="{{ url_for('static', path='job-status.js') }}"></script>
```

```js
// static/job-status.js
async function pollJob(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  renderStatus(payload);
  if (!["completed", "failed"].includes(payload.status)) {
    setTimeout(() => pollJob(url), 3000);
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web.py -v`
Expected: PASS for form submission, JSON enqueue, status polling, and job page rendering.

- [ ] **Step 6: Commit**

```bash
git add app.py whisper_transcriber/web.py templates/base.html templates/index.html templates/job_detail.html templates/job_list.html static/app.css static/job-status.js tests/test_web.py
git commit -m "feat: add durable job pages"
```

### Task 6: Add Retention, Queue Limits, Docs, and Final Verification

**Files:**
- Create: `README.md`
- Create: `tests/test_retention.py`
- Modify: `whisper_transcriber/artifacts.py`
- Modify: `whisper_transcriber/environment.py`
- Modify: `whisper_transcriber/web.py`
- Modify: `whisper_transcriber/worker.py`

- [ ] **Step 1: Write the failing operational tests**

```python
def test_prune_artifacts_removes_old_job_directories(tmp_path):
    removed = prune_artifacts(artifacts_root=tmp_path, max_age_hours=1, max_count=2, now=fixed_now)
    assert removed == ["job-old"]

def test_queue_limit_rejects_new_submissions(client, seeded_queue):
    response = client.post("/api/transcribe", json={"youtube_url": "https://youtu.be/overflow"})
    assert response.status_code == 429
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_retention.py tests/test_web.py::test_queue_limit_rejects_new_submissions -v`
Expected: FAIL because cleanup and queue limit enforcement are not implemented yet.

- [ ] **Step 3: Implement cleanup and queue-depth enforcement**

```python
def prune_artifacts(*, artifacts_root: str, max_age_hours: int, max_count: int, now: datetime) -> list[str]:
    ...

def queue_depth(self) -> int:
    return ...
```

Call cleanup:

- during web startup after environment checks
- after a worker finishes a job successfully

Reject new submissions when `queue_depth >= settings.max_queue_depth` with a friendly `429` response.

- [ ] **Step 4: Document operation and deployment**

`README.md` should cover:

- Python and `ffmpeg` prerequisites
- `pip install -r requirements.txt`
- `pip install -r requirements-dev.txt`
- `python app.py`
- `python worker.py`
- required env vars and defaults
- expected directory layout for `data/` and `artifacts/`
- health checks and troubleshooting
- recommendation to place the app behind VPN plus reverse-proxy basic auth

- [ ] **Step 5: Run the full automated suite**

Run: `pytest -v`
Expected: PASS across formatter, store, pipeline, worker, web, and retention tests.

- [ ] **Step 6: Run a manual smoke check**

Run:

```bash
python app.py
python worker.py
```

Manual verification:

- open `/`
- submit a real YouTube URL
- confirm redirect to `/jobs/<job_id>`
- confirm status transitions from `queued` to `completed`
- confirm artifact links work
- confirm `/jobs` shows the recent run
- confirm `/healthz` returns `ok`

- [ ] **Step 7: Commit**

```bash
git add README.md whisper_transcriber/artifacts.py whisper_transcriber/environment.py whisper_transcriber/web.py whisper_transcriber/worker.py tests/test_retention.py tests/test_web.py
git commit -m "feat: add operational safeguards"
```

## Final Verification Checklist

- [ ] `pytest -v`
- [ ] `python app.py`
- [ ] `python worker.py`
- [ ] Manual submit/complete flow verified with a real YouTube URL
- [ ] `/healthz` reports valid environment checks
- [ ] `/jobs` and `/jobs/<job_id>` survive refreshes and show persisted state
- [ ] Old artifacts are pruned according to configured retention

## Risks to Watch During Execution

- Do not accidentally leave synchronous `_process()` calls reachable from any route.
- Do not key artifact filenames by video title alone.
- Do not let the worker loop swallow exceptions without recording `last_error_code` and `last_error_message`.
- Do not add concurrency primitives that imply multiple simultaneous transcriptions.
- Do not build app-level auth unless the operator explicitly chooses it over reverse-proxy auth.

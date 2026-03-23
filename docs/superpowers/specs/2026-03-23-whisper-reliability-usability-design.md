# Whisper Reliability and Usability Design

## Summary

This document defines the next iteration of the YouTube-to-Whisper transcriber from a basic proof of concept into a small, durable internal service for a trusted group behind a VPN.

The current application is a synchronous single-file FastAPI app that accepts a YouTube URL, downloads audio, runs Whisper, and writes artifacts to local disk. It works as a demo, but it has weak operational boundaries: the request stays open for the full job duration, failures collapse into generic `500` responses, artifact names can collide, progress is invisible, and state disappears on refresh or restart.

The recommended design keeps the deployment intentionally simple while materially improving usability and reliability:

- keep one web service and one worker on the same host
- store durable job state in SQLite
- process one transcription job at a time with an explicit queue
- store artifacts and per-job logs under unique job directories on local disk
- present a dedicated job page with polling-based progress
- expose recent shared jobs for the small trusted group

## Goals

- Make transcription requests durable and recoverable across page refreshes and service restarts.
- Return control to the user immediately after submission instead of blocking the browser on a long POST.
- Provide clear progress stages and actionable failure messages.
- Avoid filename collisions and keep artifacts traceable to a specific run.
- Improve diagnostics and operational safety without adding unnecessary infrastructure.

## Non-Goals

- Internet-scale multi-tenant architecture
- Distributed queue infrastructure such as Redis/Celery
- Websocket-based live streaming updates
- Full user-account system
- Cloud object storage
- Automatic summarization, editing, or post-processing pipelines

## Deployment Assumptions

- The service is reachable only by a small trusted group behind a VPN.
- Usage is mostly one transcription at a time, with occasional queueing.
- The operator is willing to add a small database and worker process if it improves durability and job tracking.
- Shared visibility of recent jobs is acceptable for the initial version.

## Recommended Architecture

### Runtime Shape

Run two local processes on the same host:

1. A FastAPI web service for HTML pages and JSON endpoints
2. A single worker process that pulls queued jobs from SQLite and executes the transcription pipeline

Persist state in two places:

- SQLite for job metadata, progress, timestamps, and error state
- local disk for artifacts and per-job logs

This keeps the system small enough to operate comfortably on a home LAN host while removing the main failure modes of the current synchronous request model.

### Why This Over Simpler or Heavier Alternatives

#### Rejected: in-memory or file-only background queue

An in-process queue would be quick to build, but it would not survive process restarts cleanly, and it would make stale-job recovery, history pages, and auditability more fragile than necessary.

#### Rejected: Redis-backed queue stack

Redis plus RQ/Celery/Dramatiq would improve scale, but it adds operational surface area that the current trusted-group, mostly-single-job workload does not justify.

#### Chosen: SQLite plus one worker

SQLite-backed jobs hit the right balance:

- durable enough for refresh and restart recovery
- simple enough to run locally without extra services
- structured enough to support progress pages, retention, and debugging
- compatible with a future upgrade to a broker if concurrency requirements grow

## System Components

### Web Layer

The web layer is responsible for:

- input validation
- job creation
- rendering the submit page, job page, and recent-jobs page
- returning JSON for job status and operator diagnostics

The submission endpoint should create a job row and return or redirect immediately. It must not run the full download/transcribe flow inline.

Recommended interface split:

- `GET /` for the submit page
- `POST /jobs` for form submission
- `GET /jobs/<job_id>` for the durable job detail page
- `GET /jobs` for recent shared jobs
- `GET /api/jobs/<job_id>` for polling status
- `GET /healthz` for environment and operator checks

### Worker

The worker is responsible for:

- claiming the next queued job
- updating progress stage and status messages as it runs
- downloading audio with `yt-dlp`
- converting source media to a normalized MP3 with `ffmpeg`
- running `mlx_whisper`
- writing artifacts and logs
- finalizing success or classified failure state

Only one worker should execute at a time in this version. That aligns with the expected workload and avoids competing long-running ML jobs on the same machine.

### Storage

Artifacts should live under unique job directories:

`artifacts/<job_id>/`

Each job directory should contain:

- transcript text
- subtitle files
- segment JSON
- MP3 artifact
- optional job metadata snapshot
- per-job log file

The original video title is display metadata, not a storage key.

## Job Data Model

Each job should track at least:

- `job_id`
- `youtube_url`
- `display_title`
- `language_hint`
- `status`
- `progress_stage`
- `status_message`
- `created_at`
- `started_at`
- `finished_at`
- `attempt_count`
- `last_heartbeat_at`
- `last_error_code`
- `last_error_message`
- `artifact_dir`

Recommended status values:

- `queued`
- `running`
- `completed`
- `failed`

Recommended progress stages:

- `queued`
- `downloading`
- `converting`
- `transcribing`
- `writing`
- `completed`
- `failed`

The status should represent coarse job state, while `progress_stage` and `status_message` provide user-facing detail.

## Request and Job Lifecycle

### Submission Flow

1. User submits a YouTube URL and optional language hint.
2. Server validates the request.
3. Server inserts a new SQLite job row with generated `job_id` and `queued` state.
4. Server redirects to a dedicated job detail page or returns the `job_id` in JSON.
5. The worker later claims the job and begins processing.

### Execution Flow

1. Worker selects the next queued job.
2. Worker marks it `running`, sets stage `downloading`, and writes heartbeat updates.
3. Worker downloads source audio.
4. Worker updates stage to `converting` and produces normalized MP3.
5. Worker updates stage to `transcribing` and runs Whisper.
6. Worker updates stage to `writing` and persists all artifacts.
7. Worker marks the job `completed` and records final metadata.

### Failure Flow

Failures should be classified by stage:

- validation
- download
- conversion
- transcription
- persistence

For each failed job, store:

- a user-safe message suitable for HTML/API responses
- a more detailed internal error or traceback in logs

The UI should never show raw Python exceptions directly.

## Restart Recovery and Retry Behavior

The worker needs minimal crash recovery behavior:

- use a heartbeat or lease field while a job is running
- on startup, scan for jobs marked `running` with stale heartbeats
- requeue stale jobs if they have not exceeded retry policy
- otherwise mark them failed with a restart-recovery error note

Retry policy should be conservative:

- allow a limited automatic retry for likely transient download failures
- do not blindly retry transcription/model failures

This keeps recovery behavior predictable and avoids infinite retry loops on bad inputs or broken local model setups.

## User Experience

### Submit Page

Keep the entry experience simple:

- YouTube URL field
- optional language hint
- submit button

Do not add model selection yet unless a real use case exists. The server model should remain operator-controlled for now.

### Dedicated Job Page

After submission, redirect the user to a stable job page:

`/jobs/<job_id>`

This page should:

- poll for job status every few seconds
- show current stage and status message
- survive page refresh cleanly
- show final artifacts when the job completes
- show a clear failure message and operator-friendly reference if the job fails

### Completion View

When a job completes, show:

- detected language
- transcript preview
- artifact download links
- copy-to-clipboard action for transcript text
- lightweight metadata such as runtime and file size where useful

### Recent Shared Jobs

Because the service is intended for a small trusted group behind the VPN, the initial design assumes recent jobs can be visible to all users of the app.

The recent-jobs view should show:

- title or label
- status
- creation time
- duration if completed
- direct link to job detail

If privacy concerns emerge later, per-user isolation can be added as a follow-up rather than front-loading account complexity now.

## Access Control

VPN access reduces exposure, but it should not be the only boundary by default.

The recommended v1 control is one of:

- reverse-proxy basic auth
- a shared application password

Full per-user accounts and authorization rules are intentionally out of scope for this version.

If the operator chooses to rely on VPN-only access, that should be an explicit decision with the tradeoff understood: anyone on the VPN can submit jobs and view shared job history.

## Operational Safeguards

Add guardrails appropriate for a trusted but imperfect internal environment:

- queue depth limit
- request validation for URL length and shape
- artifact retention by age and/or count
- per-job disk budget awareness where practical
- startup environment validation
- bounded cleanup of old artifacts and stale logs

These safeguards are primarily about preventing accidental resource exhaustion, not hostile internet abuse.

## Observability

The service does not need full metrics infrastructure yet, but it does need better visibility.

Add:

- structured logs tagged with `job_id`
- per-job log files stored with artifacts
- stdout log summaries so the service is operable under `systemd`, Docker, or a reverse proxy setup
- `GET /healthz` for environment validation
- `GET /jobs/<job_id>` for detailed status
- `GET /jobs` for recent history

Useful operator-level signals include:

- current queue length
- average job runtime
- failures by stage
- last successful job time

## Testing Strategy

The current single-file structure should be decomposed enough that the workflow can be tested without real network or model execution.

### Unit Tests

Add unit coverage for:

- job state transitions
- artifact path generation
- subtitle and timestamp formatting
- retention cleanup
- stale-job recovery
- error classification

### Integration Tests

Add mocked integration coverage for:

- submission creates queued job
- worker claims and runs queued job
- stage transitions are written correctly
- successful runs produce expected artifact metadata
- failures surface user-safe messages and internal details

Mock:

- `yt_dlp`
- `ffmpeg`
- `mlx_whisper`

This should allow reliable automated tests without depending on live YouTube availability or local model downloads.

### Manual Smoke Verification

Keep one optional manual smoke path for real local verification:

- submit a real YouTube URL
- confirm job queueing, status updates, final transcript, and artifact downloads
- confirm restart behavior on an interrupted job if practical

## Rollout Plan

### Phase 1

- refactor synchronous processing into durable queued jobs
- add SQLite-backed job tracking
- add the single worker process
- add schema initialization or migration setup for predictable startup
- keep the basic form-based UI

### Phase 2

- add dedicated job pages
- add recent-job history
- improve failure presentation
- add artifact retention and cleanup
- add structured logs and per-job logs

### Phase 3

Only if the trusted group actually needs it:

- search or filtering for prior jobs
- duplicate detection for repeated URLs
- per-user history or access separation
- more advanced operator views

## Design Summary

The next version should not try to become a general-purpose transcription platform. It should become a dependable small internal service with clear state, predictable behavior, and enough operational structure to recover from ordinary failures. SQLite-backed jobs, a single worker, unique artifact directories, and polling-based job pages provide the biggest reliability and usability gains with the least unnecessary complexity.

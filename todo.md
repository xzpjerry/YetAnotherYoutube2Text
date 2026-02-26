# Local Reliability and UX Improvement Plan

1. Stabilize startup checks first (high impact, low effort)
- Add a startup validator in `app.py` to fail fast with clear messages when `ffmpeg` is missing, model path is invalid, or `artifacts/` is not writable.
- Add `/healthz` for quick local diagnostics.

2. Prevent output overwrites (high impact, low effort)
- Use a per-run `job_id` (timestamp + short UUID) and prefix all artifact filenames with it instead of only title-based names.
- Return `job_id` in API/UI responses so outputs are always traceable.

3. Add artifact retention policy (high impact, low effort)
- Implement auto-prune by age/count using env vars (example: `MAX_ARTIFACT_AGE_HOURS`, `MAX_ARTIFACTS`).
- Run cleanup at startup and after each transcription so local disk usage stays bounded.

4. Improve error messages for local debugging (high impact, medium effort)
- Catch common failures separately (`yt_dlp` download issues, `ffmpeg` conversion failures, model-load/transcribe errors) and return actionable messages in UI/API.
- Keep raw exception details in a per-job log file in `artifacts/`.

5. Make transcription non-blocking with progress (medium impact, medium effort)
- Move processing to background jobs with a simple in-memory queue.
- Add `POST /api/transcribe` -> `job_id`, and `GET /api/jobs/{job_id}` for status polling.
- Update the page to show states: downloading, converting, transcribing, writing files.

6. Add minimal test coverage (medium impact, medium effort)
- Create tests for `_safe_slug`, `_format_ts`, `_to_srt`, `_to_vtt`, and a mocked `_process` flow.
- This catches regressions without requiring real YouTube/network/model runs.

7. Improve local DX docs (medium impact, low effort)
- Add `README.md` with: prerequisites, setup, run commands, env vars, common failures, and expected output paths.

Suggested execution order:
- Phase 1: steps 1-4 (fastest reliability gain)
- Phase 2: steps 5-7

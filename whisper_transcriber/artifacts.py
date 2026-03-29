from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from whisper_transcriber.formatters import to_srt, to_vtt


@dataclass(frozen=True)
class JobArtifacts:
    artifact_dir: Path
    audio_path: Path
    transcript_path: Path
    srt_path: Path
    vtt_path: Path
    segments_path: Path


def prepare_job_artifacts(artifacts_root: str | Path, job_id: str) -> JobArtifacts:
    artifact_dir = Path(artifacts_root) / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return JobArtifacts(
        artifact_dir=artifact_dir,
        audio_path=artifact_dir / "audio.mp3",
        transcript_path=artifact_dir / "transcript.txt",
        srt_path=artifact_dir / "subtitles.srt",
        vtt_path=artifact_dir / "subtitles.vtt",
        segments_path=artifact_dir / "segments.json",
    )


def write_job_outputs(
    artifacts: JobArtifacts,
    *,
    text: str,
    segments: list[dict[str, Any]],
) -> JobArtifacts:
    artifacts.transcript_path.write_text(text, encoding="utf-8")
    artifacts.srt_path.write_text(to_srt(segments), encoding="utf-8")
    artifacts.vtt_path.write_text(to_vtt(segments), encoding="utf-8")
    artifacts.segments_path.write_text(
        json.dumps(segments, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifacts

from __future__ import annotations


class PipelineError(RuntimeError):
    code = "pipeline_error"
    stage = "pipeline"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.code
        self.stage = stage or self.stage


class DownloadError(PipelineError):
    code = "download_error"
    stage = "downloading"


class ConversionError(PipelineError):
    code = "conversion_error"
    stage = "converting"


class TranscriptionError(PipelineError):
    code = "transcription_error"
    stage = "transcribing"


class PersistenceError(PipelineError):
    code = "persistence_error"
    stage = "writing"

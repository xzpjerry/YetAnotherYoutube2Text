from __future__ import annotations


class PipelineError(RuntimeError):
    error_code = "pipeline_error"
    stage = "pipeline"
    user_message = "Job failed."

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        stage: str | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code or self.error_code
        self.stage = stage or self.stage
        self.user_message = user_message or self.user_message

    @property
    def code(self) -> str:
        return self.error_code


class DownloadError(PipelineError):
    error_code = "download_error"
    stage = "downloading"
    user_message = "Unable to download audio from the provided URL."


class ConversionError(PipelineError):
    error_code = "conversion_error"
    stage = "converting"
    user_message = "Unable to convert the downloaded audio."


class TranscriptionError(PipelineError):
    error_code = "transcription_error"
    stage = "transcribing"
    user_message = "Unable to transcribe the audio."


class PersistenceError(PipelineError):
    error_code = "persistence_error"
    stage = "writing"
    user_message = "Unable to write transcription artifacts."

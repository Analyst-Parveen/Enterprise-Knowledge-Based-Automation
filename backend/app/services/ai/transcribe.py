"""Amazon Transcribe: audio and video -> transcript.

Transcribe accepts mp4/mov/webm and reads the audio track itself, so no ffmpeg
dependency is needed anywhere in this project.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import boto3
import httpx
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import UpstreamError
from app.core.logging import get_logger

logger = get_logger(__name__)

_POLL_SECONDS = 5
_MAX_WAIT_SECONDS = 900  # 15 min ceiling - demo clips should be short

_client = None


def _transcribe():  # type: ignore[no-untyped-def]
    global _client
    if _client is None:
        _client = boto3.client(
            "transcribe",
            config=BotoConfig(
                region_name=settings.transcribe_region,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    return _client


async def transcribe_media(source_uri: str, language_code: str = "en-US") -> str:
    """Run a transcription job to completion and return the transcript text.

    In local dev with AI_PROVIDER=local this returns a labelled placeholder so
    the ingestion pipeline is exercisable offline at $0.
    """
    if settings.ai_provider == "local":
        logger.warning(
            "transcribe_stubbed",
            extra={"extra": {"warning": "AI_PROVIDER=local - returning placeholder transcript"}},
        )
        return (
            "[local-dev-stub] Transcript placeholder for media ingested during offline "
            "development. Set AI_PROVIDER=bedrock for real Amazon Transcribe output."
        )

    job_name = f"{settings.project_code}-{uuid.uuid4().hex}"

    def _start() -> None:
        _transcribe().start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": source_uri},
            LanguageCode=language_code,
        )

    try:
        await asyncio.to_thread(_start)
    except (ClientError, BotoCoreError) as exc:
        logger.error("transcribe_start_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("The transcription service is unavailable.") from exc

    waited = 0
    while waited < _MAX_WAIT_SECONDS:
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS

        def _poll() -> dict:
            return _transcribe().get_transcription_job(TranscriptionJobName=job_name)

        try:
            job = (await asyncio.to_thread(_poll))["TranscriptionJob"]
        except (ClientError, BotoCoreError) as exc:
            raise UpstreamError("The transcription service is unavailable.") from exc

        status = job["TranscriptionJobStatus"]
        if status == "COMPLETED":
            transcript_uri = job["Transcript"]["TranscriptFileUri"]
            return await _fetch_transcript(transcript_uri)
        if status == "FAILED":
            reason = job.get("FailureReason", "unknown")
            logger.error("transcribe_job_failed", extra={"extra": {"reason": reason}})
            raise UpstreamError("Transcription failed for this media file.")

    raise UpstreamError("Transcription timed out.")


async def _fetch_transcript(url: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = json.loads(response.text)

    transcripts = payload.get("results", {}).get("transcripts", [])
    return " ".join(t.get("transcript", "") for t in transcripts).strip()

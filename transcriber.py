"""Speech-to-text using OpenAI Whisper API."""
import os
import math
import re
from pydub import AudioSegment
from openai import OpenAI

MAX_FILE_SIZE = 24 * 1024 * 1024  # 24MB (leave margin under 25MB limit)


def transcribe(audio_path: str, client: OpenAI) -> list[dict]:
    """Transcribe audio file using Whisper API.

    Automatically splits large files into chunks.

    Returns:
        List of segments: [{"start": float, "end": float, "text": str}, ...]
    """
    file_size = os.path.getsize(audio_path)

    if file_size <= MAX_FILE_SIZE:
        return _transcribe_file(audio_path, client, offset=0.0)

    # Split large file into chunks
    return _transcribe_large_file(audio_path, client)


def _transcribe_file(audio_path: str, client: OpenAI, offset: float) -> list[dict]:
    """Transcribe a single audio file."""
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = []
    for seg in response.segments:
        text = seg.text.strip()
        # Remove control characters that can break downstream JSON
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        if not text:
            continue
        segments.append({
            "start": round(seg.start + offset, 3),
            "end": round(seg.end + offset, 3),
            "text": text,
        })

    return segments


def _transcribe_large_file(audio_path: str, client: OpenAI) -> list[dict]:
    """Split large audio file and transcribe each chunk."""
    audio = AudioSegment.from_wav(audio_path)
    file_size = os.path.getsize(audio_path)
    duration_ms = len(audio)

    # Calculate chunk duration to stay under size limit
    bytes_per_ms = file_size / duration_ms
    chunk_duration_ms = int(MAX_FILE_SIZE / bytes_per_ms)

    num_chunks = math.ceil(duration_ms / chunk_duration_ms)
    all_segments = []

    temp_dir = os.path.dirname(audio_path)

    for i in range(num_chunks):
        start_ms = i * chunk_duration_ms
        end_ms = min(start_ms + chunk_duration_ms, duration_ms)
        chunk = audio[start_ms:end_ms]

        chunk_path = os.path.join(temp_dir, f"chunk_{i}.wav")
        chunk.export(chunk_path, format="wav")

        try:
            offset_sec = start_ms / 1000.0
            segments = _transcribe_file(chunk_path, client, offset=offset_sec)
            all_segments.extend(segments)
        finally:
            os.remove(chunk_path)

    return all_segments

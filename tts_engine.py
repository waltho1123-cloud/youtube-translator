"""Text-to-Speech using ElevenLabs API."""
import os
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from elevenlabs.client import ElevenLabs

log = logging.getLogger("pipeline")

# Pre-made voice presets (name → voice_id)
VOICE_PRESETS = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",   # Female, calm
    "josh": "TxGEqnHWrfWFTfGW9XjX",     # Male, deep
    "bella": "EXAVITQu4vr4xnSDxMaL",    # Female, soft
    "antoni": "ErXwobaYiN019PkySvjV",    # Male, friendly
    "adam": "pNInz6obpgDQGcFmaJgB",      # Male, deep
    "sam": "yoZ06aMxZJJ28mfd3POQ",       # Male, raspy
}


def resolve_voice_id(voice: str) -> str:
    """Resolve voice name to ID. Accepts preset name or raw voice_id."""
    return VOICE_PRESETS.get(voice.lower(), voice)


def _adjust_speed(audio_path: str, speed_factor: float):
    """Adjust audio playback speed using ffmpeg atempo filter."""
    if speed_factor < 0.5 or speed_factor > 4.0:
        return

    temp_path = audio_path + ".tmp.wav"

    filters = []
    remaining = speed_factor
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining *= 2.0
    filters.append(f"atempo={remaining:.4f}")

    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-filter:a", ",".join(filters), temp_path],
        capture_output=True,
        check=True,
        timeout=60,  # 1 分鐘超時
    )
    os.replace(temp_path, audio_path)


def _get_wav_duration(path: str) -> float:
    """Get WAV file duration in seconds using wave module."""
    import wave
    with wave.open(path, "r") as wf:
        return wf.getnframes() / wf.getframerate()


def _generate_one(
    idx: int,
    seg: dict,
    client: ElevenLabs,
    output_dir: str,
    voice_id: str,
    model_id: str,
) -> dict:
    """Generate TTS for a single segment."""
    mp3_file = os.path.join(output_dir, f"tts_{idx:04d}.mp3")
    wav_file = os.path.join(output_dir, f"tts_{idx:04d}.wav")
    target_duration = seg["end"] - seg["start"]

    try:
        # Generate audio via ElevenLabs
        audio_gen = client.text_to_speech.convert(
            voice_id=voice_id,
            text=seg["translated"],
            model_id=model_id,
            output_format="mp3_22050_32",
        )

        with open(mp3_file, "wb") as f:
            for chunk in audio_gen:
                f.write(chunk)

        # Convert mp3 → wav (16kHz mono)
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_file, "-ar", "16000", "-ac", "1", wav_file],
            capture_output=True,
            check=True,
            timeout=60,  # 1 分鐘超時
        )
        os.remove(mp3_file)

        # Adjust speed if TTS audio is too long for the time slot
        actual_duration = _get_wav_duration(wav_file)
        if actual_duration > target_duration * 1.15 and target_duration > 0.5:
            speed_factor = actual_duration / target_duration
            _adjust_speed(wav_file, speed_factor)

        return {**seg, "tts_path": wav_file}

    except Exception as e:
        log.warning(f"[TTS] segment {idx} 語音合成失敗: {e}")
        for f in [mp3_file, wav_file]:
            if os.path.exists(f):
                os.remove(f)
        return {**seg, "tts_path": None}


def generate_tts_batch(
    segments: list[dict],
    api_key: str,
    output_dir: str,
    voice: str = "rachel",
    model_id: str = "eleven_multilingual_v2",
    max_workers: int = 3,
    on_progress=None,
) -> list[dict]:
    """Generate TTS audio for all segments concurrently.

    Args:
        segments: Translated segments
        api_key: ElevenLabs API key
        output_dir: Directory for TTS audio files
        voice: Voice preset name or voice_id
        model_id: ElevenLabs model
        max_workers: Concurrent TTS requests
        on_progress: Callback(completed_count, total)

    Returns:
        Segments with added "tts_path" field
    """
    tts_dir = os.path.join(output_dir, "tts")
    os.makedirs(tts_dir, exist_ok=True)

    client = ElevenLabs(api_key=api_key)
    voice_id = resolve_voice_id(voice)

    results = [None] * len(segments)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i, seg in enumerate(segments):
            future = executor.submit(
                _generate_one, i, seg, client, tts_dir, voice_id, model_id
            )
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {**segments[idx], "tts_path": None}
                log.warning(f"[TTS] segment {idx} 執行失敗: {e}")

            completed += 1
            if on_progress:
                on_progress(completed, len(segments))

    return results

"""Text-to-Speech using MiniMax API."""
import os
import json
import logging
import subprocess
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("pipeline")

# MiniMax 語音預設 (name → voice_id)
VOICE_PRESETS = {
    "female_sichuan": "female-tianmei-jingpin",    # 女聲 - 甜美
    "female_gentle": "female-shaonv-jingpin",      # 女聲 - 少女
    "female_news": "female-yujie-jingpin",         # 女聲 - 御姐
    "male_news": "male-qn-jingpin",                # 男聲 - 青年
    "male_deep": "male-qn-daxuesheng-jingpin",     # 男聲 - 大學生
    "presenter_male": "presenter_male",            # 男聲 - 主持人
    "presenter_female": "presenter_female",        # 女聲 - 主持人
}

MINIMAX_TTS_URL = "https://api.minimaxi.chat/v1/t2a_v2"


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

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-filter:a", ",".join(filters), temp_path],
            capture_output=True,
            check=True,
            timeout=60,
        )
        os.replace(temp_path, audio_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _get_wav_duration(path: str) -> float:
    """Get WAV file duration in seconds using wave module."""
    import wave
    with wave.open(path, "r") as wf:
        return wf.getnframes() / wf.getframerate()


def _generate_one(
    idx: int,
    seg: dict,
    api_key: str,
    group_id: str,
    output_dir: str,
    voice_id: str,
) -> dict:
    """Generate TTS for a single segment using MiniMax API."""
    mp3_file = os.path.join(output_dir, f"tts_{idx:04d}.mp3")
    wav_file = os.path.join(output_dir, f"tts_{idx:04d}.wav")
    target_duration = seg["end"] - seg["start"]

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "speech-02-hd",
            "text": seg["translated"],
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
            },
        }

        url = MINIMAX_TTS_URL
        if group_id:
            url = f"{url}?GroupId={group_id}"

        resp = None
        for attempt in range(3):
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < 2:
                    import time
                    time.sleep(2 ** attempt)  # 1s, 2s
                    continue
            break
        resp.raise_for_status()

        result = resp.json()
        if result.get("base_resp", {}).get("status_code", 0) != 0:
            err_msg = result.get("base_resp", {}).get("status_msg", "Unknown error")
            raise RuntimeError(f"MiniMax API error: {err_msg}")

        # MiniMax 回傳 data.audio 為 hex 編碼的音訊字串
        audio_hex = result.get("data", {}).get("audio", "")
        if not audio_hex:
            raise RuntimeError("No audio data in MiniMax response")

        with open(mp3_file, "wb") as f:
            f.write(bytes.fromhex(audio_hex))

        # Convert mp3 → wav (16kHz mono)
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_file, "-ar", "16000", "-ac", "1", wav_file],
            capture_output=True,
            check=True,
            timeout=60,
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
    voice: str = "female_sichuan",
    group_id: str = "",
    max_workers: int = 3,
    on_progress=None,
) -> list[dict]:
    """Generate TTS audio for all segments concurrently.

    Args:
        segments: Translated segments
        api_key: MiniMax API key
        output_dir: Directory for TTS audio files
        voice: Voice preset name or voice_id
        group_id: MiniMax group ID
        max_workers: Concurrent TTS requests
        on_progress: Callback(completed_count, total)

    Returns:
        Segments with added "tts_path" field
    """
    tts_dir = os.path.join(output_dir, "tts")
    os.makedirs(tts_dir, exist_ok=True)

    voice_id = resolve_voice_id(voice)

    results = [None] * len(segments)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i, seg in enumerate(segments):
            future = executor.submit(
                _generate_one, i, seg, api_key, group_id, tts_dir, voice_id
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

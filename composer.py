"""Video composition - merge Chinese TTS audio with original video."""
import logging
import os
import subprocess
from pydub import AudioSegment

log = logging.getLogger("pipeline")


def _get_duration_ms(video_path: str) -> int:
    """Get media file duration in milliseconds using ffmpeg."""
    import re
    result = subprocess.run(
        ["ffmpeg", "-i", video_path],
        capture_output=True,
        text=True,
        timeout=600,  # 10 分鐘超時
    )
    # Parse duration from ffmpeg stderr: "Duration: HH:MM:SS.xx"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr)
    if not match:
        raise RuntimeError(f"Cannot determine duration of {video_path}")
    h, m, s, cs = int(match[1]), int(match[2]), int(match[3]), int(match[4])
    return (h * 3600 + m * 60 + s) * 1000 + cs * 10


def _generate_srt(segments: list[dict], srt_path: str, field: str = "translated"):
    """Generate SRT subtitle file from segments.

    Args:
        segments: List of segments
        srt_path: Output SRT path
        field: Which field to use as subtitle text ('translated' for Chinese, 'text' for English)
    """
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            text = seg.get(field, "")
            if not text:
                continue
            start = _format_srt_time(seg["start"])
            end = _format_srt_time(seg["end"])
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")


def _format_srt_time(seconds: float) -> str:
    """Format seconds to SRT time format: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def compose_video(
    video_path: str,
    segments: list[dict],
    output_path: str,
    original_volume: float = 0.15,
    subtitle: bool = False,
    eng_subtitle: bool = False,
    accompaniment_path: str = None,
) -> str:
    """Compose final video with Chinese audio overlay.

    Args:
        video_path: Path to original video
        segments: Segments with tts_path field
        output_path: Output mp4 path
        original_volume: Volume of original audio (0.0-1.0)
        subtitle: Whether to burn Chinese subtitles into the video
        eng_subtitle: Whether to burn English subtitles into the video
        accompaniment_path: Path to separated accompaniment track (background music/sfx)

    Returns:
        Path to output video
    """
    duration_ms = _get_duration_ms(video_path)

    # Build Chinese audio track
    chinese_track = AudioSegment.silent(duration=duration_ms, frame_rate=16000)

    placed = 0
    log.info(f"[Compose] Total segments: {len(segments)}, track duration: {duration_ms}ms")
    for seg in segments:
        tts_path = seg.get("tts_path")
        if not tts_path or not os.path.exists(tts_path):
            continue

        tts_audio = AudioSegment.from_wav(tts_path)
        target_ms = int((seg["end"] - seg["start"]) * 1000)

        # Truncate if still longer than slot
        if len(tts_audio) > target_ms > 0:
            tts_audio = tts_audio[:target_ms]

        position_ms = int(seg["start"] * 1000)

        # Prevent overflow
        if position_ms + len(tts_audio) > duration_ms:
            tts_audio = tts_audio[: duration_ms - position_ms]

        chinese_track = chinese_track.overlay(tts_audio, position=position_ms)
        placed += 1
        log.info(f"  [Compose] Placed seg at {position_ms}ms, duration={len(tts_audio)}ms")

    log.info(f"[Compose] Placed {placed} segments onto Chinese track")
    # Export Chinese track as WAV
    base, _ = os.path.splitext(output_path)
    temp_audio = base + "_cn_track.wav"
    chinese_track.export(temp_audio, format="wav")

    # Generate SRT if subtitles requested
    burn_any = subtitle or eng_subtitle
    zh_srt_path = base + "_zh.srt"
    en_srt_path = base + "_en.srt"
    if subtitle:
        _generate_srt(segments, zh_srt_path, field="translated")
    if eng_subtitle:
        _generate_srt(segments, en_srt_path, field="text")

    # --- Build ffmpeg command ---
    inputs = ["-i", video_path, "-i", temp_audio]
    # input 0 = video, input 1 = Chinese TTS
    bg_input_idx = None
    if accompaniment_path and os.path.exists(accompaniment_path):
        inputs += ["-i", accompaniment_path]
        bg_input_idx = 2  # input 2 = accompaniment

    # Video filter (subtitles)
    sub_filters = []
    if burn_any:
        if subtitle:
            margin_v_zh = 50 if eng_subtitle else 25
            escaped_zh = zh_srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            sub_filters.append(
                f"subtitles='{escaped_zh}':force_style="
                f"'FontSize=24,FontName=Noto Sans CJK TC,PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV={margin_v_zh}'"
            )
        if eng_subtitle:
            escaped_en = en_srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            sub_filters.append(
                f"subtitles='{escaped_en}':force_style="
                f"'FontSize=20,FontName=Arial,PrimaryColour=&H00CCCCCC,"
                f"OutlineColour=&H00000000,Outline=1,Shadow=1,MarginV=25'"
            )

    # Audio filter
    audio_parts = []
    if bg_input_idx is not None:
        # Use separated accompaniment + TTS
        audio_parts.append(f"[{bg_input_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bg]")
        audio_parts.append("[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[cn]")
        audio_parts.append("[bg][cn]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    elif original_volume > 0:
        # Mix original audio (lowered) + TTS
        audio_parts.append(f"[0:a]volume={original_volume}[orig]")
        audio_parts.append("[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[cn]")
        audio_parts.append("[orig][cn]amix=inputs=2:duration=first:dropout_transition=0[aout]")

    has_audio_filter = len(audio_parts) > 0
    needs_reencode = burn_any  # subtitle burn requires re-encode

    # Build filter_complex
    filter_parts = []
    if sub_filters:
        filter_parts.append(f"[0:v]{','.join(sub_filters)}[vout]")
    if audio_parts:
        filter_parts.extend(audio_parts)

    if filter_parts:
        filter_complex = ";".join(filter_parts)
        v_map = "[vout]" if sub_filters else "0:v"
        a_map = "[aout]" if has_audio_filter else "1:a"
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", v_map, "-map", a_map,
            "-c:v", "libx264" if needs_reencode else "copy",
            "-preset", "ultrafast", "-crf", "26",
            "-threads", "2",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_path,
        ]
    else:
        # No filters: just replace audio
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", output_path,
        ]

    subprocess.run(cmd, capture_output=True, check=True, timeout=600)  # 10 分鐘超時

    # Clean up temp files
    os.remove(temp_audio)
    for p in [zh_srt_path, en_srt_path]:
        if os.path.exists(p):
            os.remove(p)

    return output_path

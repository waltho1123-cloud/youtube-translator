"""YouTube video downloader using pytubefix."""
import os
import subprocess

from pytubefix import YouTube


def download_audio_only(url: str, output_dir: str) -> dict:
    """Download only audio from YouTube for transcription (much faster).

    Returns:
        dict with audio_path, title, duration
    """
    os.makedirs(output_dir, exist_ok=True)

    audio_raw = os.path.join(output_dir, "source_audio_raw.mp4")
    audio_path = os.path.join(output_dir, "source_audio.wav")

    for p in [audio_raw, audio_path]:
        if os.path.exists(p):
            os.remove(p)

    try:
        yt = YouTube(url)
    except Exception as e:
        raise RuntimeError(f"無法載入影片，請確認連結是否正確: {str(e)[:100]}")

    if yt.length and yt.length > 1800:
        raise ValueError("影片過長（超過 30 分鐘），請選擇較短的影片")

    stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if stream is None:
        raise RuntimeError("No audio stream found")

    stream.download(output_path=output_dir, filename="source_audio_raw.mp4")

    # Convert to 16kHz mono WAV (optimal for Whisper)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_raw,
            "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
            audio_path,
        ],
        capture_output=True,
        check=True,
        timeout=300,  # 5 分鐘超時
    )
    os.remove(audio_raw)

    return {
        "audio_path": audio_path,
        "title": yt.title,
        "duration": yt.length,
    }


def download_video(url: str, output_dir: str, quality: str = "720") -> dict:
    """Download video from YouTube and extract audio as WAV.

    Args:
        quality: Target resolution - "1080", "720", or "480"

    Returns:
        dict with video_path, audio_path, title, duration
    """
    os.makedirs(output_dir, exist_ok=True)

    video_path = os.path.join(output_dir, "source_video.mp4")
    audio_path = os.path.join(output_dir, "source_audio.wav")

    # Clean up old files
    for p in [video_path, audio_path]:
        if os.path.exists(p):
            os.remove(p)

    # Download video using pytubefix
    try:
        yt = YouTube(url)
    except Exception as e:
        raise RuntimeError(f"無法載入影片，請確認連結是否正確: {str(e)[:100]}")

    if yt.length and yt.length > 1800:
        raise ValueError("影片過長（超過 30 分鐘），請選擇較短的影片")

    target_res = f"{quality}p"

    # Try progressive stream (video+audio combined) at target resolution
    stream = (
        yt.streams.filter(progressive=True, file_extension="mp4", resolution=target_res)
        .first()
    )

    if stream:
        # Progressive stream found - simple download
        stream.download(output_path=output_dir, filename="source_video.mp4")
    else:
        # Use adaptive stream (video-only) + separate audio, then merge
        video_stream = (
            yt.streams.filter(adaptive=True, file_extension="mp4", resolution=target_res)
            .first()
        )
        # Fallback: pick highest resolution up to target
        if video_stream is None:
            candidates = (
                yt.streams.filter(adaptive=True, file_extension="mp4")
                .order_by("resolution")
                .desc()
            )
            for s in candidates:
                res_num = int(s.resolution.replace("p", "")) if s.resolution else 0
                if res_num <= int(quality):
                    video_stream = s
                    break
            if video_stream is None and candidates:
                video_stream = candidates.last()  # lowest available

        if video_stream is None:
            # Last resort: any MP4 stream
            video_stream = (
                yt.streams.filter(file_extension="mp4")
                .order_by("resolution")
                .desc()
                .first()
            )

        if video_stream is None:
            raise RuntimeError("No suitable video stream found")

        audio_stream = (
            yt.streams.filter(only_audio=True)
            .order_by("abr")
            .desc()
            .first()
        )

        tmp_video = os.path.join(output_dir, "_tmp_video.mp4")
        tmp_audio_dl = os.path.join(output_dir, "_tmp_audio.mp4")

        video_stream.download(output_path=output_dir, filename="_tmp_video.mp4")

        if audio_stream:
            audio_stream.download(output_path=output_dir, filename="_tmp_audio.mp4")
            # Merge video + audio with ffmpeg
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", tmp_video,
                    "-i", tmp_audio_dl,
                    "-c:v", "copy", "-c:a", "aac",
                    video_path,
                ],
                capture_output=True,
                check=True,
                timeout=300,  # 5 分鐘超時
            )
            os.remove(tmp_video)
            os.remove(tmp_audio_dl)
        else:
            os.rename(tmp_video, video_path)

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video download failed: {video_path}")

    # Extract audio as 16kHz mono WAV (optimal for Whisper)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
            audio_path,
        ],
        capture_output=True,
        check=True,
        timeout=300,  # 5 分鐘超時
    )

    return {
        "video_path": video_path,
        "audio_path": audio_path,
        "title": yt.title,
        "duration": yt.length,
    }

"""YouTube video downloader using yt-dlp (primary) with pytubefix fallback."""
import json
import logging
import os
import subprocess
import tempfile

log = logging.getLogger("pipeline")


def write_cookies_file(cookies_text: str, output_dir: str) -> str | None:
    """Write Netscape-format cookies to a temp file for yt-dlp.

    Returns the file path, or None if cookies_text is empty.
    """
    if not cookies_text or not cookies_text.strip():
        return None
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "cookies.txt")
    with open(path, "w") as f:
        f.write(cookies_text)
    return path


def _download_audio_ytdlp(url: str, output_dir: str, cookies_file: str = None) -> dict:
    """Download audio using yt-dlp (more reliable)."""
    audio_raw = os.path.join(output_dir, "source_audio_raw")

    # First get video info
    log.info(f"[Download] yt-dlp: fetching info for {url} (cookies={'yes' if cookies_file else 'no'})")
    cmd_info = ["yt-dlp", "--dump-json", "--no-download",
                "--no-check-certificates", "--no-warnings"]
    if cookies_file:
        cmd_info += ["--cookies", cookies_file]
    cmd_info.append(url)
    result = subprocess.run(cmd_info, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp info failed: {result.stderr[:300]}")

    info = json.loads(result.stdout)
    title = info.get("title", "Unknown")
    duration = info.get("duration", 0)

    if duration and duration > 1800:
        raise ValueError("影片過長（超過 30 分鐘），請選擇較短的影片")

    # Download best audio
    log.info(f"[Download] yt-dlp: downloading audio for '{title}'")
    cmd_dl = [
        "yt-dlp", "-f", "bestaudio",
        "-o", audio_raw + ".%(ext)s",
        "--no-playlist",
        "--no-check-certificates",
    ]
    if cookies_file:
        cmd_dl += ["--cookies", cookies_file]
    cmd_dl.append(url)
    result = subprocess.run(cmd_dl, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed: {result.stderr[:300]}")

    # Find the downloaded file
    downloaded = None
    for f in os.listdir(output_dir):
        if f.startswith("source_audio_raw."):
            downloaded = os.path.join(output_dir, f)
            break

    if not downloaded or not os.path.exists(downloaded):
        raise RuntimeError("yt-dlp: downloaded file not found")

    return {"raw_path": downloaded, "title": title, "duration": duration}


def _download_audio_pytubefix(url: str, output_dir: str) -> dict:
    """Download audio using pytubefix (fallback)."""
    from pytubefix import YouTube

    audio_raw = os.path.join(output_dir, "source_audio_raw.mp4")

    try:
        yt = YouTube(url)
    except Exception as e:
        raise RuntimeError(f"無法載入影片: {str(e)[:100]}")

    if yt.length and yt.length > 1800:
        raise ValueError("影片過長（超過 30 分鐘），請選擇較短的影片")

    stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if stream is None:
        raise RuntimeError("No audio stream found")

    stream.download(output_path=output_dir, filename="source_audio_raw.mp4")

    return {"raw_path": audio_raw, "title": yt.title, "duration": yt.length}


def download_audio_only(url: str, output_dir: str, cookies_file: str = None) -> dict:
    """Download only audio from YouTube for transcription (much faster).

    Uses yt-dlp as primary downloader, falls back to pytubefix.

    Returns:
        dict with audio_path, title, duration
    """
    os.makedirs(output_dir, exist_ok=True)

    audio_path = os.path.join(output_dir, "source_audio.wav")
    if os.path.exists(audio_path):
        os.remove(audio_path)

    # Try yt-dlp first (more reliable), fallback to pytubefix
    result = None
    yt_dlp_err = None
    try:
        result = _download_audio_ytdlp(url, output_dir, cookies_file=cookies_file)
        log.info("[Download] yt-dlp audio succeeded")
    except Exception as e:
        yt_dlp_err = e
        log.warning(f"[Download] yt-dlp failed: {e}, trying pytubefix")
        try:
            result = _download_audio_pytubefix(url, output_dir)
            log.info("[Download] pytubefix audio succeeded")
        except Exception as e2:
            log.error(f"[Download] Both downloaders failed. yt-dlp: {e}, pytubefix: {e2}")
            raise RuntimeError(
                f"影片下載失敗（兩種下載方式皆失敗）\n"
                f"yt-dlp: {str(e)[:100]}\n"
                f"pytubefix: {str(e2)[:100]}"
            )

    raw_path = result["raw_path"]
    log.info(f"[Download] Converting {raw_path} to 16kHz WAV")

    # Convert to 16kHz mono WAV (optimal for Whisper)
    ffmpeg_result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", raw_path,
            "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
            audio_path,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if ffmpeg_result.returncode != 0:
        log.error(f"[Download] ffmpeg conversion failed: {ffmpeg_result.stderr[:300]}")
        raise RuntimeError(f"音訊轉換失敗: {ffmpeg_result.stderr[:200]}")
    if os.path.exists(raw_path):
        os.remove(raw_path)

    return {
        "audio_path": audio_path,
        "title": result["title"],
        "duration": result["duration"],
    }


def _download_video_ytdlp(url: str, output_dir: str, quality: str, cookies_file: str = None) -> dict:
    """Download video using yt-dlp."""
    video_path = os.path.join(output_dir, "source_video.mp4")

    # Get info first
    cmd_info = ["yt-dlp", "--dump-json", "--no-download",
                "--no-check-certificates", "--no-warnings"]
    if cookies_file:
        cmd_info += ["--cookies", cookies_file]
    cmd_info.append(url)
    result = subprocess.run(cmd_info, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp info failed: {result.stderr[:300]}")

    info = json.loads(result.stdout)
    title = info.get("title", "Unknown")
    duration = info.get("duration", 0)

    if duration and duration > 1800:
        raise ValueError("影片過長（超過 30 分鐘），請選擇較短的影片")

    # Download video+audio merged
    height = quality  # e.g. "720"
    cmd_dl = [
        "yt-dlp",
        "-f", f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
        "--merge-output-format", "mp4",
        "-o", video_path,
        "--no-playlist",
        "--no-check-certificates",
    ]
    if cookies_file:
        cmd_dl += ["--cookies", cookies_file]
    cmd_dl.append(url)
    result = subprocess.run(cmd_dl, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed: {result.stderr[:300]}")

    if not os.path.exists(video_path):
        raise FileNotFoundError("yt-dlp: video file not found after download")

    return {"video_path": video_path, "title": title, "duration": duration}


def _download_video_pytubefix(url: str, output_dir: str, quality: str) -> dict:
    """Download video using pytubefix (fallback)."""
    from pytubefix import YouTube

    video_path = os.path.join(output_dir, "source_video.mp4")

    try:
        yt = YouTube(url)
    except Exception as e:
        raise RuntimeError(f"無法載入影片: {str(e)[:100]}")

    if yt.length and yt.length > 1800:
        raise ValueError("影片過長（超過 30 分鐘），請選擇較短的影片")

    target_res = f"{quality}p"

    stream = (
        yt.streams.filter(progressive=True, file_extension="mp4", resolution=target_res)
        .first()
    )

    if stream:
        stream.download(output_path=output_dir, filename="source_video.mp4")
    else:
        video_stream = (
            yt.streams.filter(adaptive=True, file_extension="mp4", resolution=target_res)
            .first()
        )
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
                video_stream = candidates.last()

        if video_stream is None:
            video_stream = (
                yt.streams.filter(file_extension="mp4")
                .order_by("resolution").desc().first()
            )

        if video_stream is None:
            raise RuntimeError("No suitable video stream found")

        audio_stream = (
            yt.streams.filter(only_audio=True).order_by("abr").desc().first()
        )

        tmp_video = os.path.join(output_dir, "_tmp_video.mp4")
        tmp_audio_dl = os.path.join(output_dir, "_tmp_audio.mp4")
        video_stream.download(output_path=output_dir, filename="_tmp_video.mp4")

        if audio_stream:
            audio_stream.download(output_path=output_dir, filename="_tmp_audio.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_video, "-i", tmp_audio_dl,
                 "-c:v", "copy", "-c:a", "aac", video_path],
                capture_output=True, check=True, timeout=300,
            )
            os.remove(tmp_video)
            os.remove(tmp_audio_dl)
        else:
            os.rename(tmp_video, video_path)

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video download failed: {video_path}")

    return {"video_path": video_path, "title": yt.title, "duration": yt.length}


def download_video(url: str, output_dir: str, quality: str = "720", cookies_file: str = None) -> dict:
    """Download video from YouTube and extract audio as WAV.

    Uses yt-dlp as primary downloader, falls back to pytubefix.

    Args:
        quality: Target resolution - "1080", "720", or "480"
        cookies_file: Path to Netscape-format cookies file

    Returns:
        dict with video_path, audio_path, title, duration
    """
    os.makedirs(output_dir, exist_ok=True)

    audio_path = os.path.join(output_dir, "source_audio.wav")
    video_path = os.path.join(output_dir, "source_video.mp4")

    for p in [video_path, audio_path]:
        if os.path.exists(p):
            os.remove(p)

    # Try yt-dlp first, fallback to pytubefix
    result = None
    try:
        result = _download_video_ytdlp(url, output_dir, quality, cookies_file=cookies_file)
        log.info("[Download] yt-dlp video succeeded")
    except Exception as e:
        log.warning(f"[Download] yt-dlp video failed: {e}, trying pytubefix")
        try:
            result = _download_video_pytubefix(url, output_dir, quality)
            log.info("[Download] pytubefix video succeeded")
        except Exception as e2:
            log.error(f"[Download] Both video downloaders failed. yt-dlp: {e}, pytubefix: {e2}")
            raise RuntimeError(
                f"影片下載失敗（兩種下載方式皆失敗）\n"
                f"yt-dlp: {str(e)[:100]}\n"
                f"pytubefix: {str(e2)[:100]}"
            )

    # Extract audio as 16kHz mono WAV (optimal for Whisper)
    log.info("[Download] Extracting audio from video")
    ffmpeg_result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", result["video_path"],
            "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
            audio_path,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if ffmpeg_result.returncode != 0:
        log.error(f"[Download] ffmpeg audio extraction failed: {ffmpeg_result.stderr[:300]}")
        raise RuntimeError(f"音訊轉換失敗: {ffmpeg_result.stderr[:200]}")

    return {
        "video_path": result["video_path"],
        "audio_path": audio_path,
        "title": result["title"],
        "duration": result["duration"],
    }

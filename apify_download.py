"""YouTube download via Apify actors — reliable cloud-based alternative to yt-dlp."""
import logging
import os
import re
import requests
import time

log = logging.getLogger("pipeline")

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")

# Actor IDs
TRANSCRIPT_ACTOR = "pintostudio~youtube-transcript-scraper"

# Download actors with verified input formats
AUDIO_ACTORS = [
    {
        "id": "api-ninja~youtube-video-downloader",
        "input": lambda url: {"urls": [url], "format": "mp3", "quality": "128kbps"},
    },
]

VIDEO_ACTORS = [
    {
        "id": "api-ninja~youtube-video-downloader",
        "input": lambda url, q: {"urls": [url], "format": q},  # format: "720", "1080", etc.
    },
]


def _extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL."""
    m = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else ""


def get_transcript(url: str) -> list:
    """Get YouTube transcript via Apify (cheapest, most reliable).

    Returns:
        List of segments: [{"start": float, "end": float, "text": str}, ...]
        Empty list if no transcript available.
    """
    if not APIFY_TOKEN:
        log.warning("[Apify] No APIFY_TOKEN set, skipping transcript fetch")
        return []

    video_id = _extract_video_id(url)
    log.info(f"[Apify] Fetching transcript for {video_id}")

    try:
        resp = requests.post(
            f"https://api.apify.com/v2/acts/{TRANSCRIPT_ACTOR}/run-sync-get-dataset-items",
            headers={
                "Authorization": f"Bearer {APIFY_TOKEN}",
                "Content-Type": "application/json",
            },
            params={"timeout": 60},
            json={"urls": [url]},
            timeout=90,
        )
        resp.raise_for_status()
        items = resp.json()

        if not items:
            log.info("[Apify] No transcript data returned")
            return []

        # Parse transcript segments — format varies by actor
        segments = []
        for item in items:
            # The actor may return transcript as a list of segments
            transcript_data = item.get("transcript") or item.get("captions") or []
            if isinstance(transcript_data, str):
                # Plain text transcript without timestamps — not usable
                log.info("[Apify] Transcript is plain text (no timestamps), skipping")
                return []

            for seg in transcript_data:
                start = float(seg.get("start", seg.get("offset", 0)))
                duration = float(seg.get("duration", seg.get("dur", 0)))
                text = seg.get("text", "").strip()
                if text:
                    segments.append({
                        "start": round(start, 3),
                        "end": round(start + duration, 3),
                        "text": text,
                    })

        if segments:
            log.info(f"[Apify] Got {len(segments)} transcript segments")
        else:
            log.info("[Apify] Transcript returned but no usable segments")
        return segments

    except requests.exceptions.Timeout:
        log.warning("[Apify] Transcript fetch timed out")
        return []
    except Exception as e:
        log.warning(f"[Apify] Transcript fetch failed: {e}")
        return []


def _find_download_url(item: dict) -> str:
    """Extract download URL from Apify result item (handles various actor formats)."""
    # Direct URL fields
    for key in ("downloadUrl", "audioUrl", "url", "mediaUrl", "fileUrl"):
        val = item.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val

    # Nested in media/files arrays
    for key in ("media", "files", "downloads"):
        nested = item.get(key)
        if isinstance(nested, list) and nested:
            for sub in nested:
                if isinstance(sub, dict):
                    for k in ("url", "link", "downloadUrl"):
                        val = sub.get(k)
                        if val and isinstance(val, str) and val.startswith("http"):
                            return val

    # Key-value store (some actors store file in KV store)
    return ""


def download_audio(url: str, output_dir: str, on_progress=None) -> dict:
    """Download YouTube audio via Apify actors (async with polling).

    Args:
        on_progress: optional callback(message) called every few seconds to keep SSE alive

    Returns:
        dict with raw_path, title, duration
    Raises:
        RuntimeError if all actors fail
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN not configured")

    video_id = _extract_video_id(url)
    log.info(f"[Apify] Downloading audio for {video_id}")
    os.makedirs(output_dir, exist_ok=True)

    last_error = None
    headers = {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Content-Type": "application/json",
    }

    for actor in AUDIO_ACTORS:
        actor_id = actor["id"]
        actor_input = actor["input"](url)
        log.info(f"[Apify] Trying actor {actor_id}")

        try:
            # Step 1: Start async run
            resp = requests.post(
                f"https://api.apify.com/v2/acts/{actor_id}/runs",
                headers=headers,
                json=actor_input,
                timeout=30,
            )
            resp.raise_for_status()
            run_data = resp.json()["data"]
            run_id = run_data["id"]
            dataset_id = run_data["defaultDatasetId"]
            log.info(f"[Apify] Run {run_id} started")

            # Step 2: Poll for completion (max 3 min), emit progress to keep SSE alive
            for i in range(36):  # 36 * 5s = 180s
                time.sleep(5)
                if on_progress and i % 2 == 0:
                    on_progress(f"Apify 下載中... ({i * 5}s)")
                try:
                    status_resp = requests.get(
                        f"https://api.apify.com/v2/actor-runs/{run_id}",
                        headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
                        timeout=10,
                    )
                    status = status_resp.json()["data"]["status"]
                    if status == "SUCCEEDED":
                        break
                    if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                        raise RuntimeError(f"Apify run {status}")
                except RuntimeError:
                    raise
                except Exception:
                    continue
            else:
                raise RuntimeError("Apify run timed out (3 min)")

            # Step 3: Get results from dataset
            items_resp = requests.get(
                f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
                timeout=30,
            )
            items_resp.raise_for_status()
            items = items_resp.json()

            if not items:
                log.warning(f"[Apify] {actor_id} returned no results")
                last_error = f"{actor_id}: no results"
                continue

            item = items[0]
            title = item.get("title", "Unknown")
            duration = item.get("duration", 0)
            log.info(f"[Apify] {actor_id} result keys: {list(item.keys())}")

            download_url = _find_download_url(item)
            if not download_url:
                log.warning(f"[Apify] {actor_id}: no download URL in result")
                last_error = f"{actor_id}: no download URL"
                continue

            # Step 4: Download the audio file
            if on_progress:
                on_progress("下載音訊檔案中...")
            log.info(f"[Apify] Downloading file for '{title}'")
            audio_path = os.path.join(output_dir, "source_audio_raw.mp3")
            audio_resp = requests.get(download_url, timeout=120, stream=True)
            audio_resp.raise_for_status()
            with open(audio_path, "wb") as f:
                for chunk in audio_resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            file_size = os.path.getsize(audio_path)
            if file_size < 1000:
                last_error = f"{actor_id}: file too small ({file_size}B)"
                continue

            log.info(f"[Apify] Downloaded {file_size / 1024:.0f} KB: '{title}' ({duration}s)")
            return {"raw_path": audio_path, "title": title, "duration": duration}

        except Exception as e:
            last_error = f"{actor_id}: {e}"
            log.warning(f"[Apify] {actor_id} failed: {e}")
            continue

    raise RuntimeError(f"Apify download failed: {last_error}")


def download_video(url: str, output_dir: str, quality: str = "720", on_progress=None) -> dict:
    """Download YouTube video (MP4) via Apify.

    Returns:
        dict with raw_path, title, duration
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN not configured")

    video_id = _extract_video_id(url)
    log.info(f"[Apify] Downloading video for {video_id} (quality={quality})")
    os.makedirs(output_dir, exist_ok=True)

    headers = {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Content-Type": "application/json",
    }
    last_error = None

    for actor in VIDEO_ACTORS:
        actor_id = actor["id"]
        actor_input = actor["input"](url, quality)
        log.info(f"[Apify] Trying video actor {actor_id}")

        try:
            resp = requests.post(
                f"https://api.apify.com/v2/acts/{actor_id}/runs",
                headers=headers,
                json=actor_input,
                timeout=30,
            )
            resp.raise_for_status()
            run_data = resp.json()["data"]
            run_id = run_data["id"]
            dataset_id = run_data["defaultDatasetId"]

            for i in range(60):  # 5 min max
                time.sleep(5)
                if on_progress and i % 2 == 0:
                    on_progress(f"Apify 影片下載中... ({i * 5}s)")
                try:
                    sr = requests.get(
                        f"https://api.apify.com/v2/actor-runs/{run_id}",
                        headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
                        timeout=10,
                    )
                    status = sr.json()["data"]["status"]
                    if status == "SUCCEEDED":
                        break
                    if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                        raise RuntimeError(f"Apify run {status}")
                except RuntimeError:
                    raise
                except Exception:
                    continue
            else:
                raise RuntimeError("Apify run timed out")

            items_resp = requests.get(
                f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
                timeout=30,
            )
            items_resp.raise_for_status()
            items = items_resp.json()
            if not items:
                last_error = f"{actor_id}: no results"
                continue

            item = items[0]
            title = item.get("title", "Unknown")
            duration = item.get("duration", 0)
            download_url = _find_download_url(item)
            if not download_url:
                last_error = f"{actor_id}: no download URL"
                continue

            if on_progress:
                on_progress("下載影片檔案中...")
            video_path = os.path.join(output_dir, "source_video.mp4")
            vr = requests.get(download_url, timeout=300, stream=True)
            vr.raise_for_status()
            with open(video_path, "wb") as f:
                for chunk in vr.iter_content(chunk_size=8192):
                    f.write(chunk)

            file_size = os.path.getsize(video_path)
            if file_size < 10000:
                last_error = f"{actor_id}: file too small ({file_size}B)"
                continue

            log.info(f"[Apify] Video downloaded {file_size / 1024 / 1024:.1f} MB: '{title}'")
            return {"raw_path": video_path, "title": title, "duration": duration}

        except Exception as e:
            last_error = f"{actor_id}: {e}"
            log.warning(f"[Apify] {actor_id} video failed: {e}")
            continue

    raise RuntimeError(f"Apify video download failed: {last_error}")

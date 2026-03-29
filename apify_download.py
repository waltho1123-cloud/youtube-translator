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
DOWNLOADER_ACTOR = "streamers~youtube-video-downloader"


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


def download_audio(url: str, output_dir: str) -> dict:
    """Download YouTube audio via Apify actor.

    Returns:
        dict with audio_path, title, duration
    Raises:
        RuntimeError if download fails
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN not configured")

    video_id = _extract_video_id(url)
    log.info(f"[Apify] Downloading audio for {video_id}")

    os.makedirs(output_dir, exist_ok=True)

    # Start the actor run
    try:
        resp = requests.post(
            f"https://api.apify.com/v2/acts/{DOWNLOADER_ACTOR}/runs",
            headers={
                "Authorization": f"Bearer {APIFY_TOKEN}",
                "Content-Type": "application/json",
            },
            params={"timeout": 120},
            json={
                "urls": [url],
                "format": "mp3",
                "quality": "128",
            },
            timeout=30,
        )
        resp.raise_for_status()
        run_data = resp.json()["data"]
        run_id = run_data["id"]
        dataset_id = run_data["defaultDatasetId"]
    except Exception as e:
        raise RuntimeError(f"Apify actor start failed: {e}")

    # Poll for completion (max 3 minutes)
    log.info(f"[Apify] Run {run_id} started, waiting for completion...")
    for _ in range(36):  # 36 * 5s = 180s
        time.sleep(5)
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

    # Get results from dataset
    try:
        items_resp = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
            timeout=30,
        )
        items_resp.raise_for_status()
        items = items_resp.json()
    except Exception as e:
        raise RuntimeError(f"Apify dataset fetch failed: {e}")

    if not items:
        raise RuntimeError("Apify returned no results")

    item = items[0]
    title = item.get("title", "Unknown")
    duration = item.get("duration", 0)

    # Find the download URL for the audio file
    download_url = item.get("url") or item.get("downloadUrl") or item.get("audioUrl")
    if not download_url:
        # Check nested structures
        media = item.get("media") or item.get("files") or []
        if isinstance(media, list) and media:
            download_url = media[0].get("url") or media[0].get("link")

    if not download_url:
        log.error(f"[Apify] No download URL in result: {list(item.keys())}")
        raise RuntimeError("Apify: no download URL in result")

    # Download the audio file
    log.info(f"[Apify] Downloading audio from URL for '{title}'")
    audio_path = os.path.join(output_dir, "source_audio_raw.mp3")
    try:
        audio_resp = requests.get(download_url, timeout=120, stream=True)
        audio_resp.raise_for_status()
        with open(audio_path, "wb") as f:
            for chunk in audio_resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Apify audio download failed: {e}")

    file_size = os.path.getsize(audio_path)
    log.info(f"[Apify] Downloaded {file_size / 1024:.0f} KB audio: '{title}' ({duration}s)")

    return {
        "raw_path": audio_path,
        "title": title,
        "duration": duration,
    }

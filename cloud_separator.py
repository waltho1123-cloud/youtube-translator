"""Cloud-based vocal separation using Replicate (hosted Demucs model)."""
import logging
import os
import requests
import time

log = logging.getLogger("pipeline")

REPLICATE_TOKEN = os.getenv("REPLICATE_TOKEN", "")
DEMUCS_VERSION = "25a173108cff36ef9f80f854c162d01df9e6528be175794b81571db22a44d927"
API_BASE = "https://api.replicate.com/v1"


def is_available() -> bool:
    return bool(REPLICATE_TOKEN)


def separate_vocals(audio_path: str, output_dir: str, on_progress=None) -> dict:
    """Separate vocals from accompaniment using Replicate's Demucs model.

    Args:
        audio_path: Path to input audio file
        output_dir: Directory to save output files
        on_progress: Optional callback(message) for progress updates

    Returns:
        dict with "vocals" and "accompaniment" file paths
    """
    if not REPLICATE_TOKEN:
        raise RuntimeError("REPLICATE_TOKEN not configured")

    headers = {"Authorization": f"Bearer {REPLICATE_TOKEN}"}
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Upload audio file to Replicate
    log.info(f"[CloudSep] Uploading {audio_path} to Replicate...")
    if on_progress:
        on_progress("上傳音訊到雲端...")

    with open(audio_path, "rb") as f:
        upload_resp = requests.post(
            f"{API_BASE}/files",
            headers=headers,
            files={"content": (os.path.basename(audio_path), f)},
            timeout=120,
        )
    upload_resp.raise_for_status()
    file_url = upload_resp.json()["urls"]["get"]
    log.info(f"[CloudSep] Uploaded, file URL ready")

    # Step 2: Create prediction
    if on_progress:
        on_progress("啟動雲端音源分離...")
    pred_resp = requests.post(
        f"{API_BASE}/predictions",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "version": DEMUCS_VERSION,
            "input": {
                "audio": file_url,
                "stem": "vocals",  # two-stems mode: vocals + no_vocals
            },
        },
        timeout=30,
    )
    pred_resp.raise_for_status()
    pred_id = pred_resp.json()["id"]
    log.info(f"[CloudSep] Prediction {pred_id} created")

    # Step 3: Poll for completion
    for i in range(60):  # 60 * 5s = 5 min max
        time.sleep(5)
        if on_progress and i % 2 == 0:
            on_progress(f"雲端音源分離中... ({i * 5}s)")

        status_resp = requests.get(
            f"{API_BASE}/predictions/{pred_id}",
            headers=headers,
            timeout=10,
        )
        data = status_resp.json()
        status = data["status"]

        if status == "succeeded":
            output = data["output"]
            log.info(f"[CloudSep] Succeeded. Output keys: {list(output.keys()) if isinstance(output, dict) else type(output)}")
            break
        elif status in ("failed", "canceled"):
            error = data.get("error", "Unknown error")
            raise RuntimeError(f"Replicate prediction failed: {error}")
    else:
        raise RuntimeError("Replicate prediction timed out (5 min)")

    # Step 4: Download separated audio files
    if on_progress:
        on_progress("下載分離結果...")

    vocals_path = os.path.join(output_dir, "vocals.wav")
    accompaniment_path = os.path.join(output_dir, "accompaniment.wav")

    # Output format depends on the model; handle both dict and direct URL
    if isinstance(output, dict):
        vocals_url = output.get("vocals", "")
        accomp_url = output.get("no_vocals") or output.get("other") or output.get("accompaniment", "")
    elif isinstance(output, str):
        # Single output URL
        vocals_url = output
        accomp_url = ""
    else:
        raise RuntimeError(f"Unexpected output format: {type(output)}")

    for url, path, label in [
        (vocals_url, vocals_path, "vocals"),
        (accomp_url, accompaniment_path, "accompaniment"),
    ]:
        if url:
            log.info(f"[CloudSep] Downloading {label}...")
            resp = requests.get(url, timeout=120, stream=True)
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            log.info(f"[CloudSep] {label}: {os.path.getsize(path) / 1024:.0f} KB")

    result = {}
    if os.path.exists(vocals_path):
        result["vocals"] = vocals_path
    if os.path.exists(accompaniment_path):
        result["accompaniment"] = accompaniment_path

    if not result:
        raise RuntimeError("No output files downloaded from Replicate")

    log.info(f"[CloudSep] Done: {list(result.keys())}")
    return result

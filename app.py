#!/usr/bin/env python3
"""Web interface for YouTube English-to-Chinese Video Translator."""
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid

import logging

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from werkzeug.utils import secure_filename

load_dotenv()

logging.basicConfig(
    filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline.log"),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    force=True,
)
log = logging.getLogger("pipeline")

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory job store
jobs = {}

# 允許使用的模型白名單
ALLOWED_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"}


def _cleanup_old_jobs(max_age=3600):
    """清理超過 max_age 秒的已完成任務，防止 jobs 字典無限成長。"""
    now = time.time()
    expired = [
        jid for jid, jdata in jobs.items()
        if jdata.get("completed_at") and now - jdata["completed_at"] > max_age
    ]
    for jid in expired:
        del jobs[jid]


_YT_URL_RE = re.compile(
    r'^https?://(www\.)?(youtube\.com/(watch\?.*v=|embed/|shorts/)|youtu\.be/)[a-zA-Z0-9_-]{11}'
)


def _validate_youtube_url(url: str) -> bool:
    return bool(_YT_URL_RE.match(url))


@app.route("/health")
def health():
    return jsonify({"status": "ok", "active_jobs": len(jobs)})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/translate", methods=["POST"])
def start_translate():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not _validate_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    # 清理過期的已完成任務
    _cleanup_old_jobs()

    voice = data.get("voice", "rachel")
    volume = max(0.0, min(1.0, float(data.get("volume", 0.15))))
    model = data.get("model", "gpt-4o")
    # 驗證模型是否在白名單中，不在則使用預設值
    if model not in ALLOWED_MODELS:
        model = "gpt-4o"
    subtitle = bool(data.get("subtitle", False))
    eng_subtitle = bool(data.get("eng_subtitle", False))
    keep_bg = bool(data.get("keep_bg", False))
    quality = data.get("quality", "720")
    if quality not in ("1080", "720", "480"):
        quality = "720"

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"events": queue.Queue()}

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, url, voice, volume, model, subtitle, quality, eng_subtitle, keep_bg),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/live-translate", methods=["POST"])
def start_live_translate():
    """Live voice translation: download audio, transcribe, translate, TTS."""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not _validate_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    # 清理過期的已完成任務
    _cleanup_old_jobs()

    model = data.get("model", "gpt-4o")
    # 驗證模型是否在白名單中，不在則使用預設值
    if model not in ALLOWED_MODELS:
        model = "gpt-4o"
    voice = data.get("voice", "rachel")
    keep_bg = bool(data.get("keep_bg", False))

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"events": queue.Queue()}

    thread = threading.Thread(
        target=_run_live_pipeline,
        args=(job_id, url, model, voice, keep_bg),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/tts/<job_id>/<filename>")
def serve_tts(job_id, filename):
    """Serve TTS audio files for live playback."""
    # 驗證完整 UUID 格式（8-4-4-4-12 hex）
    if not re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', job_id):
        return "Invalid job ID", 400
    filename = secure_filename(filename)
    if not filename:
        return "Invalid filename", 400
    tts_dir = os.path.join(TEMP_DIR, f"live_{job_id}", "tts")
    filepath = os.path.realpath(os.path.join(tts_dir, filename))
    if not filepath.startswith(os.path.realpath(tts_dir)):
        return "Forbidden", 403
    if os.path.exists(filepath):
        mime = "audio/wav" if filename.endswith(".wav") else "audio/mpeg"
        return send_from_directory(tts_dir, filename, mimetype=mime)
    return "Not found", 404


@app.route("/api/progress/<job_id>")
def progress(job_id):
    """SSE endpoint for real-time progress."""
    def generate():
        job = jobs.get(job_id)
        if not job:
            yield f"data: {json.dumps({'status': 'error', 'message': 'Job not found'})}\n\n"
            return

        while True:
            try:
                event = job["events"].get(timeout=60)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("status") in ("completed", "error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'status': 'heartbeat'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/download/<filename>")
def download(filename):
    filename = secure_filename(filename)
    if not filename:
        return "Invalid filename", 400
    filepath = os.path.realpath(os.path.join(OUTPUT_DIR, filename))
    if not filepath.startswith(os.path.realpath(OUTPUT_DIR)):
        return "Forbidden", 403
    if os.path.exists(filepath):
        return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)
    return "File not found", 404


def _emit(job_id, status, message, progress=0, **kwargs):
    event = {"status": status, "message": message, "progress": progress, **kwargs}
    jobs[job_id]["events"].put(event)
    # 當任務完成或出錯時標記時間戳，供 _cleanup_old_jobs 清理
    if status in ("completed", "error"):
        jobs[job_id]["completed_at"] = time.time()


def _run_pipeline(job_id, url, voice, volume, model, subtitle=False, quality="720", eng_subtitle=False, keep_bg=False):
    """Run the full translation pipeline in a background thread."""
    try:
        # Add project dir to path for imports
        import sys
        sys.path.insert(0, BASE_DIR)

        from openai import OpenAI
        from downloader import download_video
        from transcriber import transcribe
        from translator import translate_segments
        from tts_engine import generate_tts_batch
        from composer import compose_video
        from separator import separate_vocals

        openai_key = os.getenv("OPENAI_API_KEY")
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")

        if not openai_key or not elevenlabs_key:
            _emit(job_id, "error", "API keys not configured in .env")
            return

        client = OpenAI(api_key=openai_key)

        # Use per-job temp directory to avoid concurrent conflicts
        job_temp = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_temp, exist_ok=True)

        # Step 1: Download
        _emit(job_id, "processing", "downloading", 5, step="download")
        video_info = download_video(url, job_temp, quality=quality)
        _emit(
            job_id, "processing", "downloaded", 18,
            step="download",
            title=video_info["title"],
            duration=video_info["duration"],
        )

        # Step 1.5: Separate vocals if keep_bg enabled
        accompaniment_path = None
        if keep_bg:
            _emit(job_id, "processing", "separating", 18, step="separate")
            separated = separate_vocals(video_info["audio_path"], job_temp)
            accompaniment_path = separated["accompaniment"]
            _emit(job_id, "processing", "separated", 22, step="separate")

        # Step 2: Transcribe
        _emit(job_id, "processing", "transcribing", 22, step="transcribe")
        segments = transcribe(video_info["audio_path"], client)
        _emit(
            job_id, "processing", "transcribed", 38,
            step="transcribe",
            segment_count=len(segments),
        )

        # Step 3: Translate
        _emit(job_id, "processing", "translating", 40, step="translate")

        def on_translate(batch, total):
            p = 40 + int((batch / total) * 22)
            _emit(job_id, "processing", "translating", p, step="translate",
                  batch=batch, total_batches=total)

        translated = translate_segments(
            segments, client, model=model, on_progress=on_translate
        )

        preview = [
            {"start": s["start"], "end": s["end"],
             "en": s["text"], "zh": s["translated"]}
            for s in translated[:8]
        ]
        _emit(job_id, "processing", "translated", 62,
              step="translate", preview=preview, total_segments=len(translated))

        # Step 4: TTS
        _emit(job_id, "processing", "synthesizing", 64, step="tts")

        def on_tts(completed, total):
            p = 64 + int((completed / total) * 26)
            _emit(job_id, "processing", "synthesizing", p, step="tts",
                  completed=completed, tts_total=total)

        tts_segments = generate_tts_batch(
            translated, elevenlabs_key, job_temp,
            voice=voice, max_workers=3, on_progress=on_tts,
        )

        success = sum(1 for s in tts_segments if s.get("tts_path"))
        log.info(f"[TTS] {success}/{len(tts_segments)} segments generated successfully")
        if success == 0:
            _emit(job_id, "error", "TTS 語音合成全部失敗，請檢查 ElevenLabs API Key 是否正確")
            return
        _emit(job_id, "processing", "synthesized", 90,
              step="tts", tts_success=success, tts_total=len(tts_segments))

        # Step 5: Compose
        _emit(job_id, "processing", "composing", 92, step="compose")

        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_"
            for c in video_info["title"]
        )[:60]
        out_path = os.path.join(OUTPUT_DIR, f"{safe_title}_cn.mp4")

        compose_video(video_info["video_path"], tts_segments, out_path, volume,
                      subtitle=subtitle, eng_subtitle=eng_subtitle,
                      accompaniment_path=accompaniment_path)

        # Clean up job temp directory only
        shutil.rmtree(job_temp, ignore_errors=True)

        filename = os.path.basename(out_path)
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        _emit(job_id, "completed", "done", 100,
              filename=filename, size_mb=round(size_mb, 1))

    except Exception as e:
        log.exception(f"[Pipeline] Job {job_id} failed")
        _emit(job_id, "error", "處理過程發生錯誤，請稍後重試", 0)


def _run_live_pipeline(job_id, url, model, voice, keep_bg=False):
    """Run live voice translation pipeline: audio → transcribe → translate → TTS."""
    try:
        import sys
        sys.path.insert(0, BASE_DIR)

        from openai import OpenAI
        from downloader import download_audio_only
        from transcriber import transcribe
        from translator import translate_segments
        from tts_engine import generate_tts_batch
        from separator import separate_vocals

        openai_key = os.getenv("OPENAI_API_KEY")
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")

        if not openai_key or not elevenlabs_key:
            _emit(job_id, "error", "API keys not configured in .env")
            return

        client = OpenAI(api_key=openai_key)

        # Step 1: Download audio only (fast)
        # Use per-job temp directory
        job_temp = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_temp, exist_ok=True)

        _emit(job_id, "processing", "downloading_audio", 5, step="download")
        audio_info = download_audio_only(url, job_temp)
        _emit(
            job_id, "processing", "downloaded", 15,
            step="download",
            title=audio_info["title"],
            duration=audio_info["duration"],
        )

        # Step 1.5: Separate vocals if keep_bg enabled
        accompaniment_url = None
        if keep_bg:
            _emit(job_id, "processing", "separating", 15, step="separate")
            separated = separate_vocals(audio_info["audio_path"], job_temp)
            # Copy accompaniment to serveable TTS dir
            tts_dir = os.path.join(TEMP_DIR, f"live_{job_id}", "tts")
            os.makedirs(tts_dir, exist_ok=True)
            import shutil as _shutil
            bg_dest = os.path.join(tts_dir, "accompaniment.wav")
            _shutil.copy2(separated["accompaniment"], bg_dest)
            accompaniment_url = f"/tts/{job_id}/accompaniment.wav"
            _emit(job_id, "processing", "separated", 18, step="separate")

        # Step 2: Transcribe
        _emit(job_id, "processing", "transcribing", 18, step="transcribe")
        segments = transcribe(audio_info["audio_path"], client)
        _emit(
            job_id, "processing", "transcribed", 35,
            step="transcribe",
            segment_count=len(segments),
        )

        # Step 3: Translate
        _emit(job_id, "processing", "translating", 38, step="translate")

        def on_translate(batch, total):
            p = 38 + int((batch / total) * 22)
            _emit(job_id, "processing", "translating", p, step="translate",
                  batch=batch, total_batches=total)

        translated = translate_segments(
            segments, client, model=model, on_progress=on_translate,
        )
        _emit(job_id, "processing", "translated", 60, step="translate",
              total_segments=len(translated))

        # Step 4: TTS — save to dedicated dir so files can be served
        _emit(job_id, "processing", "synthesizing", 62, step="tts")
        tts_dir = os.path.join(TEMP_DIR, f"live_{job_id}")
        os.makedirs(tts_dir, exist_ok=True)

        def on_tts(completed, total):
            p = 62 + int((completed / total) * 35)
            _emit(job_id, "processing", "synthesizing", p, step="tts",
                  completed=completed, tts_total=total)

        tts_segments = generate_tts_batch(
            translated, elevenlabs_key, tts_dir,
            voice=voice, max_workers=3, on_progress=on_tts,
        )

        success = sum(1 for s in tts_segments if s.get("tts_path"))
        log.info(f"[Live TTS] {success}/{len(tts_segments)} segments generated")
        if success == 0:
            _emit(job_id, "error", "TTS 語音合成全部失敗，請檢查 ElevenLabs API Key")
            return

        # Build response with audio URLs
        all_segments = []
        for s in tts_segments:
            seg = {
                "start": s["start"], "end": s["end"],
                "en": s["text"], "zh": s["translated"],
            }
            if s.get("tts_path") and os.path.exists(s["tts_path"]):
                seg["audio"] = f"/tts/{job_id}/{os.path.basename(s['tts_path'])}"
            all_segments.append(seg)

        # Clean up source files in job_temp, keep TTS files for serving
        for f in os.listdir(job_temp):
            fp = os.path.join(job_temp, f)
            if os.path.isfile(fp):
                os.remove(fp)

        _emit(job_id, "processing", "synthesized", 97,
              step="tts", tts_success=success, tts_total=len(tts_segments))
        _emit(job_id, "completed", "done", 100,
              segments=all_segments, accompaniment=accompaniment_url)

    except Exception as e:
        log.exception(f"[Pipeline] Job {job_id} failed")
        _emit(job_id, "error", "處理過程發生錯誤，請稍後重試", 0)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

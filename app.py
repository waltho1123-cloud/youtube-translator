#!/usr/bin/env python3
"""Web interface for YouTube English-to-Chinese Video Translator."""
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid

import logging

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from werkzeug.utils import secure_filename

import db

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

log = logging.getLogger("pipeline")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
# Log to stderr so gunicorn and Zeabur runtime logs can capture output
_sh = logging.StreamHandler(sys.stderr)
_sh.setFormatter(_fmt)
log.addHandler(_sh)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())

login_manager = LoginManager()
login_manager.init_app(app)


class User(UserMixin):
    def __init__(self, user_data):
        self.id = user_data['id']
        self.email = user_data['email']
        self.name = user_data.get('name', '')
        self.username = user_data.get('name', user_data['email'])  # backward compat


@login_manager.user_loader
def load_user(user_id):
    user_data = db.get_user_by_id(int(user_id))
    if user_data:
        return User(user_data)
    return None


# 初始化資料庫
db.init_db()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
TEMP_DIR = os.path.join(BASE_DIR, "temp")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory job store
jobs = {}
jobs_lock = threading.Lock()

# 允許使用的模型白名單
ALLOWED_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"}


def _cleanup_old_jobs(max_age=3600):
    """清理超過 max_age 秒的已完成任務，防止 jobs 字典無限成長。"""
    now = time.time()
    with jobs_lock:
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


@app.route("/api/auth/google", methods=["POST"])
def google_login():
    data = request.json
    credential = data.get("credential", "")
    if not credential:
        return jsonify({"error": "Missing credential"}), 400

    try:
        idinfo = id_token.verify_oauth2_token(
            credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        google_sub = idinfo['sub']
        email = idinfo.get('email', '')
        name = idinfo.get('name', '')
        avatar = idinfo.get('picture', '')

        user_data = db.find_or_create_google_user(google_sub, email, name, avatar)
        login_user(User(user_data))
        return jsonify({"ok": True, "username": name or email, "avatar": avatar})
    except Exception as e:
        return jsonify({"error": f"Google 驗證失敗: {str(e)[:100]}"}), 401


@app.route("/api/auth/google-client-id")
def google_client_id():
    return jsonify({"client_id": GOOGLE_CLIENT_ID})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    logout_user()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def auth_me():
    if current_user.is_authenticated:
        user_data = db.get_user_by_id(current_user.id)
        return jsonify({
            "logged_in": True,
            "username": current_user.name or current_user.email,
            "avatar": user_data.get('avatar', '') if user_data else ''
        })
    return jsonify({"logged_in": False})


@app.route("/api/keys", methods=["GET"])
def get_keys():
    """Return whether API keys are configured (not the actual keys)."""
    if not current_user.is_authenticated:
        return jsonify({"openai": False, "minimax": False})
    keys = db.get_user_keys(current_user.id)
    return jsonify({
        "openai": bool(keys.get("openai_key")),
        "minimax": bool(keys.get("minimax_key")),
        "youtube_cookies": bool(keys.get("youtube_cookies")),
    })


@app.route("/api/keys", methods=["POST"])
def set_keys():
    """Save API keys to the database for the current user."""
    if not current_user.is_authenticated:
        return jsonify({"error": "請先登入"}), 401
    data = request.json
    openai_key = data.get("openai_key", "").strip()
    minimax_key = data.get("minimax_key", "").strip()
    minimax_group = data.get("minimax_group", "").strip()
    db.update_user_keys(
        current_user.id,
        openai_key=openai_key if openai_key else None,
        minimax_key=minimax_key if minimax_key else None,
        minimax_group=minimax_group if minimax_group else None,
    )
    return jsonify({"ok": True})


@app.route("/api/youtube-cookies", methods=["POST"])
def set_youtube_cookies():
    """Save YouTube cookies for bypassing bot detection."""
    if not current_user.is_authenticated:
        return jsonify({"error": "請先登入"}), 401
    data = request.json
    cookies = data.get("cookies", "").strip()
    db.update_youtube_cookies(current_user.id, cookies)
    return jsonify({"ok": True})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/translate", methods=["POST"])
def start_translate():
    if not current_user.is_authenticated:
        return jsonify({"error": "請先登入"}), 401
    keys = db.get_user_keys(current_user.id)
    openai_key = keys.get("openai_key", "")
    minimax_key = keys.get("minimax_key", "")
    minimax_group = keys.get("minimax_group", "")
    youtube_cookies = keys.get("youtube_cookies", "")
    if not openai_key or not minimax_key:
        return jsonify({"error": "請先在設定中填寫 API Key"}), 400

    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not _validate_youtube_url(url):
        return jsonify({"error": "Invalid YouTube URL"}), 400

    # 清理過期的已完成任務
    _cleanup_old_jobs()

    voice = data.get("voice", "rachel")
    try:
        volume = max(0.0, min(1.0, float(data.get("volume", 0.15))))
    except (TypeError, ValueError):
        volume = 0.15
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
    with jobs_lock:
        jobs[job_id] = {"events": queue.Queue()}

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, url, voice, volume, model, subtitle, quality, eng_subtitle, keep_bg,
              openai_key, minimax_key, minimax_group, youtube_cookies),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/live-translate", methods=["POST"])
def start_live_translate():
    """Live voice translation: download audio, transcribe, translate, TTS."""
    if not current_user.is_authenticated:
        return jsonify({"error": "請先登入"}), 401
    keys = db.get_user_keys(current_user.id)
    openai_key = keys.get("openai_key", "")
    minimax_key = keys.get("minimax_key", "")
    minimax_group = keys.get("minimax_group", "")
    youtube_cookies = keys.get("youtube_cookies", "")
    if not openai_key or not minimax_key:
        return jsonify({"error": "請先在設定中填寫 API Key"}), 400

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
    with jobs_lock:
        jobs[job_id] = {"events": queue.Queue()}

    thread = threading.Thread(
        target=_run_live_pipeline,
        args=(job_id, url, model, voice, keep_bg,
              openai_key, minimax_key, minimax_group, youtube_cookies),
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
        with jobs_lock:
            job = jobs.get(job_id)
        if not job:
            yield f"data: {json.dumps({'status': 'error', 'message': 'Job not found'})}\n\n"
            return

        heartbeat_count = 0
        while True:
            try:
                event = job["events"].get(timeout=60)
                heartbeat_count = 0
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("status") in ("completed", "error"):
                    break
            except queue.Empty:
                heartbeat_count += 1
                if heartbeat_count > 30:
                    yield f"data: {json.dumps({'status': 'error', 'message': '連線逾時（30 分鐘無進度）'})}\n\n"
                    break
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
    with jobs_lock:
        if job_id not in jobs:
            return
        jobs[job_id]["events"].put(event)
        # 當任務完成或出錯時標記時間戳，供 _cleanup_old_jobs 清理
        if status in ("completed", "error"):
            jobs[job_id]["completed_at"] = time.time()


def _run_pipeline(job_id, url, voice, volume, model, subtitle=False, quality="720", eng_subtitle=False, keep_bg=False,
                   openai_key="", minimax_key="", minimax_group="", youtube_cookies=""):
    """Run the full translation pipeline in a background thread."""
    job_temp = os.path.join(TEMP_DIR, job_id)
    cookies_file = None
    try:
        from openai import OpenAI
        from downloader import download_video, write_cookies_file
        from transcriber import transcribe
        from translator import translate_segments
        from tts_engine import generate_tts_batch
        from composer import compose_video
        from separator import separate_vocals

        if not openai_key or not minimax_key:
            _emit(job_id, "error", "API keys not configured (OpenAI + MiniMax)")
            return

        client = OpenAI(api_key=openai_key)
        cookies_file = write_cookies_file(youtube_cookies, job_temp) if youtube_cookies else None

        # Use per-job temp directory to avoid concurrent conflicts
        os.makedirs(job_temp, exist_ok=True)

        # Step 1: Download
        _emit(job_id, "processing", "downloading", 5, step="download")
        video_info = download_video(url, job_temp, quality=quality, cookies_file=cookies_file)
        _emit(
            job_id, "processing", "downloaded", 18,
            step="download",
            title=video_info["title"],
            duration=video_info["duration"],
        )

        # Step 1.5: Separate vocals if keep_bg enabled
        accompaniment_path = None
        if keep_bg:
            from separator import is_available as demucs_available
            if not demucs_available():
                _emit(job_id, "processing", "skip_separate", 22, step="separate")
                log.warning("[Pipeline] Demucs not installed, skipping background music separation")
            else:
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
            translated, minimax_key, job_temp,
            voice=voice, group_id=minimax_group, max_workers=3, on_progress=on_tts,
        )

        success = sum(1 for s in tts_segments if s.get("tts_path"))
        log.info(f"[TTS] {success}/{len(tts_segments)} segments generated successfully")
        if success == 0:
            _emit(job_id, "error", "TTS 語音合成全部失敗，請檢查 MiniMax API Key 是否正確")
            return
        _emit(job_id, "processing", "synthesized", 90,
              step="tts", tts_success=success, tts_total=len(tts_segments))

        # Step 5: Compose
        _emit(job_id, "processing", "composing", 92, step="compose")

        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_"
            for c in video_info["title"]
        )[:60]
        out_filename = secure_filename(f"{safe_title}_{job_id[:8]}_cn.mp4")
        out_path = os.path.join(OUTPUT_DIR, out_filename)

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
        err_detail = str(e)[:200] if str(e) else type(e).__name__
        _emit(job_id, "error", f"處理過程發生錯誤: {err_detail}", 0)
    finally:
        shutil.rmtree(job_temp, ignore_errors=True)


def _run_live_pipeline(job_id, url, model, voice, keep_bg=False,
                       openai_key="", minimax_key="", minimax_group="", youtube_cookies=""):
    """Run live voice translation pipeline: audio -> transcribe -> translate -> TTS."""
    job_temp = os.path.join(TEMP_DIR, job_id)
    cookies_file = None
    try:
        from openai import OpenAI
        from downloader import download_audio_only, write_cookies_file
        from transcriber import transcribe
        from translator import translate_segments
        from tts_engine import generate_tts_batch
        from separator import separate_vocals

        if not openai_key or not minimax_key:
            _emit(job_id, "error", "API keys not configured (OpenAI + MiniMax)")
            return

        client = OpenAI(api_key=openai_key)
        cookies_file = write_cookies_file(youtube_cookies, job_temp) if youtube_cookies else None
        log.info(f"[Live Pipeline] Job {job_id} started: url={url}, model={model}, voice={voice}, keep_bg={keep_bg}, cookies={'yes' if cookies_file else 'no'}")

        # Step 1: Download audio only (fast)
        # Use per-job temp directory
        os.makedirs(job_temp, exist_ok=True)

        _emit(job_id, "processing", "downloading_audio", 5, step="download")
        try:
            audio_info = download_audio_only(url, job_temp, cookies_file=cookies_file)
        except Exception as e:
            log.error(f"[Live Pipeline] Download failed: {e}")
            _emit(job_id, "error", f"影片下載失敗: {str(e)[:200]}", 0)
            return
        log.info(f"[Live Pipeline] Downloaded: {audio_info['title']} ({audio_info['duration']}s)")
        _emit(
            job_id, "processing", "downloaded", 15,
            step="download",
            title=audio_info["title"],
            duration=audio_info["duration"],
        )

        # Step 1.5: Separate vocals if keep_bg enabled
        accompaniment_url = None
        if keep_bg:
            from separator import is_available as demucs_available
            if not demucs_available():
                _emit(job_id, "processing", "skip_separate", 18, step="separate")
                log.warning("[Live Pipeline] Demucs not installed, skipping background music separation")
            else:
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
        try:
            segments = transcribe(audio_info["audio_path"], client)
        except Exception as e:
            log.error(f"[Live Pipeline] Transcription failed: {e}")
            _emit(job_id, "error", f"語音辨識失敗: {str(e)[:200]}", 0)
            return
        log.info(f"[Live Pipeline] Transcribed: {len(segments)} segments")
        _emit(
            job_id, "processing", "transcribed", 35,
            step="transcribe",
            segment_count=len(segments),
        )

        if not segments:
            _emit(job_id, "error", "語音辨識未找到任何語音內容，請確認影片含有英語對話", 0)
            return

        # Step 3: Translate
        _emit(job_id, "processing", "translating", 38, step="translate")

        def on_translate(batch, total):
            p = 38 + int((batch / total) * 22)
            _emit(job_id, "processing", "translating", p, step="translate",
                  batch=batch, total_batches=total)

        try:
            translated = translate_segments(
                segments, client, model=model, on_progress=on_translate,
            )
        except Exception as e:
            log.error(f"[Live Pipeline] Translation failed: {e}")
            _emit(job_id, "error", f"翻譯失敗: {str(e)[:200]}", 0)
            return
        log.info(f"[Live Pipeline] Translated: {len(translated)} segments")
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
            translated, minimax_key, tts_dir,
            voice=voice, group_id=minimax_group, max_workers=3, on_progress=on_tts,
        )

        success = sum(1 for s in tts_segments if s.get("tts_path"))
        log.info(f"[Live TTS] {success}/{len(tts_segments)} segments generated")
        if success == 0:
            _emit(job_id, "error", "TTS 語音合成全部失敗，請檢查 MiniMax API Key 是否正確", 0)
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
        if os.path.isdir(job_temp):
            for f in os.listdir(job_temp):
                fp = os.path.join(job_temp, f)
                if os.path.isfile(fp):
                    os.remove(fp)

        _emit(job_id, "processing", "synthesized", 97,
              step="tts", tts_success=success, tts_total=len(tts_segments))
        log.info(f"[Live Pipeline] Job {job_id} completed successfully")
        _emit(job_id, "completed", "done", 100,
              segments=all_segments, accompaniment=accompaniment_url)

    except Exception as e:
        log.exception(f"[Live Pipeline] Job {job_id} failed with unexpected error")
        err_detail = str(e)[:200] if str(e) else type(e).__name__
        _emit(job_id, "error", f"處理過程發生未預期錯誤: {err_detail}", 0)
    finally:
        shutil.rmtree(job_temp, ignore_errors=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

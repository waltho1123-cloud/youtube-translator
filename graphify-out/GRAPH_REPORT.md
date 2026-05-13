# Graph Report - .  (2026-04-12)

## Corpus Check
- Corpus is ~8,032 words - fits in a single context window. You may not need a graph.

## Summary
- 140 nodes · 199 edges · 12 communities detected
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.83)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Flask Web App & Auth|Flask Web App & Auth]]
- [[_COMMUNITY_VideoAudio Download|Video/Audio Download]]
- [[_COMMUNITY_Pipeline Orchestration|Pipeline Orchestration]]
- [[_COMMUNITY_TTS Engine (MiniMax)|TTS Engine (MiniMax)]]
- [[_COMMUNITY_Apify Cloud Download|Apify Cloud Download]]
- [[_COMMUNITY_Video Composition & SRT|Video Composition & SRT]]
- [[_COMMUNITY_Audio Transcription|Audio Transcription]]
- [[_COMMUNITY_SQLite Database|SQLite Database]]
- [[_COMMUNITY_Local Vocal Separation|Local Vocal Separation]]
- [[_COMMUNITY_CLI Entry Point|CLI Entry Point]]
- [[_COMMUNITY_Apify Module|Apify Module]]
- [[_COMMUNITY_Dependencies|Dependencies]]

## God Nodes (most connected - your core abstractions)
1. `_run_pipeline()` - 15 edges
2. `_run_live_pipeline()` - 14 edges
3. `transcribe()` - 10 edges
4. `compose_video()` - 9 edges
5. `generate_tts_batch()` - 9 edges
6. `download_video()` - 8 edges
7. `get_db()` - 7 edges
8. `download_audio_only()` - 7 edges
9. `translate_segments()` - 7 edges
10. `get_transcript()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `download_video()` --semantically_similar_to--> `Apify Video Download`  [INFERRED] [semantically similar]
  downloader.py → apify_download.py
- `separate_vocals()` --semantically_similar_to--> `separate_vocals()`  [INFERRED] [semantically similar]
  separator.py → cloud_separator.py
- `get_transcript()` --semantically_similar_to--> `transcribe()`  [INFERRED] [semantically similar]
  apify_download.py → transcriber.py
- `download_audio_only()` --semantically_similar_to--> `Apify Audio Download (Async Polling)`  [INFERRED] [semantically similar]
  downloader.py → apify_download.py
- `CLI Entry Point (main.py)` --semantically_similar_to--> `_run_pipeline()`  [INFERRED] [semantically similar]
  main.py → app.py

## Hyperedges (group relationships)
- **5-Stage Video Translation Pipeline (Download -> Transcribe -> Translate -> TTS -> Compose)** — downloader_download_video, apify_download_video, transcriber_transcribe, translator_translate_segments, tts_engine_generate_tts_batch, composer_compose_video [EXTRACTED 1.00]
- **Multi-Source Download Fallback Chain (Apify -> yt-dlp -> pytubefix)** — apify_download_video, apify_download_audio, downloader_download_video, downloader_download_audio_only, downloader_ytdlp_fallback [EXTRACTED 0.95]
- **Vocal Separation Strategies (Local Demucs vs Cloud Replicate)** — separator_separate_vocals, cloud_separator_separate_vocals [EXTRACTED 0.95]

## Communities

### Community 0 - "Flask Web App & Auth"
Cohesion: 0.08
Nodes (22): _cleanup_old_jobs(), get_keys(), google_login(), load_user(), progress(), Return whether API keys are configured (not the actual keys)., Save API keys to the database for the current user., Save YouTube cookies for bypassing bot detection. (+14 more)

### Community 1 - "Video/Audio Download"
Cohesion: 0.13
Nodes (22): Apify Audio Download (Async Polling), download_audio_only(), _download_audio_pytubefix(), _download_audio_ytdlp(), download_video(), _download_video_pytubefix(), _download_video_ytdlp(), _extract_ytdlp_error() (+14 more)

### Community 2 - "Pipeline Orchestration"
Cohesion: 0.13
Nodes (17): Apify Video Download, _emit(), Flask Web Application, Run the full translation pipeline in a background thread., Run live voice translation pipeline: audio -> transcribe -> translate -> TTS., _run_live_pipeline(), _run_pipeline(), SSE Progress Streaming Endpoint (+9 more)

### Community 3 - "TTS Engine (MiniMax)"
Cohesion: 0.18
Nodes (13): _adjust_speed(), _generate_one(), generate_tts_batch(), _get_wav_duration(), Text-to-Speech using MiniMax API., Generate TTS audio for all segments concurrently.      Args:         segments: T, Resolve voice name to ID. Accepts preset name or raw voice_id., Adjust audio playback speed using ffmpeg atempo filter. (+5 more)

### Community 4 - "Apify Cloud Download"
Cohesion: 0.22
Nodes (12): download_audio(), download_video(), _extract_video_id(), _find_download_url(), get_transcript(), YouTube download via Apify actors — reliable cloud-based alternative to yt-dlp., Extract download URL from Apify result item (handles various actor formats)., Download YouTube audio via Apify actors (async with polling).      Args: (+4 more)

### Community 5 - "Video Composition & SRT"
Cohesion: 0.24
Nodes (10): compose_video(), _format_srt_time(), _generate_srt(), _get_duration_ms(), Video composition - merge Chinese TTS audio with original video., Get media file duration in milliseconds using ffmpeg., Generate SRT subtitle file from segments.      Args:         segments: List of s, Format seconds to SRT time format: HH:MM:SS,mmm (+2 more)

### Community 6 - "Audio Transcription"
Cohesion: 0.31
Nodes (8): Large Audio File Chunked Transcription, Speech-to-text using OpenAI Whisper API., Transcribe audio file using Whisper API.      Automatically splits large files i, Transcribe a single audio file., Split large audio file and transcribe each chunk., transcribe(), _transcribe_file(), _transcribe_large_file()

### Community 7 - "SQLite Database"
Cohesion: 0.46
Nodes (7): find_or_create_google_user(), get_db(), get_user_by_id(), get_user_keys(), init_db(), update_user_keys(), update_youtube_cookies()

### Community 8 - "Local Vocal Separation"
Cohesion: 0.33
Nodes (5): is_available(), Audio source separation using Demucs - split vocals from background., Check if demucs is installed., Separate audio into vocals and accompaniment using Demucs.      Args:         au, separate_vocals()

### Community 9 - "CLI Entry Point"
Cohesion: 0.83
Nodes (3): format_time(), main(), parse_args()

### Community 10 - "Apify Module"
Cohesion: 1.0
Nodes (1): Apify Cloud Download Module

### Community 11 - "Dependencies"
Cohesion: 1.0
Nodes (1): Python Dependencies

## Knowledge Gaps
- **55 isolated node(s):** `Audio source separation using Demucs - split vocals from background.`, `Check if demucs is installed.`, `Separate audio into vocals and accompaniment using Demucs.      Args:         au`, `YouTube download via Apify actors — reliable cloud-based alternative to yt-dlp.`, `Extract YouTube video ID from URL.` (+50 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Apify Module`** (1 nodes): `Apify Cloud Download Module`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Dependencies`** (1 nodes): `Python Dependencies`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_run_live_pipeline()` connect `Pipeline Orchestration` to `Flask Web App & Auth`, `Video/Audio Download`, `TTS Engine (MiniMax)`, `Apify Cloud Download`, `Audio Transcription`, `Local Vocal Separation`?**
  _High betweenness centrality (0.318) - this node is a cross-community bridge._
- **Why does `_run_pipeline()` connect `Pipeline Orchestration` to `Flask Web App & Auth`, `Video/Audio Download`, `TTS Engine (MiniMax)`, `Video Composition & SRT`, `Audio Transcription`?**
  _High betweenness centrality (0.281) - this node is a cross-community bridge._
- **Why does `generate_tts_batch()` connect `TTS Engine (MiniMax)` to `Video/Audio Download`, `Pipeline Orchestration`, `Apify Cloud Download`?**
  _High betweenness centrality (0.153) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `_run_pipeline()` (e.g. with `_run_live_pipeline()` and `CLI Entry Point (main.py)`) actually correct?**
  _`_run_pipeline()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Audio source separation using Demucs - split vocals from background.`, `Check if demucs is installed.`, `Separate audio into vocals and accompaniment using Demucs.      Args:         au` to the rest of the system?**
  _55 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Flask Web App & Auth` be split into smaller, more focused modules?**
  _Cohesion score 0.08 - nodes in this community are weakly interconnected._
- **Should `Video/Audio Download` be split into smaller, more focused modules?**
  _Cohesion score 0.13 - nodes in this community are weakly interconnected._
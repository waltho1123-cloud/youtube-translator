#!/usr/bin/env python3
"""YouTube English-to-Chinese Video Translator.

Downloads a YouTube video, transcribes English speech, translates to Chinese,
generates Chinese TTS audio, and composes a dubbed video.

Usage:
    python main.py "https://www.youtube.com/watch?v=VIDEO_ID"
    python main.py "URL" --output my_video.mp4 --volume 0.2
    python main.py "URL" --voice josh
"""
import argparse
import os
import shutil
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from downloader import download_video
from transcriber import transcribe
from translator import translate_segments
from tts_engine import generate_tts_batch
from composer import compose_video


def parse_args():
    parser = argparse.ArgumentParser(
        description="YouTube English → Chinese Video Translator"
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (default: output/<title>_cn.mp4)",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=0.15,
        help="Original audio volume (0.0-1.0, default: 0.15)",
    )
    parser.add_argument(
        "--voice",
        default="rachel",
        help="ElevenLabs voice: rachel/josh/bella/antoni/adam/sam or voice_id (default: rachel)",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="GPT model for translation (default: gpt-4o)",
    )
    parser.add_argument(
        "--tts-workers",
        type=int,
        default=3,
        help="Concurrent TTS workers (default: 3)",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary files after completion",
    )
    return parser.parse_args()


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def main():
    args = parse_args()
    load_dotenv()

    console = Console()

    # Validate environment
    openai_key = os.getenv("OPENAI_API_KEY")
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")

    if not openai_key:
        console.print("[red]Error: OPENAI_API_KEY not set in .env file[/red]")
        sys.exit(1)
    if not elevenlabs_key:
        console.print("[red]Error: ELEVENLABS_API_KEY not set in .env file[/red]")
        sys.exit(1)

    # Check ffmpeg
    if not shutil.which("ffmpeg"):
        console.print("[red]Error: ffmpeg not found. Install with: brew install ffmpeg[/red]")
        sys.exit(1)

    client = OpenAI(api_key=openai_key)
    temp_dir = os.path.join(os.path.dirname(__file__), "temp")
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)

    console.print(Panel("YouTube 英語影片 → 中文配音翻譯器", style="bold cyan"))
    total_start = time.time()

    # ── Step 1: Download ──
    console.print("\n[bold]Step 1/5[/bold] 下載影片...")
    t0 = time.time()

    try:
        video_info = download_video(args.url, temp_dir)
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        sys.exit(1)

    console.print(
        f"  [green]✓[/green] {video_info['title']} "
        f"({format_time(video_info['duration'])}) "
        f"[dim]({time.time() - t0:.1f}s)[/dim]"
    )

    # ── Step 2: Transcribe ──
    console.print("\n[bold]Step 2/5[/bold] 語音辨識 (Whisper)...")
    t0 = time.time()

    try:
        segments = transcribe(video_info["audio_path"], client)
    except Exception as e:
        console.print(f"[red]Transcription failed: {e}[/red]")
        sys.exit(1)

    console.print(
        f"  [green]✓[/green] 辨識出 {len(segments)} 個語句 "
        f"[dim]({time.time() - t0:.1f}s)[/dim]"
    )

    # ── Step 3: Translate ──
    console.print("\n[bold]Step 3/5[/bold] 翻譯中 (GPT)...")
    t0 = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("翻譯中...", total=None)

        def on_translate_progress(batch, total):
            progress.update(task, total=total, completed=batch, description=f"翻譯批次 {batch}/{total}")

        translated = translate_segments(
            segments, client, model=args.model, on_progress=on_translate_progress
        )

    # Show sample translations
    table = Table(title="翻譯預覽", show_lines=True, expand=False)
    table.add_column("時間", style="cyan", width=12)
    table.add_column("English", width=40)
    table.add_column("中文", style="green", width=40)
    for seg in translated[:5]:
        table.add_row(
            f"{format_time(seg['start'])}-{format_time(seg['end'])}",
            seg["text"][:50],
            seg["translated"][:50],
        )
    if len(translated) > 5:
        table.add_row("...", f"(共 {len(translated)} 句)", "...")
    console.print(table)
    console.print(f"  [green]✓[/green] 翻譯完成 [dim]({time.time() - t0:.1f}s)[/dim]")

    # ── Step 4: TTS ──
    console.print(f"\n[bold]Step 4/5[/bold] 中文語音合成 (ElevenLabs, voice={args.voice})...")
    t0 = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("語音合成中...", total=len(translated))

        def on_tts_progress(completed, total):
            progress.update(task, completed=completed, description=f"語音合成 {completed}/{total}")

        tts_segments = generate_tts_batch(
            translated,
            elevenlabs_key,
            temp_dir,
            voice=args.voice,
            max_workers=args.tts_workers,
            on_progress=on_tts_progress,
        )

    success_count = sum(1 for s in tts_segments if s.get("tts_path"))
    console.print(
        f"  [green]✓[/green] 合成 {success_count}/{len(tts_segments)} 段語音 "
        f"[dim]({time.time() - t0:.1f}s)[/dim]"
    )

    # ── Step 5: Compose ──
    console.print(
        f"\n[bold]Step 5/5[/bold] 合成影片 (原音量: {args.volume:.0%})..."
    )
    t0 = time.time()

    # Determine output path
    if args.output:
        out_path = args.output
    else:
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_"
            for c in video_info["title"]
        )[:60]
        out_path = os.path.join(output_dir, f"{safe_title}_cn.mp4")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    try:
        compose_video(
            video_info["video_path"], tts_segments, out_path, args.volume
        )
    except Exception as e:
        console.print(f"[red]Composition failed: {e}[/red]")
        sys.exit(1)

    console.print(f"  [green]✓[/green] 影片合成完成 [dim]({time.time() - t0:.1f}s)[/dim]")

    # Clean up temp files
    if not args.keep_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Summary
    total_time = time.time() - total_start
    file_size_mb = os.path.getsize(out_path) / (1024 * 1024)

    console.print(
        Panel(
            f"[bold green]完成！[/bold green]\n\n"
            f"輸出檔案: {out_path}\n"
            f"檔案大小: {file_size_mb:.1f} MB\n"
            f"總耗時:   {format_time(total_time)}\n"
            f"語句數:   {len(translated)}",
            title="處理完成",
            style="green",
        )
    )


if __name__ == "__main__":
    main()

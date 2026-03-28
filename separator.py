"""Audio source separation using Demucs - split vocals from background."""
import os
import subprocess
import logging

log = logging.getLogger("pipeline")


def is_available() -> bool:
    """Check if demucs is installed."""
    try:
        import demucs  # noqa: F401
        return True
    except ImportError:
        return False


def separate_vocals(audio_path: str, output_dir: str) -> dict:
    """Separate audio into vocals and accompaniment using Demucs.

    Args:
        audio_path: Path to input audio file
        output_dir: Directory to store separated tracks

    Returns:
        {"vocals": path, "accompaniment": path}
    """
    log.info(f"[Separator] Separating vocals from: {audio_path}")

    # Use absolute paths to avoid issues
    audio_path = os.path.abspath(audio_path)
    output_dir = os.path.abspath(output_dir)

    # Run demucs with two-stems mode (vocals vs no_vocals)
    import sys
    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems=vocals",
        "--out", output_dir,
        audio_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # 10 分鐘超時
    if result.returncode != 0:
        # Combine stdout+stderr for full error context
        full_output = (result.stdout + result.stderr).strip()
        log.error(f"[Separator] Demucs failed: {full_output}")
        raise RuntimeError(f"Demucs separation failed: {full_output[-500:]}")

    # Demucs outputs to: output_dir/htdemucs/<track_name>/vocals.wav and no_vocals.wav
    track_name = os.path.splitext(os.path.basename(audio_path))[0]
    demucs_dir = os.path.join(output_dir, "htdemucs", track_name)

    vocals_path = os.path.join(demucs_dir, "vocals.wav")
    accompaniment_path = os.path.join(demucs_dir, "no_vocals.wav")

    if not os.path.exists(accompaniment_path):
        raise RuntimeError(f"Demucs output not found: {accompaniment_path}")

    log.info(f"[Separator] Separation complete. Accompaniment: {accompaniment_path}")

    return {
        "vocals": vocals_path,
        "accompaniment": accompaniment_path,
    }

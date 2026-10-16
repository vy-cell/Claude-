"""ffmpeg/ffprobe helpers and optional local transcription."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import Transcript, Word


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-3000:]}")


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def duration(path: str | Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_audio(path: str | Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path)["streams"])


def video_size(path: str | Path) -> tuple[int, int]:
    v = next(s for s in probe(path)["streams"] if s.get("codec_type") == "video")
    w, h = int(v["width"]), int(v["height"])
    rot = int(v.get("tags", {}).get("rotate", 0) or 0)
    for sd in v.get("side_data_list", []):
        rot = int(sd.get("rotation", rot) or rot)
    return (h, w) if abs(rot) in (90, 270) else (w, h)


def still(video: Path, t: float, out: Path, width: int | None = None) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", str(video), "-frames:v", "1", *vf, "-q:v", "3", str(out)])
    return out


def sample_frames(video: Path, out_dir: Path, max_frames: int = 120, width: int = 512) -> list[tuple[float, Path]]:
    """Evenly spaced thumbnails so the planner can *see* the footage, not just hear it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = duration(video)
    step = max(1.5, dur / max_frames)
    frames = []
    t = step / 2
    while t < dur:
        p = out_dir / f"t{t:08.2f}.jpg"
        if not p.exists():
            still(video, t, p, width)
        frames.append((round(t, 2), p))
        t += step
    return frames


def transcribe(path: Path, model_size: str = "small") -> Transcript | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    if not has_audio(path):
        return Transcript(words=[], duration=duration(path))
    model = WhisperModel(model_size, compute_type="int8")
    segments, info = model.transcribe(str(path), word_timestamps=True)
    words = [
        Word(text=w.word.strip(), start=float(w.start), end=float(w.end))
        for seg in segments
        for w in (seg.words or [])
        if w.word.strip()
    ]
    return Transcript(words=words, duration=duration(path), language=info.language)


def load_or_transcribe(video: Path, cache: Path, model_size: str) -> Transcript | None:
    if cache.exists():
        return Transcript.model_validate_json(cache.read_text())
    t = transcribe(video, model_size)
    if t is not None:
        cache.write_text(t.model_dump_json(indent=2))
    return t


def transcript_lines(t: Transcript, pause: float = 0.6) -> str:
    """Compact timestamped lines: '[12.3-15.8] text'."""
    if not t or not t.words:
        return "(no speech detected)"
    lines, cur = [], [t.words[0]]
    for prev, w in zip(t.words, t.words[1:]):
        if w.start - prev.end >= pause or prev.text.endswith((".", "?", "!")):
            lines.append(cur)
            cur = []
        cur.append(w)
    lines.append(cur)
    return "\n".join(f"[{ws[0].start:.1f}-{ws[-1].end:.1f}] {' '.join(w.text for w in ws)}" for ws in lines)

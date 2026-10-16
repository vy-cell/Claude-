"""Text-to-speech with a disk cache, so re-renders in the improvement loop never re-bill unchanged lines."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .brand import Brand
from .media import duration, run


class VoiceError(RuntimeError):
    pass


def synthesize(brand: Brand, text: str, lang: str, cache_dir: Path) -> tuple[Path | None, float]:
    """Returns (wav path, seconds). Empty text -> (None, 0)."""
    text = (text or "").strip()
    if not text:
        return None, 0.0
    vo = brand.voiceover
    voice = brand.language(lang).voice_id or vo.voice_id
    key = hashlib.sha256(f"{vo.provider}|{voice}|{vo.model_id}|{vo.stability}|{vo.similarity_boost}|{vo.speed}|{lang}|{text}".encode()).hexdigest()[:20]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{lang}_{key}.wav"
    if not out.exists():
        if vo.provider == "elevenlabs":
            _elevenlabs(brand, voice, text, out)
        elif vo.provider == "command":
            _command(brand, text, out)
        else:
            _preview(text, lang, out)
    return out, duration(out)


def _to_wav(src: Path, out: Path) -> None:
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", "48000", "-ac", "1", str(out)])


def _elevenlabs(brand: Brand, voice: str, text: str, out: Path) -> None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key or not voice:
        raise VoiceError("ElevenLabs needs ELEVENLABS_API_KEY and voiceover.voice_id in brand.yaml "
                         "(or run with --voice preview to check the edit without a voice).")
    vo = brand.voiceover
    body = {"text": text, "model_id": vo.model_id,
            "voice_settings": {"stability": vo.stability, "similarity_boost": vo.similarity_boost, "speed": vo.speed}}
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps(body).encode(), method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        raise VoiceError(f"ElevenLabs error {e.code}: {e.read()[:300]!r}") from e
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
    try:
        _to_wav(Path(f.name), out)
    finally:
        os.unlink(f.name)


def _command(brand: Brand, text: str, out: Path) -> None:
    tmpl = brand.voiceover.command
    if not tmpl:
        raise VoiceError("voiceover.provider=command needs voiceover.command")
    with tempfile.TemporaryDirectory() as d:
        tf, raw = Path(d) / "text.txt", Path(d) / "raw.wav"
        tf.write_text(text)
        cmd = tmpl.format(text_file=shlex.quote(str(tf)), out_file=shlex.quote(str(raw)))
        subprocess.run(cmd, shell=True, check=True)
        _to_wav(raw, out)


def _preview(text: str, lang: str, out: Path) -> None:
    """Silent stand-in timed like natural narration (~150 wpm; vi/es run a little longer)."""
    words = len(text.split())
    rate = {"vi": 2.6, "es": 2.4}.get(lang, 2.5)
    secs = max(1.2, words / rate)
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{secs:.2f}", str(out)])

"""Claude API access: one helper for every structured call, plus image packing."""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import TypeVar

import anthropic
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

MODEL = os.environ.get("TUTORIAL_EDITOR_MODEL", "claude-opus-5-5")
T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class ClaudeRefused(RuntimeError):
    pass


def ask(system: str, content: list[dict], schema: type[T], effort: str = "high", max_tokens: int = 64000) -> T:
    """One structured call. Streams (long outputs), stable system prompt is cached,
    server-side fallbacks handle a refusal by retrying on another model."""
    with client().beta.messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort},
        output_format=schema,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        details = getattr(msg, "stop_details", None)
        raise ClaudeRefused(f"Claude declined the request: {details}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens); the video may be too long for one pass.")
    if msg.parsed_output is None:
        raise RuntimeError("Claude returned no structured output")
    return msg.parsed_output


# ---------------------------------------------------------------- content blocks


def text(s: str) -> dict:
    return {"type": "text", "text": s}


def image(img: Image.Image | Path, max_side: int = 1568, quality: int = 82) -> dict:
    if isinstance(img, Path):
        img = Image.open(img)
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def contact_sheets(frames: list[tuple[str, Path | Image.Image]], cols: int = 3, rows: int = 3, tile_w: int = 640) -> list[Image.Image]:
    """Packs many frames into a few labelled sheets so a long video fits in one request."""
    sheets = []
    per = cols * rows
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    for i in range(0, len(frames), per):
        chunk = frames[i : i + per]
        th = tile_w * 9 // 16
        sheet = Image.new("RGB", (cols * tile_w, rows * th), (0, 0, 0))
        for j, (label, src) in enumerate(chunk):
            im = (Image.open(src) if isinstance(src, Path) else src).convert("RGB")
            im.thumbnail((tile_w, th))
            x, y = (j % cols) * tile_w, (j // cols) * th
            sheet.paste(im, (x + (tile_w - im.width) // 2, y + (th - im.height) // 2))
            d = ImageDraw.Draw(sheet)
            tw = d.textlength(label, font=font)
            d.rectangle((x, y, x + tw + 16, y + 36), fill=(0, 0, 0))
            d.text((x + 8, y + 4), label, font=font, fill=(255, 230, 0))
        rows_used = (len(chunk) + cols - 1) // cols
        sheets.append(sheet.crop((0, 0, cols * tile_w, rows_used * th)))
    return sheets

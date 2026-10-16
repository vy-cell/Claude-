"""One-page, on-brand step-by-step instruction card (printable job aid) built from the video plan.

Each step gets a real photo from the footage with the instruction beside it. Claude writes
the card, a director pass reviews the rendered page and revises it until it is ready.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel

from .ai import ask, image, text
from .brand import Brand, hex_rgb
from .graphics import FontBook, StillProvider, fit, line_h, load_asset, nfc, rounded, text_w, with_shadow
from .models import VideoPlan
from .planner import _footage_blocks, _system

PAGE = (3300, 2550)  # US Letter landscape at 300 dpi


class Fact(BaseModel):
    label: str  # "BATCH", "FRIDGE"
    value: str  # "40 lb chicken", "≤ 41°F (5°C)"


class Crop(BaseModel):
    x: float  # region of the source frame to show, 0-1 (tight on the action)
    y: float
    w: float
    h: float


class Mark(BaseModel):
    """Symbol drawn on the step photo, pointing at the exact thing to measure or check."""

    kind: Literal["arrow", "ring", "box"]
    x: float  # target centre in the SOURCE frame, 0-1
    y: float
    w: float  # ring/box size in the source frame, 0-1 (ignored for arrows)
    h: float
    label: str  # 1-3 words ("4 CUPS", "PRESS TARE", "FILL LINE"); "" for none


Icon = Literal["none", "scale", "cup", "clock", "thermometer", "snowflake", "tag", "drop"]


class CardStep(BaseModel):
    title: str  # "Make the marinade"
    photo_time: float  # source second of the photo: mid-action, sharp, no on-screen graphics
    photo_crop: Crop  # the part of the frame where the action happens
    marks: list[Mark]  # 0-2 symbols on the photo
    instructions: list[str]  # 2-4 short imperative lines
    key_value: str  # the one number to remember ("2.4 lb · 1.08 kg"), "" if none
    icon: Icon  # line icon shown with the key value
    warning: str  # the one mistake to avoid ("NEVER tap water"), "" if none


class CardPlan(BaseModel):
    title: str
    subtitle: str
    key_facts: list[Fact]  # 2-4 chips in the header
    steps: list[CardStep]  # all steps on one page (max 12)
    rules: list[Fact]  # 2-4 food-safety / storage rules in the footer
    footer_note: str


class CardReview(BaseModel):
    score: int
    ready_to_ship: bool
    issues: list[str]
    card: CardPlan


CARD_GUIDE = """\
Create a one-page printed instruction card ("job aid") that a new team member can follow at
the station instead of watching the video. US Letter landscape, posted on the wall.
- All steps on ONE page, in order. Combine sub-steps so there are 4-12 steps.
- Each step: short title, real photo from the RAW footage caught in the middle of the action
  (hands doing the step, the readout showing the target, the line being reached) - sharp,
  well-lit, uncluttered, never a frame with titles, captions or other graphics on it, never a
  transition. photo_crop frames the action tightly, source-frame
  coordinates 0-1. The photo slot is about square, so frame the action with that in mind.
- marks: 0-2 symbols ON the photo pointing at exactly what to measure or check: ring around
  a scale readout / dial / temperature, arrow to a fill line or button, box around a label.
  Coordinates are in the source frame (0-1) and must land precisely on the target. Short
  label (1-3 words, e.g. "2.40 LB", "FILL LINE", "PRESS TARE").
- 2-4 imperative instruction lines (max ~8 words each), the key number to remember with a
  matching icon (scale = weight, cup = volume, clock = time, thermometer = temperature,
  snowflake = freezer, tag = label, drop = water/liquid, none), and the one mistake to avoid.
- Header key_facts: the batch-level numbers (batch size, total powder, water...).
- Footer rules: the food-safety / storage rules that apply to the whole task.
- Brand: clean, minimal, generous white space, short intentional text. Facts must match the video plan exactly.
"""


def make_card(brand: Brand, plan: VideoPlan, frames, max_seconds: int) -> CardPlan:
    content = [
        text(CARD_GUIDE),
        text("Final video plan (the source of truth for steps and facts):\n```json\n" + plan.model_dump_json(indent=1) + "\n```"),
        *_footage_blocks(frames, tile_w=480),
    ]
    return ask(_system(brand, max_seconds), content, CardPlan, effort="high")


def review_card(brand: Brand, plan: VideoPlan, card: CardPlan, rendered: Path, warnings: list[str], frames,
                reference: list[Path], max_seconds: int, text_only: bool = False) -> CardReview:
    content = [
        text(CARD_GUIDE),
        text("Video plan (facts):\n```json\n" + plan.model_dump_json(indent=1) + "\n```"),
        text("Current card JSON:\n```json\n" + card.model_dump_json(indent=1) + "\n```"),
        text("Rendered card:"), image(rendered, 2400, 88),
        text("Layout warnings from the renderer:\n" + ("\n".join(warnings) or "(none)")),
    ]
    if reference:
        content.append(text("Brand reference (approved sample video frames):"))
        content += [image(p, 900) for p in reference[:6]]
    if not text_only:
        content += _footage_blocks(frames, tile_w=480)
    content.append(text(
        "Review it as the brand's creative director and as a new hire using it at the station. Is every step "
        "clear without the video? Is every photo a clean mid-action frame (no titles/captions/graphics from "
        "a video, no transition), cropped tight on the action? Do the marks land exactly on the measurement "
        "or control they point at? Text legible from 1 m, short, no shrinking or "
        "truncation? Facts match the plan? On brand? Return a revised card fixing everything."
        + (" Translated version: change wording only, keep photo times and list lengths." if text_only else "")))
    return ask(_system(brand, max_seconds), content, CardReview, effort="high")


def translate_card(brand: Brand, card: CardPlan, lang_name: str, max_seconds: int) -> CardPlan:
    content = [text(f"Translate this instruction card into simple, natural {lang_name} for kitchen staff. Keep "
                    f"these terms as written: {', '.join(brand.glossary) or '(none)'}. Keep numbers and both units. "
                    "Keep every list the same length; keep photo_time, photo_crop, icons and mark positions unchanged (translate mark labels). Keep lines short."),
               text("```json\n" + card.model_dump_json(indent=1) + "\n```")]
    out = ask(_system(brand, max_seconds), content, CardPlan, effort="medium")
    if len(out.steps) == len(card.steps):
        for a, b in zip(out.steps, card.steps):
            a.photo_time, a.photo_crop, a.icon = b.photo_time, b.photo_crop, b.icon
            if len(a.marks) == len(b.marks):
                for ma, mb in zip(a.marks, b.marks):
                    ma.kind, ma.x, ma.y, ma.w, ma.h = mb.kind, mb.x, mb.y, mb.w, mb.h
            else:
                a.marks = b.marks
    return out


# ---------------------------------------------------------------- rendering


def render_card(brand: Brand, card: CardPlan, stills: StillProvider, out_png: Path) -> tuple[Path, Path, list[str]]:
    warnings: list[str] = []
    c = brand.colors
    fonts = FontBook(brand)
    PW, PH = PAGE
    page = Image.new("RGBA", PAGE, hex_rgb(c.card_bg, 255))
    d = ImageDraw.Draw(page)
    M = 120  # outer margin

    # header
    logo = load_asset(brand, brand.assets.logo_horizontal, 96)
    if logo:
        page.alpha_composite(logo, (M, 80))
    f, lines, _ = fit(fonts, "heading", card.title, 1900, 120, 80, 1, warnings=warnings, what="card title")
    d.text((M, 205), lines[0], font=f, fill=hex_rgb(c.text))
    sub = nfc(card.subtitle).upper()
    fs = fonts.get("body", 36, 500, sub)
    d.rectangle((M, 362, M + 7, 402), fill=hex_rgb(c.stripe_a))
    _spaced(d, (M + 26, 360), sub, fs, hex_rgb(c.muted_text), 0.22 * 36)
    if brand.template.badge_text:
        fb = fonts.get("body", 30, 600)
        bw = int(text_w(fb, brand.template.badge_text, 6)) + 60
        d.rounded_rectangle((PW - M - bw, 80, PW - M, 140), 6, fill=hex_rgb(c.badge_bg))
        _spaced(d, (PW - M - bw + 30, 92), brand.template.badge_text, fb, hex_rgb(c.surface), 6)
    # key facts chips (right side of header)
    x = PW - M
    for fact in reversed(card.key_facts[:4]):
        fl = fonts.get("body", 24, 600, fact.label)
        fv, lv, _ = fit(fonts, "body", fact.value, 520, 44, 30, 1, 700, warnings=warnings, what="key fact")
        w = int(max(text_w(fl, nfc(fact.label).upper(), 4), text_w(fv, lv[0]))) + 70
        x -= w
        chip = rounded((w, 150), 14, hex_rgb(c.primary))
        cd = ImageDraw.Draw(chip)
        _spaced(cd, (35, 24), nfc(fact.label).upper(), fl, hex_rgb(c.surface, 220), 4)
        cd.text((35, 64), lv[0], font=fv, fill=hex_rgb(c.surface))
        page.alpha_composite(chip, (x, 230))
        x -= 30

    # steps grid
    steps = card.steps[:12]
    n = max(len(steps), 1)
    cols = 3 if n <= 9 else 4
    if n <= 4:
        cols = 2
    rows = math.ceil(n / cols)
    top, bottom = 460, PH - 380
    gap = 40
    cw = (PW - 2 * M - (cols - 1) * gap) // cols
    ch = (bottom - top - (rows - 1) * gap) // rows
    for i, st in enumerate(steps):
        cx = M + (i % cols) * (cw + gap)
        cy = top + (i // cols) * (ch + gap)
        cell = rounded((cw, ch), 22, hex_rgb(c.surface))
        cd = ImageDraw.Draw(cell)
        pad = 28
        ph_h = ch - 2 * pad
        ph_w = min(int(cw * 0.5), int(ph_h * 1.4))
        photo = step_photo(brand, fonts, stills.get(st.photo_time), st, (ph_w, ph_h))
        mask = Image.new("L", photo.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, ph_w - 1, ph_h - 1), 16, fill=255)
        cell.paste(photo, (pad, pad), mask)
        # number badge on the photo
        nb = 74
        cd.ellipse((pad + 14, pad + 14, pad + 14 + nb, pad + 14 + nb), fill=hex_rgb(c.primary), outline=hex_rgb(c.surface), width=4)
        cd.text((pad + 14 + nb / 2, pad + 14 + nb / 2), str(i + 1), font=fonts.get("body", 40, 700), fill=hex_rgb(c.surface), anchor="mm")
        tx = pad + ph_w + 30
        tw = cw - tx - pad
        ty = pad + 4
        ft, lt, _ = fit(fonts, "heading", st.title, tw, 50, 34, 2, warnings=warnings, what=f"step {i + 1} title")
        for ln in lt:
            cd.text((tx, ty), ln, font=ft, fill=hex_rgb(c.text))
            ty += line_h(ft)
        ty += 10
        reserve = (64 if st.key_value else 0) + (52 if st.warning else 0)
        avail = ch - pad - reserve - ty
        size = 30
        while size >= 20:
            fi = fonts.get("body", size, 400, " ".join(st.instructions))
            wrapped = [_wrap(fi, nfc(t), tw - 34) for t in st.instructions]
            need = sum(len(w) for w in wrapped) * line_h(fi) + 8 * len(wrapped)
            if need <= avail:
                break
            size -= 2
        else:
            warnings.append(f"step {i + 1}: instructions too long for the card - shorten them")
        for wl in wrapped:
            cd.ellipse((tx + 2, ty + line_h(fi) // 2 - 6, tx + 14, ty + line_h(fi) // 2 + 6), fill=hex_rgb(c.stripe_a))
            for ln in wl:
                if ty + line_h(fi) > ch - pad - reserve:
                    break
                cd.text((tx + 30, ty), ln, font=fi, fill=hex_rgb(c.text))
                ty += line_h(fi)
            ty += 8
        by = ch - pad - reserve
        if st.key_value:
            fk, lk, _ = fit(fonts, "body", st.key_value, tw - 90, 32, 22, 1, 700, warnings=warnings, what=f"step {i + 1} key value")
            iw = 44 if st.icon != "none" else 0
            kw = int(text_w(fk, lk[0])) + 40 + iw
            cd.rounded_rectangle((tx, by + 6, tx + kw, by + 58), 10, fill=hex_rgb(c.primary))
            if iw:
                draw_icon(cd, st.icon, tx + 16, by + 14, 36, hex_rgb(c.surface))
            cd.text((tx + 20 + iw, by + 32), lk[0], font=fk, fill=hex_rgb(c.surface), anchor="lm")
            by += 64
        if st.warning:
            fw, lw, _ = fit(fonts, "body", st.warning, tw - 50, 28, 20, 1, 700, warnings=warnings, what=f"step {i + 1} warning")
            cd.ellipse((tx, by + 8, tx + 34, by + 42), fill=hex_rgb(c.danger))
            cd.line((tx + 10, by + 18, tx + 24, by + 32), fill=hex_rgb(c.surface), width=4)
            cd.line((tx + 24, by + 18, tx + 10, by + 32), fill=hex_rgb(c.surface), width=4)
            cd.text((tx + 46, by + 25), lw[0], font=fw, fill=hex_rgb(c.danger), anchor="lm")
        cell, sp = with_shadow(cell, 18, (0, 8), 40)
        page.alpha_composite(cell, (cx - sp, cy - sp))

    # footer band with rules
    band_y = PH - 300
    sw = 110
    for i, x in enumerate(range(0, PW, sw)):
        d.rectangle((x, band_y, x + sw, PH), fill=hex_rgb(c.stripe_a if i % 2 == 0 else c.stripe_b))
    rules = card.rules[:4]
    if rules:
        rw = (PW - 2 * M - (len(rules) - 1) * 30) // len(rules)
        for i, r in enumerate(rules):
            chip = rounded((rw, 150), 14, hex_rgb(c.surface, 245))
            cd = ImageDraw.Draw(chip)
            fl = fonts.get("body", 24, 600, r.label)
            _spaced(cd, (30, 22), nfc(r.label).upper(), fl, hex_rgb(c.primary), 4)
            fv, lv, _ = fit(fonts, "body", r.value, rw - 60, 36, 24, 2, 600, warnings=warnings, what="footer rule")
            for j, ln in enumerate(lv):
                cd.text((30, 62 + j * line_h(fv)), ln, font=fv, fill=hex_rgb(c.text))
            page.alpha_composite(chip, (M + i * (rw + 30), band_y + 40))
    if card.footer_note:
        fn = fonts.get("body", 24, 400, card.footer_note)
        d.text((PW - M, band_y - 24), nfc(card.footer_note), font=fn, fill=hex_rgb(c.muted_text), anchor="rd")

    rgb = page.convert("RGB")
    rgb.save(out_png, optimize=True)
    pdf = out_png.with_suffix(".pdf")
    rgb.save(pdf, "PDF", resolution=300)
    return out_png, pdf, warnings


def _spaced(d, xy, s, font, fill, tracking):
    x, y = xy
    for ch in s:
        d.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def _wrap(font, s: str, max_w: int) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if font.getlength(cand) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- photo + symbols


def _crop_rect(img_w: int, img_h: int, crop: Crop, aspect: float) -> tuple[float, float, float, float]:
    """Pixel rect containing the requested crop, grown to the cell's aspect ratio and kept in frame."""
    x, y = crop.x * img_w, crop.y * img_h
    w, h = max(crop.w * img_w, 40), max(crop.h * img_h, 40)
    if w / h < aspect:
        nw = h * aspect
        x -= (nw - w) / 2
        w = nw
    else:
        nh = w / aspect
        y -= (nh - h) / 2
        h = nh
    if w > img_w or h > img_h:  # cannot grow further: fall back to the largest rect of that aspect
        s = min(img_w / w, img_h / h)
        cx, cy = x + w / 2, y + h / 2
        w, h = w * s, h * s
        x, y = cx - w / 2, cy - h / 2
    x = min(max(x, 0), img_w - w)
    y = min(max(y, 0), img_h - h)
    return x, y, w, h


def step_photo(brand: Brand, fonts: FontBook, frame: Image.Image, st: CardStep, size: tuple[int, int]) -> Image.Image:
    c = brand.colors
    fw, fh = frame.size
    cx, cy, cw, ch = _crop_rect(fw, fh, st.photo_crop, size[0] / size[1])
    photo = frame.crop((int(cx), int(cy), int(cx + cw), int(cy + ch))).resize(size, Image.LANCZOS).convert("RGBA")
    sx, sy = size[0] / cw, size[1] / ch

    def to_px(nx: float, ny: float) -> tuple[float, float]:
        return (nx * fw - cx) * sx, (ny * fh - cy) * sy

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    accent, white = hex_rgb(c.accent), hex_rgb(c.surface)
    for m in st.marks[:2]:
        px, py = to_px(m.x, m.y)
        if m.kind in ("ring", "box"):
            rw, rh = max(m.w * fw * sx / 2, 26), max(m.h * fh * sy / 2, 26)
            box = (px - rw, py - rh, px + rw, py + rh)
            for width, col in ((14, white), (7, accent)):
                if m.kind == "ring":
                    d.ellipse(box, outline=col, width=width)
                else:
                    d.rounded_rectangle(box, 12, outline=col, width=width)
            anchor_pt = (px, py + rh + 14) if py + rh + 70 < size[1] else (px, py - rh - 70)
        else:
            # arrow comes in from whichever side has more room and points at the target
            sign = 1 if px > size[0] / 2 else -1  # +1: tail on the left, pointing right
            tail = (px - sign * 150, py)
            neck = (px - sign * 30, py)
            d.line((tail, neck), fill=white, width=16)
            d.line((tail, neck), fill=accent, width=8)
            d.polygon([(px, py), (px - sign * 38, py - 24), (px - sign * 38, py + 24)], fill=accent, outline=white, width=4)
            anchor_pt = (tail[0], py + 28)
        if m.label:
            f = fonts.get("body", 26, 700, m.label)
            label = nfc(m.label).upper()
            lw = int(f.getlength(label)) + 28
            lx = min(max(anchor_pt[0] - lw / 2, 6), size[0] - lw - 6)
            ly = min(max(anchor_pt[1], 6), size[1] - 50)
            d.rounded_rectangle((lx, ly, lx + lw, ly + 44), 8, fill=accent, outline=white, width=3)
            d.text((lx + lw / 2, ly + 22), label, font=f, fill=white, anchor="mm")
    photo.alpha_composite(overlay)
    return photo


def draw_icon(d: ImageDraw.ImageDraw, kind: str, x: float, y: float, s: float, col) -> None:
    """Simple line icons in the guideline's outline style."""
    w = max(int(s / 11), 3)
    if kind == "clock":
        d.ellipse((x, y, x + s, y + s), outline=col, width=w)
        d.line((x + s / 2, y + s / 2, x + s / 2, y + s * 0.22), fill=col, width=w)
        d.line((x + s / 2, y + s / 2, x + s * 0.72, y + s / 2), fill=col, width=w)
    elif kind == "thermometer":
        d.rounded_rectangle((x + s * 0.38, y, x + s * 0.62, y + s * 0.68), s * 0.12, outline=col, width=w)
        d.ellipse((x + s * 0.26, y + s * 0.56, x + s * 0.74, y + s), outline=col, width=w)
        d.line((x + s / 2, y + s * 0.3, x + s / 2, y + s * 0.72), fill=col, width=w)
    elif kind == "scale":
        d.rounded_rectangle((x, y + s * 0.42, x + s, y + s), s * 0.12, outline=col, width=w)
        d.rectangle((x + s * 0.3, y + s * 0.56, x + s * 0.7, y + s * 0.8), outline=col, width=max(w - 1, 2))
        d.line((x + s * 0.1, y + s * 0.3, x + s * 0.9, y + s * 0.3), fill=col, width=w)
        d.line((x + s / 2, y + s * 0.3, x + s / 2, y + s * 0.42), fill=col, width=w)
    elif kind == "cup":
        d.polygon([(x + s * 0.12, y + s * 0.12), (x + s * 0.78, y + s * 0.12), (x + s * 0.7, y + s), (x + s * 0.2, y + s)],
                  outline=col, width=w)
        d.arc((x + s * 0.62, y + s * 0.28, x + s, y + s * 0.7), -90, 90, fill=col, width=w)
        for k in (0.4, 0.6, 0.8):
            d.line((x + s * 0.3, y + s * k, x + s * 0.45, y + s * k), fill=col, width=max(w - 1, 2))
    elif kind == "snowflake":
        import math as _m
        cx, cy, r = x + s / 2, y + s / 2, s / 2
        for a in range(0, 180, 60):
            dx, dy = r * _m.cos(_m.radians(a)), r * _m.sin(_m.radians(a))
            d.line((cx - dx, cy - dy, cx + dx, cy + dy), fill=col, width=w)
    elif kind == "tag":
        d.polygon([(x, y + s * 0.5), (x + s * 0.3, y + s * 0.15), (x + s, y + s * 0.15), (x + s, y + s * 0.85),
                   (x + s * 0.3, y + s * 0.85)], outline=col, width=w)
        d.ellipse((x + s * 0.24, y + s * 0.42, x + s * 0.38, y + s * 0.58), outline=col, width=max(w - 1, 2))
    elif kind == "drop":
        d.polygon([(x + s / 2, y), (x + s * 0.82, y + s * 0.58), (x + s * 0.18, y + s * 0.58)], outline=col, width=w)
        d.ellipse((x + s * 0.16, y + s * 0.34, x + s * 0.84, y + s), outline=col, width=w)

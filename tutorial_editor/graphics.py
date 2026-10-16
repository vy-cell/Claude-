"""Brand template: turns a Scene into a background plus animated layers.

Every visual element is its own transparent PNG layer with an entrance time and an
eased animation. render.py composites them over footage (or a card background) with
ffmpeg so elements glide in one after another, like the reference video, instead of
popping on as hard squares. Layout problems (text that had to shrink or be cut) are
collected as warnings and fed back to the director review loop.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .brand import Brand, hex_rgb
from .models import Scene, VideoPlan

W, H = 1920, 1080  # design grid; render.py scales if the output differs
BAND_Y = 918  # top of the striped band on cards
CAPTION_CY = 987  # caption pill centre line

Anim = Literal["none", "fade", "up", "left", "right", "pop"]


@dataclass
class Layer:
    name: str
    img: Image.Image | None  # RGBA, cropped to its own box
    x: int
    y: int
    enter: float = 0.0
    anim: Anim = "fade"
    # kind=photo: slow push-in on a still from the footage; kind=inset: live crop of the footage
    kind: Literal["image", "photo", "inset"] = "image"
    w: int = 0
    h: int = 0
    radius: int = 0
    still_time: float = -1
    crop: tuple[float, float, float, float] | None = None  # inset source crop (normalised x, y, w, h)


@dataclass
class SceneGraphic:
    background: Image.Image | None  # full-frame RGB for cards; None for footage scenes
    layers: list[Layer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- fonts / text


class FontBook:
    def __init__(self, brand: Brand):
        self.brand = brand

    def _path(self, role: str) -> Path:
        p = self.brand.path(getattr(self.brand.fonts, role))
        if not p or not p.exists():
            raise FileNotFoundError(f"font for '{role}' not found: {p} (put font files in brand/fonts/)")
        return p

    def get(self, role: Literal["heading", "body"], size: int, weight: int = 400, text: str = "") -> ImageFont.FreeTypeFont:
        path = self._path(role)
        if text and not _covers(str(path), text):
            path = self._path(f"{role}_fallback")
        return _font(str(path), size, weight)


@lru_cache(maxsize=None)
def _cmap(path: str) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    return frozenset(TTFont(path, lazy=True).getBestCmap().keys())


def _covers(path: str, text: str) -> bool:
    cmap = _cmap(path)
    return all(ord(c) in cmap for c in text if not c.isspace())


@lru_cache(maxsize=256)
def _font(path: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    try:
        axes = f.get_variation_axes()
    except OSError:
        return f  # static font
    values = []
    for a in axes:
        name = a.get("name", b"")
        name = (name.decode() if isinstance(name, bytes) else str(name)).lower()
        values.append(min(max(weight, a["minimum"]), a["maximum"]) if "weight" in name or name == "wght" else a["default"])
    f.set_variation_by_axes(values)
    return f


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def text_w(font: ImageFont.FreeTypeFont, s: str, tracking: float = 0) -> float:
    return font.getlength(s) + tracking * max(len(s) - 1, 0)


def draw_text(d: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, font, fill, tracking: float = 0) -> None:
    if not tracking:
        d.text(xy, s, font=font, fill=fill)
        return
    x, y = xy
    for ch in s:
        d.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def wrap(font, s: str, max_w: float, tracking: float = 0) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if text_w(font, cand, tracking) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(fonts: FontBook, role, s: str, max_w: float, size: int, min_size: int, max_lines: int, weight=400,
        tracking_em: float = 0, warnings: list[str] | None = None, what: str = "") -> tuple[ImageFont.FreeTypeFont, list[str], float]:
    """Largest size (down to min_size) at which the text wraps into max_lines."""
    s = nfc(s)
    for sz in range(size, min_size - 1, -2):
        f = fonts.get(role, sz, weight, s)
        tr = tracking_em * sz
        lines = wrap(f, s, max_w, tr)
        if len(lines) <= max_lines and all(text_w(f, ln, tr) <= max_w for ln in lines):
            if sz < size * 0.8 and warnings is not None:
                warnings.append(f"{what}: text shrunk to {sz}px to fit - consider shorter wording: {s!r}")
            return f, lines, tr
    f = fonts.get(role, min_size, weight, s)
    tr = tracking_em * min_size
    lines = wrap(f, s, max_w, tr)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,") + "…"
        if warnings is not None:
            warnings.append(f"{what}: text too long, truncated - must be shorter: {s!r}")
    return f, lines, tr


def line_h(font) -> int:
    a, d = font.getmetrics()
    return a + d


# ---------------------------------------------------------------- primitives


def rounded(size: tuple[int, int], radius: int, fill, outline=None, width: int = 0) -> Image.Image:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=fill, outline=outline, width=width)
    return im


def with_shadow(im: Image.Image, blur: int = 14, offset: tuple[int, int] = (0, 6), opacity: int = 70) -> tuple[Image.Image, int]:
    """Soft drop shadow. Returns the new image and the padding added on each side."""
    pad = blur * 2 + max(abs(offset[0]), abs(offset[1]))
    out = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    alpha = im.getchannel("A").point(lambda a: a * opacity // 255)
    shadow.paste((20, 20, 20, 255), (pad + offset[0], pad + offset[1]), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(shadow)
    out.alpha_composite(im, (pad, pad))
    return out, pad


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    sw, sh = img.size
    s = max(size[0] / sw, size[1] / sh)
    img = img.resize((max(1, round(sw * s)), max(1, round(sh * s))), Image.LANCZOS)
    x, y = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((x, y, x + size[0], y + size[1]))


def rounded_photo(img: Image.Image, size: tuple[int, int], radius: int, border: int = 0, border_color=(255, 255, 255, 255)) -> Image.Image:
    ph = cover(img.convert("RGB"), size).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    ph.putalpha(mask)
    if border:
        ImageDraw.Draw(ph).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, outline=border_color, width=border)
    return ph


def load_asset(brand: Brand, p: str | None, height: int) -> Image.Image | None:
    path = brand.path(p)
    if not path or not path.exists():
        return None
    im = Image.open(path).convert("RGBA")
    return im.resize((max(1, round(im.width * height / im.height)), height), Image.LANCZOS)


# ---------------------------------------------------------------- components


class Kit:
    """Brand components. Coordinates are on the 1920x1080 design grid."""

    def __init__(self, brand: Brand, stills: "StillProvider"):
        self.b = brand
        self.c = brand.colors
        self.fonts = FontBook(brand)
        self.stills = stills

    # -- shared chrome

    def badge(self) -> Layer | None:
        text = self.b.template.badge_text
        if not text:
            return None
        f = self.fonts.get("body", 20, 600, text)
        tr = 0.2 * 20
        w = int(text_w(f, text, tr)) + 40
        im = rounded((w, 42), 4, hex_rgb(self.c.badge_bg, 245))
        draw_text(ImageDraw.Draw(im), (20, 9), text, f, hex_rgb(self.c.surface), tr)
        return Layer("badge", im, 1891 - w, 27, 0, "none")

    def caption(self, text: str, warnings: list[str]) -> Layer | None:
        text = nfc(text).strip()
        if not text:
            return None
        if len(text) > self.b.template.caption_max_chars:
            warnings.append(f"caption over {self.b.template.caption_max_chars} chars: {text!r}")
        f, lines, _ = fit(self.fonts, "body", text, 1460, 46, 34, 2, 400, warnings=warnings, what="caption")
        lh = line_h(f)
        w = int(max(text_w(f, ln) for ln in lines)) + 72
        h = lh * len(lines) + 30
        im = rounded((w, h), 6, hex_rgb(self.c.caption_bg, 240))
        d = ImageDraw.Draw(im)
        for i, ln in enumerate(lines):
            d.text(((w - text_w(f, ln)) / 2, 12 + i * lh), ln, font=f, fill=hex_rgb(self.c.surface))
        return Layer("caption", im, (W - w) // 2, CAPTION_CY - h // 2, 0.25, "up")

    def eyebrow(self, text: str, size: int = 22, tracking_em: float = 0.18, color=None, bar=True) -> Image.Image:
        text = nfc(text).upper()
        f = self.fonts.get("body", size, 500, text)
        tr = tracking_em * size
        tw = int(text_w(f, text, tr))
        bar_w = 5 if bar else 0
        im = Image.new("RGBA", (tw + bar_w + 18, line_h(f) + 6), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if bar:
            d.rectangle((0, 2, bar_w - 1, im.height - 3), fill=hex_rgb(self.c.stripe_a))
        draw_text(d, (bar_w + 14 if bar else 0, 1), text, f, color or hex_rgb(self.c.muted_text), tr)
        return im

    # -- footage overlays

    def step_strip(self, step_word: str, code: str, label: str) -> Layer | None:
        if not (code or label):
            return None
        code_t, label_t = nfc(f"{step_word} {code}" if code else "").upper(), nfc(label).upper()
        f = self.fonts.get("body", 24, 600, code_t + label_t)
        tr = 0.14 * 24
        cw = text_w(f, code_t, tr) if code_t else 0
        lw = text_w(f, label_t, tr)
        sep = 30 if code_t and label_t else 0
        w = int(cw + sep + lw) + 52
        im = rounded((w, 56), 6, hex_rgb(self.c.card_bg, 228))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 6, 5, 50), fill=hex_rgb(self.c.primary))
        x = 26
        if code_t:
            draw_text(d, (x, 13), code_t, f, hex_rgb(self.c.primary), tr)
            x += cw + 14
            d.line((x, 16, x, 40), fill=hex_rgb(self.c.muted_text), width=2)
            x += 16
        draw_text(d, (x, 13), label_t, f, hex_rgb(self.c.text, 230), tr)
        return Layer("step", im, 35, 27, 0.05, "right")

    def title_tag(self, code: str, heading: str, warnings: list[str]) -> Layer | None:
        if not heading:
            return None
        f, lines, _ = fit(self.fonts, "heading", heading, 900, 64, 44, 1, warnings=warnings, what="title tag")
        tw = int(text_w(f, lines[0]))
        bw = 0
        if code:
            fc = self.fonts.get("body", 38, 700, code)
            bw = max(90, int(fc.getlength(code)) + 36)
        w = 30 + (bw + 26 if code else 0) + tw + 40
        im = rounded((w, 104), 8, hex_rgb(self.c.card_bg, 228))
        d = ImageDraw.Draw(im)
        x = 16
        if code:
            d.rounded_rectangle((x, 17, x + bw, 87), 10, fill=hex_rgb(self.c.primary))
            d.text((x + (bw - fc.getlength(code)) / 2, 52), code, font=fc, fill=hex_rgb(self.c.surface), anchor="lm")
            x += bw + 26
        d.text((x, 54), lines[0], font=f, fill=hex_rgb(self.c.text), anchor="lm")
        im, pad = with_shadow(im, 10, (0, 4), 40)
        return Layer("tag", im, 35 - pad, 100 - pad, 0.3, "right")

    def callouts(self, scene: Scene, warnings: list[str]) -> tuple[list[Layer], int]:
        layers, y = [], 100
        tones = {"do": self.c.primary, "dont": self.c.danger, "info": self.c.primary_light}
        for i, co in enumerate(scene.callouts[: self.b.template.callout_max]):
            value, label = nfc(co.value).upper(), nfc(co.label).upper()
            if co.tone == "note":
                f, lines, _ = fit(self.fonts, "body", value, 700, 34, 26, 1, 700, warnings=warnings, what="note callout")
                w = int(text_w(f, lines[0])) + 56
                im = rounded((w, 64), 8, hex_rgb(self.c.accent_light, 250))
                ImageDraw.Draw(im).text((w / 2, 32), lines[0], font=f, fill=hex_rgb(self.c.surface), anchor="mm")
            else:
                fv, lines, _ = fit(self.fonts, "body", value, 780, 46, 32, 1, 700, warnings=warnings, what="callout")
                fl = self.fonts.get("body", 22, 500, label)
                trl = 0.18 * 22
                w = int(max(text_w(fv, lines[0]), text_w(fl, label, trl))) + 64
                w = max(w, 260)
                h = 118 if label else 86
                im = rounded((w, h), 10, hex_rgb(tones[co.tone]), outline=hex_rgb(self.c.surface, 245), width=4)
                d = ImageDraw.Draw(im)
                if label:
                    draw_text(d, ((w - text_w(fl, label, trl)) / 2, 18), label, fl, hex_rgb(self.c.surface, 225), trl)
                d.text((w / 2, h - 38 if label else h / 2), lines[0], font=fv, fill=hex_rgb(self.c.surface), anchor="mm")
            im, pad = with_shadow(im, 12, (0, 6), 60)
            layers.append(Layer(f"callout{i}", im, 1888 - (im.width - 2 * pad) - pad, y - pad, 0.55 + 0.3 * i, "left"))
            y += im.height - 2 * pad + 40
        return layers, y

    def pointer(self, p, idx: int, tx: int, ty: int, warnings: list[str]) -> Layer:
        text = nfc(p.text).upper()
        f, lines, _ = fit(self.fonts, "body", text, 520, 32, 24, 1, 700, warnings=warnings, what="pointer")
        tw = int(text_w(f, lines[0]))
        aw, ah, gap = 86, 104, 14
        lw, lh = tw + 48, 62
        im = Image.new("RGBA", (aw + gap + lw + 8, max(ah, lh) + 8), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        cy = im.height // 2
        accent, white = hex_rgb(self.c.accent), hex_rgb(self.c.surface)
        # arrow tip at the left edge, label to the right; mirrored afterwards for side="left"
        tri = [(4, cy), (4 + aw, cy - ah // 2), (4 + aw, cy + ah // 2)]
        d.polygon(tri, fill=accent, outline=white, width=5)
        lx = 4 + aw + gap
        d.rounded_rectangle((lx, cy - lh // 2, lx + lw, cy + lh // 2), 8, fill=accent)
        if p.side == "left":
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
            d = ImageDraw.Draw(im)
            lx = im.width - lx - lw
        d.text((lx + lw / 2, cy), lines[0], font=f, fill=white, anchor="mm")
        im, pad = with_shadow(im, 10, (0, 4), 55)
        x = tx - pad - 4 if p.side == "right" else tx - (im.width - pad) + 4
        x = min(max(x, -pad), W - im.width + pad)
        y = min(max(ty - im.height // 2, 70), BAND_Y - im.height)
        return Layer(f"pointer{idx}", im, x, y, 0.9 + 0.25 * idx, "right" if p.side == "right" else "left")

    def inset(self, ins, idx: int, src_aspect: float, top: int, warnings: list[str]) -> list[Layer]:
        crop_aspect = (ins.w * src_aspect) / max(ins.h, 1e-3)
        h = 540 if ins.position == "left" else min(540, max(300, 820 - top))
        w = int(min(max(h * crop_aspect, 300), 720))
        h = int(min(h, w / crop_aspect)) if crop_aspect > 0 else h
        x = 110 if ins.position == "left" else 1888 - w
        y = 205 if ins.position == "left" else top
        border = 8
        frame = rounded((w, h), 12, None, outline=(255, 255, 255, 255), width=border)
        frame_s, pad = with_shadow(rounded((w, h), 12, (255, 255, 255, 255)), 14, (0, 8), 70)
        # shadow-only layer: cut the interior so it does not cover the live inset
        hole = Image.new("L", frame_s.size, 255)
        ImageDraw.Draw(hole).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), 12, fill=0)
        frame_s.putalpha(Image.composite(frame_s.getchannel("A"), Image.new("L", frame_s.size, 0), hole))
        t0 = 0.7 + 0.2 * idx
        layers = [
            Layer(f"inset{idx}_shadow", frame_s, x - pad, y - pad, t0, "pop"),
            Layer(f"inset{idx}", None, x + border, y + border, t0, "pop", kind="inset", w=w - 2 * border, h=h - 2 * border,
                  radius=6, crop=(ins.x, ins.y, ins.w, ins.h)),
            Layer(f"inset{idx}_frame", frame, x, y, t0, "pop"),
        ]
        if ins.label:
            f, lines, _ = fit(self.fonts, "body", nfc(ins.label).upper(), w + 120, 32, 24, 1, 700, warnings=warnings, what="inset label")
            lw = int(text_w(f, lines[0])) + 52
            pill = rounded((lw, 60), 8, hex_rgb(self.c.accent_light, 250))
            ImageDraw.Draw(pill).text((lw / 2, 30), lines[0], font=f, fill=hex_rgb(self.c.surface), anchor="mm")
            pill, pp = with_shadow(pill, 8, (0, 4), 45)
            layers.append(Layer(f"inset{idx}_label", pill, x + w // 2 - lw // 2 - pp, y + h + 18 - pp, t0 + 0.35, "up"))
        return layers

    # -- cards

    def card_bg(self, band: bool = True) -> Image.Image:
        im = Image.new("RGB", (W, H), hex_rgb(self.c.card_bg))
        if band:
            d = ImageDraw.Draw(im)
            sw = 71
            for i, x in enumerate(range(0, W, sw)):
                d.rectangle((x, BAND_Y, x + sw, H), fill=hex_rgb(self.c.stripe_a if i % 2 == 0 else self.c.stripe_b))
        return im

    def header(self, scene: Scene, warnings: list[str]) -> list[Layer]:
        out = []
        if scene.section:
            out.append(Layer("section", self.eyebrow(scene.section, 18, 0.16), 28, 44, 0.0, "fade"))
        if scene.heading:
            f, lines, _ = fit(self.fonts, "heading", scene.heading, 1500, 84, 56, 1, warnings=warnings, what="card heading")
            im = Image.new("RGBA", (int(text_w(f, lines[0])) + 10, line_h(f) + 10), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((0, 0), lines[0], font=f, fill=hex_rgb(self.c.text))
            out.append(Layer("heading", im, 116, 112, 0.1, "up"))
        if scene.subheading:
            out.append(Layer("subheading", self.eyebrow(scene.subheading, 22, 0.2, bar=True), 112, 222, 0.3, "fade"))
        return out

    def photo(self, name: str, t: float, box: tuple[int, int, int, int], radius: int, enter: float, shadow=True) -> list[Layer]:
        x, y, w, h = box
        layers = []
        if shadow:
            sh, pad = with_shadow(rounded((w, h), radius, (255, 255, 255, 255)), 18, (0, 10), 60)
            hole = Image.new("L", sh.size, 255)
            ImageDraw.Draw(hole).rounded_rectangle((pad + 2, pad + 2, pad + w - 3, pad + h - 3), radius, fill=0)
            sh.putalpha(Image.composite(sh.getchannel("A"), Image.new("L", sh.size, 0), hole))
            layers.append(Layer(f"{name}_shadow", sh, x - pad, y - pad, enter, "pop"))
        layers.append(Layer(name, None, x, y, enter, "pop", kind="photo", w=w, h=h, radius=radius, still_time=t))
        return layers


class StillProvider:
    """Fetches frames from the raw footage for cards (title photo, step thumbnails, right/wrong)."""

    def __init__(self, video: Path, cache_dir: Path):
        self.video, self.cache = video, cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def path(self, t: float) -> Path:
        from .media import still

        p = self.cache / f"still_{t:09.3f}.jpg"
        if not p.exists():
            still(self.video, t, p)
        return p

    def get(self, t: float) -> Image.Image:
        return Image.open(self.path(t)).convert("RGB")


# ---------------------------------------------------------------- scene builders


def build_scene(kit: Kit, plan: VideoPlan, scene: Scene, src_aspect: float, map_point) -> SceneGraphic:
    warnings: list[str] = []
    builder = {
        "title": _title, "overview": _overview, "requirements": _requirements, "checklist": _checklist,
        "chapter": _chapter, "compare": _compare, "closing": _closing, "footage": _footage,
    }[scene.kind]
    g = builder(kit, plan, scene, warnings, src_aspect=src_aspect, map_point=map_point)
    badge = kit.badge()
    if badge:
        g.layers.append(badge)
    cap = kit.caption(scene.caption, warnings)
    if cap:
        g.layers.append(cap)
    g.layers = [layer for layer in g.layers if layer is not None]
    g.warnings = warnings + g.warnings
    return g


def _footage(kit: Kit, plan, s: Scene, warnings, src_aspect, map_point, **_) -> SceneGraphic:
    layers = [kit.step_strip(plan.ui.step, s.step_code, s.section), kit.title_tag(s.step_code, s.heading, warnings)]
    co_layers, bottom = kit.callouts(s, warnings)
    layers += co_layers
    for i, ins in enumerate(s.insets[:1]):
        layers += kit.inset(ins, i, src_aspect, bottom + 10, warnings)
    for i, p in enumerate(s.pointers[:2]):
        tx, ty = map_point(p.x, p.y)
        layers.append(kit.pointer(p, i, tx, ty, warnings))
    return SceneGraphic(None, layers)


def _title(kit: Kit, plan: VideoPlan, s: Scene, warnings, **_) -> SceneGraphic:
    c = kit.c
    bg = kit.card_bg()
    ImageDraw.Draw(bg).rectangle((1215, 0, W, BAND_Y - 1), fill=hex_rgb(c.panel_bg))
    layers: list[Layer] = []
    logo = load_asset(kit.b, kit.b.assets.logo_horizontal, 80)
    if logo:
        layers.append(Layer("logo", logo, 113, 95, 0.0, "fade"))
    title = s.heading or plan.title
    f, lines, _ = fit(kit.fonts, "heading", title, 1020, 124, 80, 2, warnings=warnings, what="title")
    lh = int(line_h(f) * 1.02)
    im = Image.new("RGBA", (1060, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((0, i * lh), ln, font=f, fill=hex_rgb(c.text))
    layers.append(Layer("title", im, 115, 545 - im.height, 0.2, "up"))
    layers.append(Layer("series", kit.eyebrow(s.subheading or plan.series_label, 34, 0.24), 113, 588, 0.55, "fade"))
    if s.still_time >= 0:
        layers += kit.photo("hero", s.still_time, (1275, 250, 590, 420), 22, 0.4)
    mascot = load_asset(kit.b, kit.b.assets.mascot, 390)
    if mascot:
        layers.append(Layer("mascot", mascot, 815, 1000 - mascot.height, 0.9, "pop"))
    return SceneGraphic(bg, layers)


def _overview(kit: Kit, plan: VideoPlan, s: Scene, warnings, **_) -> SceneGraphic:
    layers = kit.header(s, warnings)
    steps = plan.steps[:6]
    n = max(len(steps), 1)
    tw, th, gap = (240, 180, 130) if n <= 4 else (200, 150, 90)
    total = n * tw + (n - 1) * gap
    x0, y0 = (W - total) // 2, 420
    for i, st in enumerate(steps):
        x = x0 + i * (tw + gap)
        t = 0.6 + 0.45 * i
        if st.still_time >= 0:
            layers += kit.photo(f"step{i}", st.still_time, (x, y0, tw, th), 16, t)
        num = rounded((52, 52), 26, hex_rgb(kit.c.primary))
        fnum = kit.fonts.get("body", 26, 700)
        ImageDraw.Draw(num).text((26, 26), str(i + 1), font=fnum, fill=hex_rgb(kit.c.surface), anchor="mm")
        layers.append(Layer(f"num{i}", num, x - 16, y0 - 16, t + 0.15, "pop"))
        f, lines, _ = fit(kit.fonts, "body", st.name, tw + gap - 20, 26, 18, 2, 500, warnings=warnings, what="overview step")
        lab = Image.new("RGBA", (tw + gap, line_h(f) * len(lines) + 6), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lab)
        for j, ln in enumerate(lines):
            dl.text(((tw + gap) / 2, j * line_h(f)), ln, font=f, fill=hex_rgb(kit.c.text), anchor="ma")
        layers.append(Layer(f"label{i}", lab, x - gap // 2, y0 + th + 24, t + 0.2, "up"))
        if i < n - 1:
            arr = Image.new("RGBA", (gap - 40, 40), (0, 0, 0, 0))
            da = ImageDraw.Draw(arr)
            aw = arr.width
            da.polygon([(aw * 0.25, 8), (aw * 0.75, 20), (aw * 0.25, 32)], fill=hex_rgb(kit.c.accent))
            layers.append(Layer(f"arrow{i}", arr, x + tw + 20, y0 + th // 2 - 20, t + 0.35, "right"))
    return SceneGraphic(kit.card_bg(), layers)


def _panel(kit: Kit, size, title: str) -> tuple[Image.Image, int]:
    im = rounded(size, 18, hex_rgb(kit.c.surface, 255))
    ImageDraw.Draw(im)
    if title:
        im.alpha_composite(kit.eyebrow(title, 20, 0.18, bar=False), (36, 30))
    return im, 84


def _requirements(kit: Kit, plan, s: Scene, warnings, **_) -> SceneGraphic:
    layers = kit.header(s, warnings)
    # convention from the planner prompt: equipment rows have an empty detail, ingredient rows carry an amount
    equip = [e for e in s.entries if not e.detail.strip()]
    ingr = [e for e in s.entries if e.detail.strip()]
    c = kit.c
    cols = [(equip, 116, 540, plan.ui.equipment), (ingr, 700, 1104, plan.ui.ingredients)]
    for ci, (items, x, w, title) in enumerate(cols):
        if not items:
            continue
        panel_h = 560
        panel, top = _panel(kit, (w, panel_h), title)
        panel, pad = with_shadow(panel, 16, (0, 8), 35)
        layers.append(Layer(f"panel{ci}", panel, x - pad, 300 - pad, 0.5 + 0.2 * ci, "up"))
        y = 300 + top
        row_h = min(110 if ci else 60, (panel_h - top - 20) // max(len(items), 1))
        for i, e in enumerate(items):
            t = 0.9 + 0.3 * i + 0.2 * ci
            if ci == 0:
                f, lines, _ = fit(kit.fonts, "body", e.title, w - 100, 30, 20, 1, 400, warnings=warnings, what="equipment")
                im = Image.new("RGBA", (w - 60, row_h), (0, 0, 0, 0))
                d = ImageDraw.Draw(im)
                d.ellipse((0, row_h // 2 - 6, 12, row_h // 2 + 6), fill=hex_rgb(c.stripe_a))
                d.text((30, row_h // 2), lines[0], font=f, fill=hex_rgb(c.text), anchor="lm")
            else:
                f1, l1, _ = fit(kit.fonts, "body", e.title, w - 100, 24, 18, 1, 400, warnings=warnings, what="ingredient name")
                f2, l2, _ = fit(kit.fonts, "body", e.detail, w - 100, 38, 24, 1, 700, warnings=warnings, what="ingredient amount")
                im = Image.new("RGBA", (w - 60, row_h), (0, 0, 0, 0))
                d = ImageDraw.Draw(im)
                d.rounded_rectangle((0, 8, 8, row_h - 8), 4, fill=hex_rgb(c.primary))
                d.text((28, row_h // 2 - 4), l1[0], font=f1, fill=hex_rgb(c.muted_text), anchor="ld")
                d.text((28, row_h // 2 + 2), l2[0], font=f2, fill=hex_rgb(c.text), anchor="la")
            layers.append(Layer(f"row{ci}_{i}", im, x + 36, y + i * row_h, t, "up"))
    return SceneGraphic(kit.card_bg(), layers)


def _checklist(kit: Kit, plan, s: Scene, warnings, **_) -> SceneGraphic:
    layers = kit.header(s, warnings)
    items = s.entries[:5]
    n = max(len(items), 1)
    gap = 36
    tw = min(380, (1688 - gap * (n - 1)) // n)
    th = 400
    c = kit.c
    for i, e in enumerate(items):
        tile = rounded((tw, th), 18, hex_rgb(c.surface))
        d = ImageDraw.Draw(tile)
        d.ellipse((30, 30, 92, 92), fill=hex_rgb(c.primary))
        d.text((61, 61), str(i + 1), font=kit.fonts.get("body", 30, 700), fill=hex_rgb(c.surface), anchor="mm")
        if e.kicker:
            tile.alpha_composite(kit.eyebrow(e.kicker, 18, 0.18, bar=False), (32, 140))
        f, lines, _ = fit(kit.fonts, "body", e.title, tw - 64, 40, 26, 2, 700, warnings=warnings, what="checklist title")
        for j, ln in enumerate(lines):
            d.text((32, 186 + j * line_h(f)), ln, font=f, fill=hex_rgb(c.text))
        if e.detail:
            fd, ld, _ = fit(kit.fonts, "body", e.detail, tw - 64, 26, 18, 3, 400, warnings=warnings, what="checklist detail")
            yy = 196 + len(lines) * line_h(f)
            for j, ln in enumerate(ld):
                d.text((32, yy + j * line_h(fd)), ln, font=fd, fill=hex_rgb(c.muted_text))
        tile, pad = with_shadow(tile, 16, (0, 8), 35)
        x = 116 + i * (tw + gap)
        layers.append(Layer(f"tile{i}", tile, x - pad, 330 - pad, 0.6 + 0.55 * i, "up"))
    return SceneGraphic(kit.card_bg(), layers)


def _chapter(kit: Kit, plan: VideoPlan, s: Scene, warnings, **_) -> SceneGraphic:
    c = kit.c
    layers: list[Layer] = []
    n = len(plan.steps)
    eyebrow = s.subheading or (f"{plan.ui.step} {s.chapter_index} {plan.ui.of} {n}" if s.chapter_index else "")
    if eyebrow:
        layers.append(Layer("eyebrow", kit.eyebrow(eyebrow, 22, 0.2), 112, 330, 0.1, "fade"))
    f, lines, _ = fit(kit.fonts, "heading", s.heading, 860, 110, 70, 2, warnings=warnings, what="chapter title")
    lh = int(line_h(f) * 1.0)
    im = Image.new("RGBA", (900, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((0, i * lh), ln, font=f, fill=hex_rgb(c.text))
    layers.append(Layer("chapter_title", im, 116, 380, 0.25, "up"))
    # progress pills
    x, y = 116, 380 + im.height + 40
    for i, st in enumerate(plan.steps):
        active = i + 1 == s.chapter_index
        label = nfc(st.name).upper()
        fp = kit.fonts.get("body", 15, 600, label)
        tr = 0.12 * 15
        pw = int(text_w(fp, label, tr)) + 28
        pill = rounded((pw, 32), 16, hex_rgb(c.primary) if active else (0, 0, 0, 0),
                       outline=None if active else hex_rgb(c.muted_text, 160), width=0 if active else 2)
        draw_text(ImageDraw.Draw(pill), (14, 6), label, fp, hex_rgb(c.surface) if active else hex_rgb(c.muted_text), tr)
        if x + pw > 1000:
            x, y = 116, y + 44
        layers.append(Layer(f"pill{i}", pill, x, y, 0.5 + 0.08 * i, "fade"))
        x += pw + 12
    if s.still_time >= 0:
        layers += kit.photo("chapter_photo", s.still_time, (1080, 90, 760, 760), 24, 0.3)
    return SceneGraphic(kit.card_bg(), layers)


def _compare(kit: Kit, plan, s: Scene, warnings, **_) -> SceneGraphic:
    c = kit.c
    layers = kit.header(s, warnings)
    boxes = []
    if s.wrong_time >= 0:
        boxes.append(("wrong", s.wrong_time, s.wrong_label, c.danger, "✕"))
    if s.right_time >= 0:
        boxes.append(("right", s.right_time, s.right_label, c.primary, "✓"))
    bw, bh = 780, 540
    xs = [120, 1020] if len(boxes) == 2 else [(W - bw) // 2]
    for i, (name, t, label, col, mark) in enumerate(boxes):
        x, y = xs[i], 320
        enter = 0.5 + 1.1 * i
        layers += kit.photo(f"{name}_photo", t, (x, y, bw, bh), 18, enter)
        frame = rounded((bw, bh), 18, None, outline=hex_rgb(col), width=7)
        layers.append(Layer(f"{name}_frame", frame, x, y, enter, "pop"))
        tag_text = nfc(plan.ui.wrong if name == "wrong" else plan.ui.right).upper()
        ft = kit.fonts.get("body", 22, 700, tag_text)
        tw = int(ft.getlength(tag_text)) + 36
        tag = rounded((tw, 40), 8, hex_rgb(col))
        ImageDraw.Draw(tag).text((tw / 2, 20), tag_text, font=ft, fill=hex_rgb(c.surface), anchor="mm")
        layers.append(Layer(f"{name}_tag", tag, x + 22, y + 22, enter + 0.25, "fade"))
        circ = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        dc = ImageDraw.Draw(circ)
        dc.ellipse((0, 0, 63, 63), fill=hex_rgb(col), outline=hex_rgb(c.surface), width=4)
        if name == "wrong":
            dc.line((21, 21, 43, 43), fill=hex_rgb(c.surface), width=6)
            dc.line((43, 21, 21, 43), fill=hex_rgb(c.surface), width=6)
        else:
            dc.line((18, 33, 28, 43), fill=hex_rgb(c.surface), width=6)
            dc.line((28, 43, 47, 22), fill=hex_rgb(c.surface), width=6)
        layers.append(Layer(f"{name}_mark", circ, x + bw - 40, y - 24, enter + 0.4, "pop"))
        if label:
            fl, ll, _ = fit(kit.fonts, "body", nfc(label).upper(), bw - 80, 22, 16, 1, 700, warnings=warnings, what="compare label")
            lw = int(text_w(fl, ll[0])) + 36
            pill = rounded((lw, 38), 8, hex_rgb(col))
            ImageDraw.Draw(pill).text((lw / 2, 19), ll[0], font=fl, fill=hex_rgb(c.surface), anchor="mm")
            layers.append(Layer(f"{name}_label", pill, x + bw // 2 - lw // 2, y + bh - 58, enter + 0.5, "up"))
    return SceneGraphic(kit.card_bg(), layers)


def _closing(kit: Kit, plan, s: Scene, warnings, **_) -> SceneGraphic:
    c = kit.c
    bg = Image.new("RGB", (W, H), hex_rgb(c.card_bg))
    grad = Image.linear_gradient("L").resize((W, H))
    bg = Image.composite(Image.new("RGB", (W, H), hex_rgb(c.stripe_a)), bg, grad.point(lambda v: int(v * 0.85)))
    layers: list[Layer] = []
    logo = load_asset(kit.b, kit.b.assets.logo_horizontal, 70)
    if logo:
        layers.append(Layer("logo", logo, (W - logo.width) // 2, 70, 0.0, "fade"))
    if s.still_time >= 0:
        layers += kit.photo("closing_photo", s.still_time, (W // 2 - 330, 260, 560, 460), 140, 0.3)
    word = nfc(s.heading or kit.b.template.closing_word)
    f, lines, _ = fit(kit.fonts, "heading", word, 700, 150, 80, 1, warnings=warnings, what="closing word")
    tw = int(text_w(f, lines[0])) + 40
    im = Image.new("RGBA", (tw, line_h(f) + 20), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((20, 0), lines[0], font=f, fill=hex_rgb(c.primary))
    im = im.rotate(14, resample=Image.BICUBIC, expand=True)
    layers.append(Layer("closing_word", im, W // 2 + 60, 210, 0.9, "pop"))
    mascot = load_asset(kit.b, kit.b.assets.mascot, 360)
    if mascot:
        layers.append(Layer("mascot", mascot, W // 2 + 170, 820 - mascot.height + 120, 0.6, "pop"))
    return SceneGraphic(bg, layers)

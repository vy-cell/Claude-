"""Two-page SOP recipe card (front + back, US Letter) in the approved Summerfield format.

Front: "how to make" title block with hero photo, batch strip, tools, steps 1-6, before-you-
start line, QR to the training video. Back: work-in-batches flow, remaining steps, storage
timeline, "Never" row, footer with version / effective date / approver.

The page is HTML/CSS printed by headless Chromium. Photos are frames from the finished video
(they already carry its callouts, insets and arrows) or from the raw footage, cropped to the
action. Every generated PDF embeds its own source (card JSON + photos), so
`edit-card card.pdf --request "..."` can change it later; PDFs made elsewhere are imported
by Claude, reusing their photos.
"""

from __future__ import annotations

import html
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date
from glob import glob
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel

from .ai import ask, contact_sheets, image, text
from .brand import Brand, CardInfo
from .graphics import _covers

# ---------------------------------------------------------------- data (what Claude writes)


class Crop(BaseModel):
    x: float  # region of the source frame to show, 0-1
    y: float
    w: float
    h: float


class PhotoRef(BaseModel):
    """Where a photo comes from. Exactly one source is used, in this order: file, scene, time."""

    file: str  # an existing photo file name in the card's photo set ("" for none)
    scene: int  # finished-video scene index to take the frame from (-1 for none)
    time: float  # raw-footage second (-1 for none)
    crop: Crop  # part of that frame to show


class BatchItem(BaseModel):
    value: str  # "40 lb"
    detail: str  # "chicken thigh · 8 bags of 5 lb"


class CardStep(BaseModel):
    title: str  # "Measure the water"
    body: str  # 1-3 short sentences; **bold** the critical words and numbers
    check: str  # success check or must-do, e.g. "Screen reads 0.00"
    phase: int  # 0-based index into `flow` - colours the step number
    photo: PhotoRef


class TimelineNode(BaseModel):
    kicker: str  # "DAY 0", "+24 HRS", "NOT USED?"
    title: str  # "Prep"
    detail: str  # "marinate in fridge"
    tone: Literal["green", "caramel", "blue"]


Icon = Literal["none", "thermometer", "clock", "scale", "cup", "snowflake", "tag", "drop", "hand"]


class NeverItem(BaseModel):
    title: str  # "All powder"
    detail: str  # "at once"
    photo: PhotoRef  # usually the WRONG shot from a Right-vs-Wrong scene
    icon: Icon  # used instead of a photo when there is no good shot ("none" otherwise)


class CardUi(BaseModel):
    """Small template words, translated with the card."""

    tools: str  # "TOOLS"
    before_you_start: str  # "Before you start:"
    turn_over: str  # "Turn over for steps {first}–{last} →"
    watch_title: str  # "Watch it"
    watch_note: str  # "scan for the training video"
    confidential: str  # "Confidential"
    sop: str  # "SOP"
    effective: str  # "Effective"
    approved_by: str  # "Approved by"


class SOPCard(BaseModel):
    kicker: str  # script line above the title, "how to make"
    title: str  # "Popcorn Chicken Marinade"
    short_title: str  # back-page header, "Popcorn Chicken"
    flow: list[str]  # 3-5 phases, "MARINADE", "PORTION", "MIX", "STORE"
    hero: PhotoRef  # finished result
    batch_label: str  # "One batch"
    batch: list[BatchItem]  # up to 3
    tools: list[str]
    steps: list[CardStep]  # 6 on the front, the rest (up to 4) on the back
    before_you_start: list[str]
    batch_flow_label: str  # "Work in batches" ("" to hide)
    batch_flow: list[str]  # "Fill all 8", "Pour all 8", "Mix all 8", "Change gloves" (last is highlighted)
    back_kicker: str  # script line on the back header, "almost done"
    timeline_title: str  # "Ready in 24 hours" ("" to hide the timeline)
    timeline_note: str  # "prepped Mon 2 PM = ready Tue 2 PM"
    timeline: list[TimelineNode]  # up to 5
    never_title: str  # "Never"
    never: list[NeverItem]  # 3-5
    ui: CardUi


class CardReview(BaseModel):
    score: int
    ready_to_ship: bool
    issues: list[str]
    card: SOPCard


class ImportedCard(BaseModel):
    card: SOPCard
    info: CardInfo


FRONT_STEPS = 6

CARD_GUIDE = """\
# The Summerfield SOP recipe card (2 pages, US Letter portrait)

A new team member uses it at the station instead of watching the video. Match the approved
reference card exactly in structure, tone and density.

FRONT
- kicker: 2-3 lowercase words in script ("how to make"). title: the recipe/task name.
- flow: 3-5 CAPS phase words joined by arrows (MARINADE → PORTION → MIX → STORE).
- hero: photo of the finished result.
- batch_label + up to 3 batch items: the batch-level quantities (value big, detail small;
  both units where they exist).
- tools: every tool and its count, short.
- steps 1-6 (front holds exactly 6; the back holds up to 4 more, so 10 max - combine
  sub-steps to fit). Each step: 2-4 word title; body = 1-3 short plain sentences a beginner can
  follow, **bold** only the critical words/numbers (max ~40 words); check = the success test or
  the one rule (max ~8 words), shown with a green tick; phase = which flow phase it belongs to.
- before_you_start: hygiene / safety prep items.
BACK
- batch_flow: the order of work when repeating over many units (last chip = the key action),
  or empty if the task has no batching.
- back_kicker: 2-3 lowercase script words ("almost done").
- timeline: readiness / shelf life / freezing / thawing as up to 5 nodes (green = prep/ready,
  caramel = use-by, blue = freeze/thaw). Empty title if the task has no storage rules.
- never: the 3-5 most costly mistakes, 2-3 word title + 2-4 word detail ("Pour by eye" /
  "always measure"). Prefer the WRONG photo from the video's Right-vs-Wrong scenes; else an icon.
PHOTOS
- Prefer frames from the finished video (photo.scene) - they already show the zoom insets,
  callouts and arrows pointing at the measurement. Crop to the action and the measurement
  graphics; crop OUT the bottom caption pill, the top-left STEP strip, the top-right badge and
  any half-cut graphics. Sharp, mid-action, never a transition or a title card.
- photo.time (raw footage) only when no finished-video frame shows the step.
- crop: the step thumbnail is about 4:3, the hero about 7:5, the Never circles are round.
FACTS: every number must match the video plan exactly. Never invent.
ui: the template words in the card's language.
"""

# ---------------------------------------------------------------- Claude calls


def _ref_blocks(brand: Brand) -> list[dict]:
    refs = sorted((brand.base_dir / "examples" / "card").glob("page-*.png"))
    if not refs:
        return []
    return [text("Approved reference card (match this format and density):")] + [image(p, 1400, 85) for p in refs[:2]]


def _system(brand: Brand) -> str:
    return "\n\n".join([
        f"You write the printed SOP recipe cards for {brand.name}: clear for a total beginner, exact, on brand.",
        CARD_GUIDE,
        "# Brand style guide\n" + brand.style_guide_text(),
    ])


def make_card(brand: Brand, plan_json: str, scene_frames: list[tuple[str, Path]], raw_frames: list[tuple[float, Path]]) -> SOPCard:
    content = [
        *_ref_blocks(brand),
        text("Final video plan (source of truth for steps and facts):\n```json\n" + plan_json + "\n```"),
    ]
    if scene_frames:
        content.append(text("Finished video, one frame per scene (labelled 'scene N'); photo.scene picks from these:"))
        content += [image(s) for s in contact_sheets(scene_frames, cols=2, rows=2, tile_w=960)]
    if raw_frames:
        content.append(text("Raw footage frames (labelled with seconds); photo.time picks from these:"))
        content += [image(s) for s in contact_sheets([(f"{t:.1f}s", p) for t, p in raw_frames], 3, 3, 480)]
    content.append(text("Write the SOP card."))
    return ask(_system(brand), content, SOPCard, effort="high")


def review_card(brand: Brand, card: SOPCard, pages: list[Path], warnings: list[str], scene_frames, raw_frames,
                text_only: bool = False, request: str = "") -> CardReview:
    content = [
        *_ref_blocks(brand),
        text("Current card JSON:\n```json\n" + card.model_dump_json(indent=1) + "\n```"),
        text("Rendered card pages:"), *[image(p, 1600, 85) for p in pages],
        text("Layout warnings from the renderer:\n" + ("\n".join(warnings) or "(none)")),
    ]
    if request:
        content.append(text("The person asked for these changes - they must be reflected:\n" + request))
    if scene_frames and not text_only:
        content += [text("Finished video scenes:")] + [image(s) for s in contact_sheets(scene_frames, 2, 2, 960)]
    if raw_frames and not text_only:
        content += [text("Raw footage frames:")] + [image(s) for s in contact_sheets([(f"{t:.1f}s", p) for t, p in raw_frames], 3, 3, 480)]
    content.append(text(
        "Review as the brand's creative director and as a new hire using it at the station: matches the "
        "reference format; every step doable without the video; photos show the right action and the "
        "measurement, cropped clean (no caption pill / badge / half-cut graphics); nothing overflows; text "
        "short; facts consistent. Photos marked with a file name are already cropped - keep them unless "
        "they are wrong. Return the revised card." + (" Translated version: wording only, keep every photo, "
        "phase, tone, icon and list length." if text_only else "")))
    return ask(_system(brand), content, CardReview, effort="high")


def translate_card(brand: Brand, card: SOPCard, lang_name: str) -> SOPCard:
    content = [text(f"Translate this SOP card into simple, natural {lang_name} for kitchen staff. Keep these terms as "
                    f"written: {', '.join(brand.glossary) or '(none)'}. Keep numbers, both units and **bold** marks. "
                    "Keep every list the same length; keep photo refs, phases, tones and icons unchanged. Keep the "
                    "{first}/{last} placeholders in ui.turn_over. Keep lines short."),
               text("```json\n" + card.model_dump_json(indent=1) + "\n```")]
    out = ask(_system(brand), content, SOPCard, effort="medium")
    return merge_card_text(card, out)


def edit_card(brand: Brand, card: SOPCard, request: str, photos: list[Path]) -> SOPCard:
    content = [
        text("Current card JSON:\n```json\n" + card.model_dump_json(indent=1) + "\n```"),
        text("Photos available (photo.file can use any of these names):"),
        *[image(s) for s in contact_sheets([(p.name, p) for p in photos], 3, 3, 480)],
        text("Apply this change request exactly, keep everything else the same:\n" + request),
    ]
    return ask(_system(brand), content, SOPCard, effort="high")


def import_card(brand: Brand, pdf: Path, photo_dir: Path) -> ImportedCard:
    """Read a card PDF that was not made by this tool, reusing its photos."""
    photo_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", "90", "-png", str(pdf), f"{d}/page"], check=True)
        pages = sorted(Path(d).glob("page*.png"))
        page_blocks = [image(p, 1400, 85) for p in pages[:4]]
        subprocess.run(["pdfimages", "-j", "-png", str(pdf), f"{d}/img"], check=True)
        listing = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True).stdout.splitlines()[2:]
        masks = {i for i, ln in enumerate(listing) if ln.split()[2:3] == ["smask"]}
        for i, p in enumerate(sorted(Path(d).glob("img-*"))):
            if i in masks:
                continue
            im = Image.open(p)
            if min(im.size) >= 150:
                im.convert("RGB").save(photo_dir / f"photo_{i:03d}.jpg", quality=90)
    photos = sorted(photo_dir.glob("photo_*.jpg"))
    pdf_text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    content = [
        text("Card pages:"), *page_blocks,
        text("Extracted text:\n" + pdf_text[:20000]),
        text("Photos extracted from the PDF (use these names in photo.file, crop 0,0,1,1):"),
        *[image(s) for s in contact_sheets([(p.name, p) for p in photos], 3, 3, 480)],
        text("Convert this card into the SOP card JSON exactly as it is (same wording, steps, photos), and read "
             "the footer fields (version, effective date, approver name and title) into info."),
    ]
    return ask(_system(brand), content, ImportedCard, effort="high")


def merge_card_text(src: SOPCard, tr: SOPCard) -> SOPCard:
    """Wording from the translation, photos / phases / tones / icons / counts from the source."""
    out = tr.model_copy(deep=True)
    out.hero = src.hero
    if len(out.steps) != len(src.steps) or len(out.never) != len(src.never) or len(out.timeline) != len(src.timeline):
        raise ValueError("translation changed the card structure")
    for a, b in zip(out.steps, src.steps):
        a.photo, a.phase = b.photo, b.phase
    for a, b in zip(out.never, src.never):
        a.photo, a.icon = b.photo, b.icon
    for a, b in zip(out.timeline, src.timeline):
        a.tone = b.tone
    return out


# ---------------------------------------------------------------- photos


@dataclass
class PhotoSources:
    photo_dir: Path
    raw_video: Path | None = None
    final_video: Path | None = None
    scene_times: list[float] = field(default_factory=list)  # settled-frame time of each scene in final_video


def resolve_photos(card: SOPCard, src: PhotoSources) -> list[str]:
    """Turn every PhotoRef into a cropped file in photo_dir (file refs afterwards, crop = full)."""
    from .media import still

    src.photo_dir.mkdir(parents=True, exist_ok=True)
    warnings = []
    refs = [("hero", card.hero)] + [(f"step{i + 1}", s.photo) for i, s in enumerate(card.steps)] + \
           [(f"never{i + 1}", n.photo) for i, n in enumerate(card.never) if n.icon == "none"]
    for name, ref in refs:
        frame = None
        if ref.file and (src.photo_dir / ref.file).exists():
            if (ref.crop.x, ref.crop.y, ref.crop.w, ref.crop.h) == (0, 0, 1, 1):
                continue
            frame = Image.open(src.photo_dir / ref.file).convert("RGB")
        elif ref.scene >= 0 and src.final_video and ref.scene < len(src.scene_times):
            p = src.photo_dir / f"_scene{ref.scene}.png"
            if not p.exists():
                still(src.final_video, src.scene_times[ref.scene], p)
            frame = Image.open(p).convert("RGB")
        elif ref.time >= 0 and src.raw_video:
            p = src.photo_dir / f"_raw{ref.time:.2f}.png"
            if not p.exists():
                still(src.raw_video, ref.time, p)
            frame = Image.open(p).convert("RGB")
        if frame is None:
            warnings.append(f"{name}: no photo source available")
            ref.file = ""
            continue
        c = ref.crop
        x0, y0 = max(0.0, min(c.x, 0.95)), max(0.0, min(c.y, 0.95))
        x1, y1 = min(1.0, x0 + max(c.w, 0.05)), min(1.0, y0 + max(c.h, 0.05))
        W, H = frame.size
        crop = frame.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
        crop.thumbnail((1000, 1000))
        out_name = f"{name}_{abs(hash((ref.scene, ref.time, ref.file, x0, y0, x1, y1))) % 10**8}.jpg"
        crop.save(src.photo_dir / out_name, quality=90)
        ref.file, ref.scene, ref.time = out_name, -1, -1
        ref.crop = Crop(x=0, y=0, w=1, h=1)
    return warnings


# ---------------------------------------------------------------- rendering


@dataclass
class CardRender:
    pdf: Path
    pages: list[Path]
    warnings: list[str]


def chrome_path() -> str:
    import os

    for c in (os.environ.get("CHROME_PATH"), shutil.which("chromium"), shutil.which("google-chrome"),
              shutil.which("chromium-browser"), *sorted(glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))):
        if c and Path(c).exists():
            return c
    raise RuntimeError("Chromium/Chrome not found - install it or set CHROME_PATH")


def render_card(brand: Brand, card: SOPCard, info: CardInfo, lang: str, photo_dir: Path, out_pdf: Path) -> CardRender:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_pdf.with_suffix(".html")
    html_path.write_text(card_html(brand, card, info, lang, photo_dir), encoding="utf-8")
    chrome = chrome_path()
    base = [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files", "--hide-scrollbars"]
    uri = html_path.resolve().as_uri()
    raw_pdf = out_pdf.with_suffix(".raw.pdf")
    subprocess.run(base + ["--no-pdf-header-footer", f"--print-to-pdf={raw_pdf}", uri], check=True, capture_output=True)
    dom = subprocess.run(base + ["--virtual-time-budget=5000", "--dump-dom", uri], check=True, capture_output=True, text=True).stdout
    warnings = _overflow_warnings(dom)
    _embed_source(raw_pdf, out_pdf, card, info, lang, photo_dir)
    raw_pdf.unlink(missing_ok=True)
    n_pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(out_pdf)], capture_output=True, text=True).stdout).group(1))
    if n_pages != 2:
        warnings.append(f"card printed on {n_pages} pages instead of 2 - shorten text")
    prefix = out_pdf.with_suffix("")
    subprocess.run(["pdftoppm", "-r", "110", "-png", str(out_pdf), f"{prefix}-page"], check=True)
    pages = sorted(out_pdf.parent.glob(prefix.name + "-page-*.png"))
    return CardRender(out_pdf, pages, warnings)


def _overflow_warnings(dom: str) -> list[str]:
    m = re.search(r'data-overflow="([^"]*)"', dom)
    if not m:
        return []
    return [w for w in html.unescape(m.group(1)).split("|") if w]


def _embed_source(src_pdf: Path, out_pdf: Path, card: SOPCard, info: CardInfo, lang: str, photo_dir: Path) -> None:
    import logging

    from pypdf import PdfReader, PdfWriter

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # Chrome PDFs trigger a harmless trailer-size notice

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("card.json", card.model_dump_json(indent=1))
        z.writestr("info.json", info.model_dump_json(indent=1))
        z.writestr("lang.txt", lang)
        used = {card.hero.file} | {s.photo.file for s in card.steps} | {n.photo.file for n in card.never}
        for name in sorted(u for u in used if u):
            p = photo_dir / name
            if p.exists():
                z.write(p, f"photos/{name}")
    w = PdfWriter(clone_from=PdfReader(str(src_pdf)))
    w.add_attachment("card_source.zip", buf.getvalue())
    w.add_metadata({"/Title": card.title, "/Subject": "Summerfield SOP card", "/Keywords": f"sop-card;lang={lang};v={info.version}"})
    with open(out_pdf, "wb") as f:
        w.write(f)


def load_embedded(pdf: Path, photo_dir: Path) -> tuple[SOPCard, CardInfo, str] | None:
    """Card source embedded by render_card, or None for PDFs made elsewhere."""
    from pypdf import PdfReader

    att = PdfReader(str(pdf)).attachments.get("card_source.zip")
    if not att:
        return None
    photo_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(att[0])) as z:
        card = SOPCard.model_validate_json(z.read("card.json"))
        info = CardInfo.model_validate_json(z.read("info.json"))
        lang = z.read("lang.txt").decode()
        for n in z.namelist():
            if n.startswith("photos/") and not n.endswith("/"):
                (photo_dir / Path(n).name).write_bytes(z.read(n))
    return card, info, lang


# ---------------------------------------------------------------- HTML template


def _e(s: str) -> str:
    return html.escape(s or "")


def _rich(s: str) -> str:
    """Escape, then **bold** -> <strong>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _e(s))


def _font_url(brand: Brand, rel: str) -> str:
    p = brand.path(rel)
    return p.resolve().as_uri() if p and p.exists() else ""


def _all_text(card: SOPCard, *fields: str) -> str:
    return " ".join(str(getattr(card, f)) for f in fields)


ICONS = {
    "thermometer": '<path d="M10 4a2 2 0 0 1 4 0v10.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "scale": '<rect x="3" y="10" width="18" height="10" rx="2"/><path d="M5 6h14M12 6v4"/><rect x="8" y="13" width="8" height="4"/>',
    "cup": '<path d="M5 4h12l-1.5 16h-9z"/><path d="M17 8h2a2 2 0 0 1 0 4h-2.4"/><path d="M8 9h3M8 13h3"/>',
    "snowflake": '<path d="M12 2v20M3.3 7l17.4 10M3.3 17L20.7 7"/>',
    "tag": '<path d="M3 12l6-7h12v14H9z"/><circle cx="9.5" cy="12" r="1.3"/>',
    "drop": '<path d="M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11z"/>',
    "hand": '<path d="M7 11V6a1.5 1.5 0 0 1 3 0v4M10 10V4.5a1.5 1.5 0 0 1 3 0V10M13 10V5.5a1.5 1.5 0 0 1 3 0V12M16 10a1.5 1.5 0 0 1 3 0v4a7 7 0 0 1-7 7h-1a6 6 0 0 1-5-3l-2.5-4a1.5 1.5 0 0 1 2.5-1.6L7 15"/>',
}
CHECK = '<svg viewBox="0 0 24 24" class="tick"><path d="M4 12.5l5 5L20 6.5"/></svg>'


def card_html(brand: Brand, card: SOPCard, info: CardInfo, lang: str, photo_dir: Path) -> str:
    c, f = brand.colors, brand.fonts
    phases = [c.phase_a, c.phase_b, c.phase_c, c.phase_d]
    tones = {"green": (c.tone_green, c.tone_green), "caramel": (c.tone_caramel, c.tone_caramel_text), "blue": (c.tone_blue, c.tone_blue_text)}

    # one font per role for the whole card: if a language needs letters the brand font lacks,
    # switch that role to its fallback everywhere, so words never mix two typefaces
    head_text = _all_text(card, "title", "short_title", "batch_label", "batch_flow_label", "timeline_title", "never_title") + \
        " ".join(s.title for s in card.steps) + " ".join(n.title for n in card.timeline) + card.ui.watch_title + \
        " ".join(b.value for b in card.batch)
    heading_font = f.heading if _covers(str(brand.path(f.heading)), head_text) else f.heading_fallback
    accent_font = f.accent if _covers(str(brand.path(f.accent)), card.kicker + card.back_kicker) else f.heading_fallback
    accent_italic = "normal" if accent_font == f.accent else "italic"

    def photo(ref: PhotoRef, cls: str) -> str:
        if ref.file and (photo_dir / ref.file).exists():
            return f'<div class="{cls}"><img src="{(photo_dir / ref.file).resolve().as_uri()}"></div>'
        return f'<div class="{cls} missing"></div>'

    def step_html(i: int, s: CardStep) -> str:
        col = phases[s.phase % 4] if s.phase >= 0 else phases[0]
        check = f'<div class="check">{CHECK}<span>{_rich(s.check)}</span></div>' if s.check else ""
        return (f'<div class="step" data-name="step {i + 1}">{photo(s.photo, "sph")}<div class="stx">'
                f'<div class="sth"><span class="num" style="background:{col}">{i + 1}</span><h3>{_e(s.title)}</h3></div>'
                f'<p>{_rich(s.body)}</p>{check}</div></div>')

    front_steps = card.steps[:FRONT_STEPS]
    back_steps = card.steps[FRONT_STEPS:]
    arrow = '<span class="arr">→</span>'
    flow = arrow.join(f"<span>{_e(x.upper())}</span>" for x in card.flow)
    batch = "".join(f'<div class="bi"><div class="bv">{_e(b.value)}</div><div class="bd">{_e(b.detail)}</div></div>' for b in card.batch[:3])
    tools = " · ".join(_e(t) for t in card.tools)
    before = " · ".join(_e(t) for t in card.before_you_start)
    turn = ""
    if back_steps:
        turn = _e(card.ui.turn_over.replace("{first}", str(FRONT_STEPS + 1)).replace("{last}", str(len(card.steps))))
    url = info.video_url.replace("{lang}", lang)
    watch = ""
    if url:
        import segno

        qr = segno.make(url, error="m").svg_inline(scale=3, dark=c.text, border=0)
        watch = (f'<div class="watch"><div><div class="wt">{_e(card.ui.watch_title)}</div>'
                 f'<div class="wn">{_e(card.ui.watch_note)}</div></div><div class="qr">{qr}</div></div>')
    chips = ""
    if card.batch_flow_label and card.batch_flow:
        items = [f'<span class="chip{" dark" if i == len(card.batch_flow) - 1 else ""}">{_e(x)}</span>' for i, x in enumerate(card.batch_flow)]
        chips = f'<div class="bflow"><span class="bfl">{_e(card.batch_flow_label)}</span><div class="chips">{arrow.join(items)}</div></div>'
    timeline = ""
    if card.timeline_title and card.timeline:
        nodes = "".join(
            f'<div class="node"><span class="dot" style="background:{tones[n.tone][0]}"></span>'
            f'<div class="nk" style="color:{tones[n.tone][1]}">{_e(n.kicker.upper())}</div>'
            f'<div class="nt">{_e(n.title)}</div><div class="nd">{_e(n.detail)}</div></div>' for n in card.timeline[:5])
        timeline = (f'<section class="tl"><div class="tlh"><h2>{_e(card.timeline_title)}</h2>'
                    f'<span>· {_e(card.timeline_note)}</span></div><div class="nodes">{nodes}</div></section>')
    never = ""
    if card.never:
        items = []
        for n in card.never[:5]:
            if n.icon != "none" or not n.photo.file:
                icon = ICONS.get(n.icon, ICONS["hand"])
                circle = f'<div class="nc icon"><svg viewBox="0 0 24 24">{icon}</svg></div>'
            else:
                circle = photo(n.photo, "nc")
            items.append(f'<div class="nv">{circle}<div class="nvt">{_e(n.title)}</div><div class="nvd">{_e(n.detail)}</div></div>')
        never = f'<section class="never"><h2>{_e(card.never_title)}</h2><div class="nvs">{"".join(items)}</div></section>'
    eff = info.effective or date.today().strftime(info.date_format)
    who = ", ".join(x for x in (info.approved_by, info.approver_title) if x)
    foot_parts = [brand.name, card.ui.confidential, f"{card.ui.sop} v{info.version}" if info.version else "",
                  f"{card.ui.effective} {eff}", f"{card.ui.approved_by} {who}" if who else ""]
    footer = " · ".join(_e(p) for p in foot_parts if p)
    logo_stacked = brand.path("assets/logo-stacked-dark.png")
    logo_mark = brand.path(brand.assets.logo_mark)
    img = lambda p, cls: f'<img class="{cls}" src="{p.resolve().as_uri()}">' if p and p.exists() else ""  # noqa: E731

    css = f"""
@font-face {{ font-family: Head; src: url('{_font_url(brand, heading_font)}'); }}
@font-face {{ font-family: Body; src: url('{_font_url(brand, f.body)}'); font-weight: 100 900; }}
@font-face {{ font-family: Script; src: url('{_font_url(brand, accent_font)}'); }}
@page {{ size: 8.5in 11in; margin: 0; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ background: #fff; }}
body {{ font-family: Body, sans-serif; color: {c.text}; font-size: 13.2px; line-height: 1.5; -webkit-print-color-adjust: exact; }}
.page {{ width: 816px; height: 1056px; position: relative; overflow: hidden; page-break-after: always; background: #fff; }}
.inner {{ padding: 0 39px; }}
h1, h2, h3, .bv, .wt, .bfl, .nt, .blabel {{ font-family: Head, serif; font-weight: 400; }}
.hdr {{ background: {c.card_bg}; height: 196px; position: relative; padding: 30px 39px 0; }}
.stripe {{ height: 10px; margin-top: 0; background: repeating-linear-gradient(90deg, {c.stripe_a} 0 14px, transparent 14px 29px); }}
.stripe-wrap {{ background: linear-gradient({c.card_bg} 0 50%, #fff 50% 100%); padding: 0; }}
.logo {{ height: 46px; display: block; }}
.kicker {{ font-family: Script, cursive; font-style: {accent_italic}; color: {c.tone_green}; font-size: 23px; line-height: 1; margin-top: 10px; }}
h1 {{ font-size: 40px; line-height: 1.08; margin-top: 2px; max-width: 520px; }}
.flow {{ font-weight: 600; font-size: 12px; letter-spacing: .19em; margin-top: 10px; }}
.flow .arr, .chips .arr {{ color: {c.accent}; margin: 0 .6em; letter-spacing: 0; font-weight: 400; font-size: 10px; }}
.hero {{ position: absolute; right: 39px; top: 31px; width: 205px; height: 146px; border: 5px solid #fff; border-radius: 14px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,.08); }}
.hero img, .sph img, .nc img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.batch {{ display: flex; margin-top: 22px; border: 1.5px solid {c.stripe_b}; border-radius: 14px; overflow: hidden; min-height: 72px; }}
.blabel {{ background: {c.stripe_a}; width: 116px; display: flex; align-items: center; justify-content: center; font-size: 19px; text-align: center; padding: 0 8px; }}
.bi {{ flex: 1; padding: 10px 16px; border-left: 1.5px solid {c.stripe_b}; }}
.bv {{ font-size: 23px; line-height: 1.15; }}
.bd {{ font-size: 12px; line-height: 1.35; color: {c.text}; }}
.tools {{ margin-top: 12px; font-size: 12.4px; line-height: 1.55; }}
.tools b {{ color: {c.tone_green}; letter-spacing: .2em; font-weight: 700; margin-right: .5em; }}
.steps {{ display: grid; grid-template-columns: 1fr 1fr; column-gap: 28px; row-gap: 20px; margin-top: 22px; }}
.step {{ display: flex; gap: 15px; }}
.sph {{ flex: 0 0 149px; height: 112px; border-radius: 12px; overflow: hidden; background: {c.card_bg}; }}
.stx {{ flex: 1; min-width: 0; }}
.sth {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
.num {{ flex: 0 0 27px; height: 27px; border-radius: 50%; color: #fff; font-family: Head, serif; font-size: 15px; display: flex; align-items: center; justify-content: center; }}
h3 {{ font-size: 18.5px; line-height: 1.12; }}
.stx p {{ font-size: 12.3px; line-height: 1.47; }}
.stx strong {{ font-weight: 650; }}
.check {{ display: flex; gap: 6px; color: {c.tone_green}; font-weight: 650; font-size: 11.8px; line-height: 1.4; margin-top: 5px; }}
.tick {{ flex: 0 0 13px; width: 13px; height: 13px; margin-top: 3px; fill: none; stroke: {c.tone_green}; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }}
.ffoot {{ position: absolute; left: 39px; right: 39px; bottom: 28px; display: flex; align-items: flex-end; gap: 24px; }}
.before {{ flex: 1; font-size: 12.2px; line-height: 1.6; }}
.before b {{ font-weight: 700; }}
.turn {{ font-weight: 700; margin-top: 8px; }}
.watch {{ flex: 0 0 182px; background: {c.card_bg}; border-radius: 14px; padding: 10px 10px 10px 12px; display: flex; gap: 10px; align-items: center; justify-content: flex-end; text-align: right; }}
.wt {{ font-size: 19px; line-height: 1.1; }}
.wn {{ font-size: 11.6px; line-height: 1.35; }}
.qr svg {{ width: 70px; height: 70px; display: block; }}
.hdr2 {{ background: {c.card_bg}; height: 80px; display: flex; align-items: center; justify-content: space-between; padding: 0 39px; }}
.hdr2 .left {{ display: flex; align-items: center; gap: 14px; }}
.mark {{ height: 38px; }}
.hdr2 h2 {{ font-size: 27px; }}
.kicker2 {{ font-family: Script, cursive; font-style: {accent_italic}; color: {c.tone_green}; font-size: 22px; }}
.bflow {{ margin-top: 24px; border: 1.5px solid {c.stripe_b}; border-radius: 22px; padding: 7px 8px 7px 20px; display: flex; align-items: center; justify-content: space-between; }}
.bfl {{ font-size: 18px; }}
.chip {{ background: {c.chip_bg}; border-radius: 12px; padding: 4px 11px; font-weight: 650; font-size: 12.2px; }}
.chip.dark {{ background: {c.text}; color: #fff; }}
.tl {{ background: {c.card_bg}; border-radius: 18px; padding: 18px 26px 20px; margin-top: 22px; }}
.tlh h2 {{ display: inline; font-size: 25px; }}
.tlh span {{ font-size: 13px; margin-left: 6px; }}
.nodes {{ display: flex; margin-top: 16px; position: relative; }}
.nodes::before {{ content: ""; position: absolute; left: 10%; right: 10%; top: 6px; height: 1.5px; background: {c.tone_green}; opacity: .55; }}
.node {{ flex: 1; text-align: center; position: relative; padding: 0 4px; }}
.dot {{ display: block; width: 14px; height: 14px; border-radius: 50%; border: 2.5px solid #fff; margin: 0 auto 10px; box-shadow: 0 0 0 1px rgba(0,0,0,.04); }}
.nk {{ font-weight: 700; font-size: 11.5px; letter-spacing: .14em; }}
.nt {{ font-size: 19px; line-height: 1.2; margin-top: 2px; }}
.nd {{ font-size: 11.8px; line-height: 1.35; margin-top: 2px; }}
.never h2 {{ font-size: 25px; margin: 22px 0 10px; }}
.nvs {{ display: flex; justify-content: space-between; }}
.nv {{ width: 150px; text-align: center; }}
.nc {{ width: 76px; height: 76px; border-radius: 50%; overflow: hidden; margin: 0 auto 8px; border: 3px solid {c.never_ring}; background: #fff; display: flex; align-items: center; justify-content: center; }}
.nc.icon svg {{ width: 34px; height: 34px; fill: none; stroke: {c.danger}; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }}
.nvt {{ font-weight: 700; font-size: 12.2px; line-height: 1.3; }}
.nvd {{ font-size: 12px; line-height: 1.35; }}
.foot2 {{ position: absolute; left: 39px; right: 39px; bottom: 24px; display: flex; align-items: center; gap: 10px; font-size: 12px; color: {c.muted_text}; }}
.foot2 .mk {{ height: 18px; opacity: .75; }}
.foot2 .pg {{ margin-left: auto; }}
.missing {{ background: repeating-linear-gradient(45deg, {c.card_bg} 0 8px, #fff 8px 16px); }}
"""
    # measures overflow so the director loop can fix wording; result lands in body[data-overflow]
    js = """
document.fonts.ready.then(() => {
  const out = [];
  document.querySelectorAll('.page').forEach((p, i) => {
    const kids = [...p.querySelectorAll('.inner > *, .ffoot')];
    const bottom = Math.max(...kids.map(k => k.getBoundingClientRect().bottom - p.getBoundingClientRect().top));
    const limit = p.clientHeight - (i === 0 ? 0 : 50);
    if (bottom > limit + 1) out.push(`page ${i + 1} overflows by ${Math.round(bottom - limit)}px - shorten text or combine steps`);
  });
  const ff = document.querySelector('.ffoot'), st = document.querySelector('.page .steps');
  if (ff && st && st.getBoundingClientRect().bottom > ff.getBoundingClientRect().top - 8)
    out.push(`front steps run into the footer by ${Math.round(st.getBoundingClientRect().bottom - ff.getBoundingClientRect().top + 8)}px - shorten step text`);
  document.querySelectorAll('.step').forEach(s => {
    const t = s.querySelector('.stx');
    if (t.scrollWidth > t.clientWidth + 1) out.push(`${s.dataset.name}: a word is too long for the column`);
  });
  document.body.dataset.overflow = out.join('|');
});
"""
    front = f"""
<div class="page">
  <div class="hdr">
    {img(logo_stacked, "logo")}
    <div class="kicker">{_e(card.kicker)}</div>
    <h1>{_e(card.title)}</h1>
    <div class="flow">{flow}</div>
    {photo(card.hero, "hero")}
  </div>
  <div class="stripe-wrap"><div class="stripe"></div></div>
  <div class="inner">
    <div class="batch"><div class="blabel">{_e(card.batch_label)}</div>{batch}</div>
    <div class="tools"><b>{_e(card.ui.tools.upper())}</b>{tools}</div>
    <div class="steps">{"".join(step_html(i, s) for i, s in enumerate(front_steps))}</div>
  </div>
  <div class="ffoot">
    <div class="before"><b>{_e(card.ui.before_you_start)}</b> {before}{f'<div class="turn">{turn}</div>' if turn else ""}</div>
    {watch}
  </div>
</div>"""
    back = f"""
<div class="page">
  <div class="hdr2"><div class="left">{img(logo_mark, "mark")}<h2>{_e(card.short_title)}</h2></div><div class="kicker2">{_e(card.back_kicker)}</div></div>
  <div class="stripe-wrap"><div class="stripe"></div></div>
  <div class="inner">
    {chips}
    {f'<div class="steps">{"".join(step_html(FRONT_STEPS + i, s) for i, s in enumerate(back_steps))}</div>' if back_steps else ""}
    {timeline}
    {never}
  </div>
  <div class="foot2">{img(logo_mark, "mk")}<span>{footer}</span><span class="pg">2 / 2</span></div>
</div>"""
    return f"""<!doctype html><html lang="{_e(lang)}"><head><meta charset="utf-8"><title>{_e(card.title)}</title>
<style>{css}</style></head><body>{front}{back}<script>{js}</script></body></html>"""

"""Keep the brand kit current.

update-guideline: read a (new) brand guideline PDF, update brand.yaml colour/font tokens and
                  regenerate the guideline section of style_guide.md. The old versions are kept
                  in brand/history/ so any update can be rolled back.
learn-example:    study an approved finished video, store reference frames for the director
                  and regenerate the "video template" section of style_guide.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel

from .ai import ask, contact_sheets, image, text
from .brand import Brand, Colors
from .media import duration, run, still

GUIDE_START, GUIDE_END = "<!-- guideline:start -->", "<!-- guideline:end -->"
SAMPLE_START, SAMPLE_END = "<!-- sample:start -->", "<!-- sample:end -->"


class FontChoice(BaseModel):
    heading: str  # family name, e.g. "Caudex"
    body: str
    accent: str


class GuidelineUpdate(BaseModel):
    colors: Colors  # every template token mapped to the guideline palette
    fonts: FontChoice
    voice_and_tone: str
    style_guide_markdown: str  # the guideline section of style_guide.md
    changes: list[str]  # what differs from the current configuration


class SampleNotes(BaseModel):
    template_markdown: str  # the "video template" section of style_guide.md
    reference_times: list[float]  # 8-12 source seconds that best show the look (one per scene type)


def _replace_section(doc: str, start: str, end: str, body: str) -> str:
    block = f"{start}\n{body.strip()}\n{end}"
    if start in doc and end in doc:
        return re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: block, doc, flags=re.S)
    return (doc.rstrip() + "\n\n" + block + "\n").lstrip()


def _backup(brand: Brand) -> Path:
    hist = brand.base_dir / "history" / time.strftime("%Y%m%d-%H%M%S")
    hist.mkdir(parents=True, exist_ok=True)
    for p in (brand.source_file, brand.path(brand.style_guide)):
        if p and p.exists():
            shutil.copy2(p, hist / p.name)
    return hist


def update_guideline(brand: Brand, pdf: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", "50", "-jpeg", str(pdf), f"{d}/p"], check=True)
        pages = sorted(Path(d).glob("p*.jpg"))
        page_blocks = [image(p, 1400, 80) for p in pages[:90]]
        pdf_text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
    current = brand.model_dump_json(include={"colors", "fonts", "voice_and_tone"}, indent=1)
    system = ("You maintain the brand kit for an automated video/print template. You read brand guideline "
              "documents precisely and never invent values that are not in them.")
    content = [
        text("New brand guideline - page images follow, then the extracted text."),
        *page_blocks,
        text("Extracted text:\n" + pdf_text[:60000]),
        text("Current template configuration:\n```json\n" + current + "\n```\n\n"
             "Map the guideline onto the template tokens. Use the guideline's exact HEX values wherever the "
             "guideline defines a colour for that role; tokens the guideline does not define (e.g. darker UI "
             "shades derived for contrast) keep their current value unless they now clash with the palette. "
             "Fonts: family names exactly as in the guideline. style_guide_markdown: a concise but complete "
             "summary of every rule that matters for video and print (feel, palette with roles, typography and "
             "where each font is used, logo usage, layout do's and don'ts, photography direction, text "
             "density). changes: list every difference from the current configuration."),
    ]
    upd = ask(system, content, GuidelineUpdate, effort="high")

    hist = _backup(brand)
    gdir = brand.base_dir / "guidelines"
    gdir.mkdir(exist_ok=True)
    shutil.copy2(pdf, gdir / pdf.name)

    # targeted edits keep the comments in brand.yaml intact
    y = brand.source_file.read_text()
    for k, v in upd.colors.model_dump().items():
        y = re.sub(rf'(^\s*{k}:\s*)"#[0-9A-Fa-f]{{6}}"', lambda m: f'{m.group(1)}"{v.upper()}"', y, flags=re.M)
    notes = list(upd.changes)
    for role, family in (("heading", upd.fonts.heading), ("body", upd.fonts.body)):
        current_file = getattr(brand.fonts, role)
        if family.replace(" ", "").lower() in Path(current_file).name.replace(" ", "").lower():
            continue
        match = next((p for p in (brand.base_dir / "fonts").glob("*.[ot]tf")
                      if family.replace(" ", "").lower() in p.name.lower()), None)
        if match:
            y = re.sub(rf'(^\s*{role}:\s*)".*?"', lambda m: f'{m.group(1)}"fonts/{match.name}"', y, count=1, flags=re.M)
            notes.append(f"{role} font -> {match.name}")
        else:
            notes.append(f"ACTION NEEDED: guideline {role} font is '{family}' - add its .ttf/.otf to brand/fonts/ "
                         f"and re-run (still using {current_file}).")
    y = re.sub(r'(^voice_and_tone:\s*).*$', lambda m: m.group(1) + _yaml_str(upd.voice_and_tone), y, count=1, flags=re.M)
    brand.source_file.write_text(y)

    sg = brand.path(brand.style_guide)
    doc = sg.read_text() if sg.exists() else "# Brand style guide\n"
    sg.write_text(_replace_section(doc, GUIDE_START, GUIDE_END, upd.style_guide_markdown))
    notes.append(f"previous versions saved in {hist}")
    return notes


def learn_example(brand: Brand, video: Path, name: str) -> list[str]:
    dur = duration(video)
    with tempfile.TemporaryDirectory() as d:
        frames = []
        n = 48
        for i in range(n):
            t = (i + 0.5) * dur / n
            frames.append((f"{t:.1f}s", still(video, t, Path(d) / f"{i:03d}.jpg", 640)))
        sheets = contact_sheets(frames, cols=3, rows=3, tile_w=640)
        loud = subprocess.run(["ffmpeg", "-i", str(video), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
        m = re.search(r"I:\s+(-?[\d.]+) LUFS", loud.split("Summary:")[-1])
        lufs = m.group(1) if m else "unknown"
        content = [
            text(f"Approved example video '{name}': {dur:.0f} s, integrated loudness {lufs} LUFS. "
                 f"{n} evenly spaced frames follow, labelled with time."),
            *[image(s) for s in sheets],
            text("Describe the reusable video template this example follows so every future video is edited the "
                 "same way: structure and scene types in order, what each scene type contains and where, typography "
                 "per element, colours, callout/pointer/inset conventions, caption style and length, transitions and "
                 "animation feel, pacing (scene lengths), narration style, total length. Write it as instructions for "
                 "an editor (markdown). Also pick 8-12 timestamps that best show the look, one per scene type."),
        ]
        notes = ask("You are a senior motion designer documenting a brand video template.", content, SampleNotes, effort="high")
    ref_dir = brand.base_dir / "examples" / name
    ref_dir.mkdir(parents=True, exist_ok=True)
    for t in notes.reference_times[:12]:
        still(video, min(max(t, 0), dur - 0.1), ref_dir / f"ref_{t:07.2f}.jpg", 1280)
    _backup(brand)
    sg = brand.path(brand.style_guide)
    doc = sg.read_text() if sg.exists() else "# Brand style guide\n"
    sg.write_text(_replace_section(doc, SAMPLE_START, SAMPLE_END, notes.template_markdown))
    return [f"saved {len(notes.reference_times[:12])} reference frames to {ref_dir}", "updated video template section of style_guide.md"]


def reference_frames(brand: Brand) -> list[Path]:
    d = brand.base_dir / "examples"
    return sorted(d.glob("*/ref_*.jpg")) if d.exists() else []


def _yaml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe", "pdftoppm", "pdftotext"):
        if not shutil.which(tool):
            raise SystemExit(f"missing tool: {tool}")
    run(["ffmpeg", "-v", "error", "-version"])

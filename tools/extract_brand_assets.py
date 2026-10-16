"""Rebuild brand/assets/ locally from your private files (they are never committed).

    python tools/extract_brand_assets.py --guideline 2026_Brand_Guideline.pdf \
        [--sample Training_Sample.mp4 --mascot-time 2 --mascot-box 790,580,1150,995]

- Logo: page 1 of the guideline (dark logo on sage) -> transparent dark + white versions,
  plus a horizontal lockup (mark left of the wordmark) used on title cards and the job aid.
- Mascot (optional): cut out of a frame of the approved sample video. A designer-supplied
  transparent PNG at brand/assets/mascot.png will always look better - use one if you have it.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "brand" / "assets"


def logos(pdf: Path, page: int = 1, box=(730, 410, 1270, 715), mark_box=(935, 415, 1050, 530)) -> None:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", "288", "-png", str(pdf), f"{d}/p"], check=True)
        im = Image.open(next(Path(d).glob("p*.png"))).convert("RGB")
    s = im.width / 2000  # boxes are in a 2000-px-wide page space

    def cut(b, name):
        x0, y0, x1, y1 = [int(v * s) for v in b]
        g = im.crop((x0, y0, x1, y1)).convert("L")
        a = g.point(lambda v: max(0, min(255, int((228 - v) * 255 / (228 - 40)))))
        a = a.crop(a.point(lambda v: 255 if v > 20 else 0).getbbox())
        for col, suffix in [((35, 31, 32), "dark"), ((255, 255, 255), "white")]:
            out = Image.new("RGBA", a.size, col + (0,))
            out.putalpha(a)
            out.save(ASSETS / f"{name}-{suffix}.png")

    cut(box, "logo-stacked")
    cut(mark_box, "logomark")
    for tone in ("dark", "white"):
        st = Image.open(ASSETS / f"logo-stacked-{tone}.png")
        a = st.getchannel("A")
        rows = [a.crop((0, y, st.width, y + 1)).getextrema()[1] for y in range(st.height)]
        gap = next(y for y in range(int(st.height * 0.25), st.height) if rows[y] < 10)
        start = next(y for y in range(gap, st.height) if rows[y] >= 10)
        mark = st.crop((0, 0, st.width, gap))
        mark = mark.crop(mark.getchannel("A").getbbox())
        word = st.crop((0, start, st.width, st.height))
        word = word.crop(word.getchannel("A").getbbox())
        mh = int(word.height * 1.05)
        mark = mark.resize((int(mark.width * mh / mark.height), mh), Image.LANCZOS)
        g = int(word.height * 0.22)
        out = Image.new("RGBA", (mark.width + g + word.width, max(mh, word.height)), (0, 0, 0, 0))
        out.alpha_composite(mark, (0, (out.height - mh) // 2))
        out.alpha_composite(word, (mark.width + g, (out.height - word.height) // 2))
        out.save(ASSETS / f"logo-horizontal-{tone}.png")


def mascot(video: Path, t: float, box: tuple[int, int, int, int]) -> None:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "f.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(f)], check=True)
        im = Image.open(f).convert("RGB").crop(box)
    w, h = im.size
    work = im.copy()
    seeds = [(x, 0) for x in range(0, w, 6)] + [(0, y) for y in range(0, h, 6)] + \
            [(w - 1, y) for y in range(0, h, 6)] + [(x, h - 1) for x in range(0, w, 6)]
    for sd in seeds:
        if work.getpixel(sd) != (255, 0, 255):
            ImageDraw.floodfill(work, sd, (255, 0, 255), thresh=38)
    mask = Image.new("L", (w, h), 0)
    px, m = work.load(), mask.load()
    for y in range(h):
        for x in range(w):
            m[x, y] = 0 if px[x, y] == (255, 0, 255) else 255
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1))
    out = im.convert("RGBA")
    out.putalpha(mask)
    out.crop(mask.getbbox()).save(ASSETS / "mascot.png")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--guideline", type=Path, required=True)
    ap.add_argument("--sample", type=Path)
    ap.add_argument("--mascot-time", type=float, default=2.0)
    ap.add_argument("--mascot-box", default="790,580,1150,995")
    a = ap.parse_args()
    ASSETS.mkdir(parents=True, exist_ok=True)
    logos(a.guideline)
    if a.sample:
        mascot(a.sample, a.mascot_time, tuple(int(v) for v in a.mascot_box.split(",")))
    print("wrote", ", ".join(sorted(p.name for p in ASSETS.glob("*.png"))))


if __name__ == "__main__":
    main()

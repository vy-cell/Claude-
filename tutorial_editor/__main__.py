"""Command line.

    python -m tutorial_editor make RAW.mp4 [--notes sop.md] [--languages en,vi,es]
    python -m tutorial_editor update-guideline NEW_GUIDELINE.pdf
    python -m tutorial_editor learn-example APPROVED.mp4 --name popcorn-chicken
    python -m tutorial_editor render plan.json RAW.mp4 --lang en      # re-render a saved plan, no AI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brand import load_brand


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tutorial_editor", description="On-brand training video agent")
    ap.add_argument("--brand", type=Path, default=Path("brand/brand.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="raw footage -> finished, reviewed videos (+ translations + instruction card)")
    m.add_argument("raw", type=Path, nargs="+")
    m.add_argument("--notes", type=Path, help="SOP / recipe / key facts (markdown or text). Strongly recommended.")
    m.add_argument("--out", type=Path, default=Path("out"))
    m.add_argument("--languages", help="comma list, default from brand.yaml (en,vi,es)")
    m.add_argument("--max-seconds", type=int, default=355, help="hard length cap (default 355 = under 6 min)")
    m.add_argument("--rounds", type=int, default=4, help="max director review rounds")
    m.add_argument("--target-score", type=int, default=90)
    m.add_argument("--voice", choices=["elevenlabs", "command", "preview"], help="override voiceover.provider")
    m.add_argument("--plan", type=Path, help="start from a saved plan.json instead of planning from scratch")
    m.add_argument("--no-card", action="store_true", help="skip the one-page instruction card")
    m.add_argument("--whisper-model", default="small")

    g = sub.add_parser("update-guideline", help="apply a new/updated brand guideline PDF")
    g.add_argument("pdf", type=Path)

    e = sub.add_parser("learn-example", help="learn the video template from an approved finished video")
    e.add_argument("video", type=Path)
    e.add_argument("--name", required=True)

    r = sub.add_parser("render", help="render a saved plan without any AI calls")
    r.add_argument("plan", type=Path)
    r.add_argument("raw", type=Path)
    r.add_argument("--lang", default="en")
    r.add_argument("--out", type=Path, default=Path("out/render.mp4"))
    r.add_argument("--voice", choices=["elevenlabs", "command", "preview"])

    a = ap.parse_args(argv)
    brand = load_brand(a.brand)

    if a.cmd == "make":
        from .pipeline import Options, make

        if a.voice:
            brand.voiceover.provider = a.voice
        langs = a.languages.split(",") if a.languages else [x.code for x in brand.output.languages]
        opt = Options(languages=langs, max_seconds=a.max_seconds, max_rounds=a.rounds, target_score=a.target_score,
                      make_card=not a.no_card, notes=a.notes.read_text() if a.notes else "", plan_file=a.plan,
                      whisper_model=a.whisper_model)
        for raw in a.raw:
            make(raw, brand, a.out, opt)
    elif a.cmd == "update-guideline":
        from .guideline import update_guideline

        for line in update_guideline(brand, a.pdf):
            print("-", line)
    elif a.cmd == "learn-example":
        from .guideline import learn_example

        for line in learn_example(brand, a.video, a.name):
            print("-", line)
    elif a.cmd == "render":
        from .models import VideoPlan
        from .render import Renderer

        if a.voice:
            brand.voiceover.provider = a.voice
        plan = VideoPlan.model_validate_json(a.plan.read_text())
        a.out.parent.mkdir(parents=True, exist_ok=True)
        res = Renderer(brand, a.raw, a.out.parent / "work").render(plan, a.lang, a.out)
        print(f"{res.video} ({res.duration:.0f} s)")
        for w in res.warnings:
            print("warning:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())

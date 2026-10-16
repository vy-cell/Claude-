"""Command line.

    python -m tutorial_editor make RAW.mp4 [--notes sop.md] [--languages en,vi,es]
    python -m tutorial_editor update-guideline NEW_GUIDELINE.pdf
    python -m tutorial_editor learn-example APPROVED.mp4 --name popcorn-chicken
    python -m tutorial_editor render plan.json RAW.mp4 --lang en      # re-render a saved plan, no AI
    python -m tutorial_editor card out/RAW --approved-by "Karina Fong" --approver-title "R&D Specialist"
    python -m tutorial_editor edit-card card.pdf --request "Powder is now 2.45 lb" [--languages vi,es]
    python -m tutorial_editor learn-card approved_card.pdf             # the card format to match
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brand import load_brand


def _info_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("SOP card fields (printed on the card; defaults in brand.yaml card_info)")
    g.add_argument("--version", dest="card_version", help='SOP version, e.g. "1.0"')
    g.add_argument("--effective", help="effective date as printed, e.g. 10/16/2026 (default: today)")
    g.add_argument("--approved-by", help='approver name, e.g. "Karina Fong"')
    g.add_argument("--approver-title", help='e.g. "R&D Specialist"')
    g.add_argument("--video-url", help="training video link for the QR code ({lang} -> en/vi/es)")


def _overrides(a) -> dict:
    return {"version": a.card_version, "effective": a.effective, "approved_by": a.approved_by,
            "approver_title": a.approver_title, "video_url": a.video_url}


def _info(brand, a):
    return brand.card_info.model_copy(update={k: v for k, v in _overrides(a).items() if v is not None})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tutorial_editor", description="On-brand training video agent")
    ap.add_argument("--brand", type=Path, default=Path("brand/brand.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="raw footage -> finished, reviewed videos (+ translations + SOP card)")
    m.add_argument("raw", type=Path, nargs="+")
    m.add_argument("--notes", type=Path, help="SOP / recipe / key facts (markdown or text). Strongly recommended.")
    m.add_argument("--out", type=Path, default=Path("out"))
    m.add_argument("--languages", help="comma list, default from brand.yaml (en,vi,es)")
    m.add_argument("--max-seconds", type=int, default=355, help="hard length cap (default 355 = under 6 min)")
    m.add_argument("--rounds", type=int, default=4, help="max director review rounds")
    m.add_argument("--target-score", type=int, default=90)
    m.add_argument("--voice", choices=["elevenlabs", "command", "preview"], help="override voiceover.provider")
    m.add_argument("--plan", type=Path, help="start from a saved plan.json instead of planning from scratch")
    m.add_argument("--no-card", action="store_true", help="skip the SOP recipe card")
    m.add_argument("--whisper-model", default="small")
    _info_flags(m)

    c = sub.add_parser("card", help="(re)generate the SOP cards for a finished output folder, e.g. after filling in fields")
    c.add_argument("output", type=Path, help="out/<video> folder from `make`")
    c.add_argument("--languages", help="comma list (default: the video's languages)")
    c.add_argument("--rounds", type=int, default=3)
    _info_flags(c)

    ec = sub.add_parser("edit-card", help="edit an existing SOP card PDF from a plain-language request")
    ec.add_argument("pdf", type=Path)
    ec.add_argument("--request", default="", help='what to change, e.g. "step 3: stop at 2.45 lb"')
    ec.add_argument("--out", type=Path, help="output PDF (default: <name>_edited.pdf next to the input)")
    ec.add_argument("--lang", help="language of the card (default: from the card)")
    ec.add_argument("--languages", help="also produce these translations, comma list")
    _info_flags(ec)

    lc = sub.add_parser("learn-card", help="store an approved SOP card as the format reference")
    lc.add_argument("pdf", type=Path)

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
                      whisper_model=a.whisper_model, card_info=_info(brand, a))
        for raw in a.raw:
            make(raw, brand, a.out, opt)
    elif a.cmd == "card":
        from .cardflow import cards_for_output

        langs = a.languages.split(",") if a.languages else None
        for code, pdf in cards_for_output(brand, a.output, _info(brand, a), langs, print, a.rounds).items():
            print(f"{code}: {pdf}")
    elif a.cmd == "edit-card":
        from .cardflow import edit_card_pdf

        out = a.out or a.pdf.with_name(a.pdf.stem + "_edited.pdf")
        langs = a.languages.split(",") if a.languages else []
        for code, pdf in edit_card_pdf(brand, a.pdf, out, a.request, _overrides(a), a.lang, langs, print).items():
            print(f"{code}: {pdf}")
    elif a.cmd == "learn-card":
        import subprocess

        d = brand.base_dir / "examples" / "card"
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("page-*.png"):
            old.unlink()
        subprocess.run(["pdftoppm", "-r", "110", "-png", str(a.pdf), str(d / "page")], check=True)
        print("reference card pages:", ", ".join(p.name for p in sorted(d.glob("page-*.png"))))
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

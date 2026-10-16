"""SOP card workflows.

- after a video is finished: write the card from the final plan + finished-video frames,
  render, director-review, revise; then translate (wording only) for every language.
- `card`: regenerate the cards for an existing output folder, e.g. after filling in the
  approver / date / version, without touching the video.
- `edit-card`: change an existing card PDF from a plain-language request. Cards made here
  carry their own source; other PDFs are imported by Claude, reusing their photos.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .brand import Brand, CardInfo
from .card import (CardRender, PhotoSources, SOPCard, edit_card, import_card, load_embedded, make_card,
                   merge_card_text, render_card, resolve_photos, review_card, translate_card)
from .media import still


@dataclass
class CardJob:
    brand: Brand
    out: Path  # out/<video>/
    stem: str
    plan_json: str
    raw: Path | None
    final_video: Path | None
    scene_times: list[float]
    raw_frames: list
    info: CardInfo
    languages: list[str]
    rounds: int = 3
    translation_rounds: int = 1
    target_score: int = 90


def build_cards(job: CardJob, log) -> dict[str, str]:
    b = job.brand
    src = b.output.source_language
    photo_dir = job.out / "card_photos"
    scene_frames = _scene_frames(job, job.out / "work" / "card_scenes")
    sources = PhotoSources(photo_dir, job.raw, job.final_video, job.scene_times)

    log("## SOP card\nWriting the card from the finished video ...")
    card = make_card(b, job.plan_json, scene_frames, job.raw_frames)
    warns = resolve_photos(card, sources)
    pdf = job.out / src / f"{job.stem}_card_{src}.pdf"
    r = render_card(b, card, job.info, src, photo_dir, pdf)
    for i in range(1, job.rounds + 1):
        rv = review_card(b, card, r.pages, r.warnings + warns, scene_frames, job.raw_frames)
        log(f"[card {src}] round {i}: score **{rv.score}**/100, ready={rv.ready_to_ship}\n" + _bullets(rv.issues + r.warnings))
        if rv.ready_to_ship and rv.score >= job.target_score and not (r.warnings + warns):
            break
        card = rv.card
        warns = resolve_photos(card, sources)
        r = render_card(b, card, job.info, src, photo_dir, pdf)
    (job.out / src / f"card_{src}.json").write_text(card.model_dump_json(indent=2))
    outputs = {src: str(r.pdf)}

    for code in job.languages:
        if code == src:
            continue
        name = b.language(code).name
        tcard = translate_card(b, card, name)
        tr = _render_translated(job, tcard, code, photo_dir, log)
        outputs[code] = str(tr.pdf)
    return outputs


def _render_translated(job: CardJob, tcard: SOPCard, code: str, photo_dir: Path, log) -> CardRender:
    b = job.brand
    pdf = job.out / code / f"{job.stem}_card_{code}.pdf"
    r = render_card(b, tcard, job.info, code, photo_dir, pdf)
    for i in range(1, job.translation_rounds + 1):
        rv = review_card(b, tcard, r.pages, r.warnings, [], [], text_only=True)
        log(f"[card {code}] round {i}: score **{rv.score}**/100, ready={rv.ready_to_ship}\n" + _bullets(rv.issues + r.warnings))
        if rv.ready_to_ship and not r.warnings:
            break
        try:
            tcard = merge_card_text(tcard, rv.card)
        except ValueError as e:
            log(f"[card {code}] ignored a revision that changed the structure ({e})")
            break
        r = render_card(b, tcard, job.info, code, photo_dir, pdf)
    (job.out / code / f"card_{code}.json").write_text(tcard.model_dump_json(indent=2))
    return r


def _scene_frames(job: CardJob, d: Path) -> list[tuple[str, Path]]:
    if not job.final_video or not job.scene_times:
        return []
    d.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(job.scene_times):
        p = d / f"scene_{i:03d}.jpg"
        if not p.exists():
            still(job.final_video, t, p, 960)
        frames.append((f"scene {i}", p))
    return frames


# ---------------------------------------------------------------- regenerate from an output folder


def cards_for_output(brand: Brand, out: Path, info: CardInfo, languages: list[str] | None, log, rounds: int = 3) -> dict[str, str]:
    from .media import sample_frames

    meta = json.loads((out / "render_meta.json").read_text())
    src = brand.output.source_language
    raw = Path(meta["raw"]) if meta.get("raw") and Path(meta["raw"]).exists() else None
    job = CardJob(
        brand=brand, out=out, stem=meta["stem"], plan_json=(out / src / f"plan_{src}.json").read_text(),
        raw=raw, final_video=Path(meta["video"]), scene_times=meta["scene_times"],
        raw_frames=sample_frames(raw, out / "work" / "frames") if raw else [],
        info=info, languages=languages or meta.get("languages", [src]), rounds=rounds,
    )
    return build_cards(job, log)


# ---------------------------------------------------------------- edit an existing card


def edit_card_pdf(brand: Brand, pdf: Path, out_pdf: Path, request: str, overrides: dict, lang: str | None,
                  translate_to: list[str], log) -> dict[str, str]:
    photo_dir = out_pdf.parent / (out_pdf.stem + "_photos")
    loaded = load_embedded(pdf, photo_dir)
    if loaded:
        card, info, card_lang = loaded
        log(f"Loaded the editable source embedded in {pdf.name}.")
    else:
        log(f"{pdf.name} was not made by this tool - reading it with Claude and reusing its photos ...")
        imp = import_card(brand, pdf, photo_dir)
        card, info, card_lang = imp.card, imp.info, lang or brand.output.source_language
    lang = lang or card_lang

    changed = bool(request)
    if request:
        card = edit_card(brand, card, request, sorted(photo_dir.glob("*.jpg")))
        resolve_photos(card, PhotoSources(photo_dir))
    info = info.model_copy(update={k: v for k, v in overrides.items() if v is not None})
    if changed and overrides.get("version") is None:
        info.version = bump_version(info.version)
    if changed and overrides.get("effective") is None:
        info.effective = ""  # today

    r = render_card(brand, card, info, lang, photo_dir, out_pdf)
    if request:
        rv = review_card(brand, card, r.pages, r.warnings, [], [], request=request)
        log(f"[edit] review: score **{rv.score}**/100, ready={rv.ready_to_ship}\n" + _bullets(rv.issues + r.warnings))
        if not (rv.ready_to_ship and not r.warnings):
            card = rv.card
            resolve_photos(card, PhotoSources(photo_dir))
            r = render_card(brand, card, info, lang, photo_dir, out_pdf)
    outputs = {lang: str(r.pdf)}
    for code in translate_to:
        if code == lang:
            continue
        tcard = translate_card(brand, card, brand.language(code).name)
        tpdf = out_pdf.with_name(re.sub(rf"_{lang}$", "", out_pdf.stem) + f"_{code}.pdf")
        outputs[code] = str(render_card(brand, tcard, info, code, photo_dir, tpdf).pdf)
    for w in r.warnings:
        log("warning: " + w)
    return outputs


def bump_version(v: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)", v.strip())
    return f"{m.group(1)}.{int(m.group(2)) + 1}" if m else v


def _bullets(items) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none)"

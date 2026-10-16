"""The autonomous loop: plan -> render -> director review -> revise -> ... -> ship.

No human edits are needed between rounds. Every round is logged to review_log.md so a person
can see what the director changed and why, and each round's plan is kept (plan_r1.json ...).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .brand import Brand
from .card import CardPlan, make_card, render_card, review_card, translate_card
from .guideline import reference_frames
from .media import duration, load_or_transcribe, sample_frames, still, transcript_lines, video_size
from .models import VideoPlan
from .planner import make_plan, merge_text, plan_to_json, review, translate
from .render import Renderer, RenderResult, SceneTiming


@dataclass
class Options:
    languages: list[str]
    max_seconds: int = 355  # hard cap is 6 minutes; keep a margin
    max_rounds: int = 4
    target_score: int = 90
    translation_rounds: int = 2
    card_rounds: int = 3
    make_card: bool = True
    notes: str = ""
    plan_file: Path | None = None
    whisper_model: str = "small"


@dataclass
class Log:
    path: Path
    lines: list[str] = field(default_factory=list)

    def __call__(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {msg}", flush=True)
        self.lines.append(msg)
        self.path.write_text("# Review log\n\n" + "\n\n".join(self.lines) + "\n")


def make(raw: Path, brand: Brand, out_root: Path, opt: Options) -> dict:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.stem)
    out = out_root / stem
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    log = Log(out / "review_log.md")
    src_lang = brand.output.source_language

    log(f"Source: `{raw}` ({duration(raw):.0f} s)")
    frames = sample_frames(raw, work / "frames")
    transcript = load_or_transcribe(raw, work / "transcript.json", opt.whisper_model)
    if transcript is None:
        log("No speech transcript (faster-whisper not installed) - planning from the footage frames and notes only.")
    renderer = Renderer(brand, raw, work)
    refs = reference_frames(brand)

    # ---------------------------------------------------------- 1. plan
    if opt.plan_file:
        plan = VideoPlan.model_validate_json(opt.plan_file.read_text())
        log(f"Starting from existing plan `{opt.plan_file}`")
    else:
        log("Planning the edit ...")
        plan = make_plan(brand, duration=duration(raw), size=video_size(raw), frames=frames,
                         transcript=transcript_lines(transcript) if transcript else "(no transcript)",
                         notes=opt.notes, max_seconds=opt.max_seconds)
    (out / "plan_r0.json").write_text(plan_to_json(plan))

    # ---------------------------------------------------------- 2. improve until it ships
    lang_dir = out / src_lang
    lang_dir.mkdir(exist_ok=True)
    final_video = lang_dir / f"{stem}_{src_lang}.mp4"
    plan, result = improve(brand, renderer, plan, src_lang, final_video, frames, refs, opt, log, out, text_only=False)
    (lang_dir / f"plan_{src_lang}.json").write_text(plan_to_json(plan))
    outputs = {src_lang: {"video": str(result.video), "srt": str(result.srt), "seconds": round(result.duration, 1)}}

    # ---------------------------------------------------------- 3. instruction card
    card = None
    if opt.make_card:
        card = build_card(brand, renderer, plan, frames, refs, opt, log, lang_dir, stem, src_lang)
        outputs[src_lang]["card"] = str(lang_dir / f"{stem}_card_{src_lang}.pdf")

    # ---------------------------------------------------------- 4. translations
    for code in opt.languages:
        if code == src_lang:
            continue
        lang = brand.language(code)
        log(f"## {lang.name}\nTranslating ...")
        tplan = translate(brand, plan, code, lang.name, opt.max_seconds)
        d = out / code
        d.mkdir(exist_ok=True)
        tplan, tres = improve(brand, renderer, tplan, code, d / f"{stem}_{code}.mp4", frames, refs, opt, log, out,
                              text_only=True)
        (d / f"plan_{code}.json").write_text(plan_to_json(tplan))
        outputs[code] = {"video": str(tres.video), "srt": str(tres.srt), "seconds": round(tres.duration, 1)}
        if card is not None:
            tcard = translate_card(brand, card, lang.name, opt.max_seconds)
            png, pdf, warns = render_card(brand, tcard, renderer.stills, d / f"{stem}_card_{code}.png")
            for _ in range(opt.translation_rounds):
                rv = review_card(brand, tplan, tcard, png, warns, frames, refs, opt.max_seconds, text_only=True)
                log(f"{lang.name} card review: score {rv.score}, ready={rv.ready_to_ship}\n" + _bullets(rv.issues))
                if rv.ready_to_ship and not warns:
                    break
                if len(rv.card.steps) == len(tcard.steps):
                    for a, b in zip(rv.card.steps, tcard.steps):
                        a.photo_time = b.photo_time
                tcard = rv.card
                png, pdf, warns = render_card(brand, tcard, renderer.stills, d / f"{stem}_card_{code}.png")
            (d / f"card_{code}.json").write_text(tcard.model_dump_json(indent=2))
            outputs[code]["card"] = str(pdf)

    (out / "outputs.json").write_text(json.dumps(outputs, indent=2, ensure_ascii=False))
    log("## Done\n```json\n" + json.dumps(outputs, indent=2, ensure_ascii=False) + "\n```")
    if plan.open_questions:
        log("Open questions for a person to confirm (the video was still finished):\n" + _bullets(plan.open_questions))
    return outputs


def improve(brand: Brand, renderer: Renderer, plan: VideoPlan, lang: str, video: Path, frames, refs, opt: Options,
            log: Log, out: Path, text_only: bool) -> tuple[VideoPlan, RenderResult]:
    """Render, let the director review the actual output, apply the revised plan, repeat."""
    rounds = opt.translation_rounds if text_only else opt.max_rounds
    best: tuple[int, VideoPlan, RenderResult] | None = None
    result: RenderResult | None = None
    for r in range(1, rounds + 1):
        timings = renderer.time_scenes(plan, lang)
        total = renderer.total(timings)
        over = total > opt.max_seconds
        if over:
            log(f"[{lang}] round {r}: plan runs {total:.0f} s - over the {opt.max_seconds} s cap, tightening before rendering.")
            out_frames: list = []
            report = _report(plan, timings, total, [], None, opt.max_seconds)
        else:
            log(f"[{lang}] round {r}: rendering {len(plan.scenes)} scenes, {total:.0f} s ...")
            result = renderer.render(plan, lang, video, timings)
            out_frames = _settled_frames(result, out / "work" / "review" / f"{lang}_r{r}")
            report = _report(plan, timings, total, result.warnings, _loudness(result.video), opt.max_seconds)
        rv = review(brand, plan, round_no=r, report=report, output_frames=out_frames, reference_frames=refs,
                    raw_frames=frames, duration=total, max_seconds=opt.max_seconds, text_only=text_only)
        high = [i for i in rv.issues if i.severity.lower() == "high"]
        log(f"[{lang}] round {r} director score **{rv.score}**/100, ready={rv.ready_to_ship}, "
            f"{len(rv.issues)} issues ({len(high)} high)\n" + _bullets([f"scene {i.scene}: {i.problem} -> {i.fix}" for i in rv.issues]))
        if not over and result is not None and (best is None or rv.score >= best[0]):
            best = (rv.score, plan, result)
        if not over and rv.ready_to_ship and rv.score >= opt.target_score and not high and not result.warnings:
            log(f"[{lang}] shipped after round {r}.")
            return plan, result
        try:
            # translations: wording only, structure stays identical to the source-language video
            new = merge_text(plan, rv.plan) if text_only else rv.plan
        except ValueError as e:
            log(f"[{lang}] ignored a revision that changed the structure ({e}).")
            break
        if new == plan:
            log(f"[{lang}] director made no further changes.")
            break
        plan = new
        (out / f"plan_{lang}_r{r}.json").write_text(plan_to_json(plan))

    # rounds exhausted: render the last revision if it fits, else keep the best reviewed version
    timings = renderer.time_scenes(plan, lang)
    if renderer.total(timings) <= opt.max_seconds:
        result = renderer.render(plan, lang, video, timings)
        log(f"[{lang}] final render of the last revision ({result.duration:.0f} s).")
        return plan, result
    if best is None:
        raise RuntimeError(f"[{lang}] could not get the video under {opt.max_seconds} s")
    _, plan, _ = best
    result = renderer.render(plan, lang, video, renderer.time_scenes(plan, lang))
    log(f"[{lang}] kept the best reviewed version ({result.duration:.0f} s).")
    return plan, result


def build_card(brand, renderer, plan, frames, refs, opt, log, d: Path, stem: str, lang: str) -> CardPlan:
    log("## Instruction card\nWriting the one-page card ...")
    card = make_card(brand, plan, frames, opt.max_seconds)
    png, pdf, warns = render_card(brand, card, renderer.stills, d / f"{stem}_card_{lang}.png")
    for r in range(1, opt.card_rounds + 1):
        rv = review_card(brand, plan, card, png, warns, frames, refs, opt.max_seconds)
        log(f"card round {r}: score **{rv.score}**/100, ready={rv.ready_to_ship}\n" + _bullets(rv.issues))
        if rv.ready_to_ship and rv.score >= opt.target_score and not warns:
            break
        card = rv.card
        png, pdf, warns = render_card(brand, card, renderer.stills, d / f"{stem}_card_{lang}.png")
    (d / f"card_{lang}.json").write_text(card.model_dump_json(indent=2))
    return card


# ---------------------------------------------------------------- helpers


def _settled_frames(result: RenderResult, d: Path) -> list[tuple[str, Path]]:
    """One frame per scene, late enough that every element has animated in."""
    d.mkdir(parents=True, exist_ok=True)
    frames = []
    for t, seg in zip(result.timings, result.segments):
        p = d / f"scene_{t.index:03d}.jpg"
        still(seg, max(0.0, min(t.duration - 0.25, 2.6)), p, 960)
        frames.append((f"scene {t.index}", p))
    return frames


def _report(plan: VideoPlan, timings: list[SceneTiming], total: float, warnings: list[str], lufs, cap: int) -> str:
    rows = [f"- scene {t.index} {s.kind}: {t.duration:.1f}s (voice {t.vo_dur:.1f}s, footage speed {t.speed:.2f}x)"
            for s, t in zip(plan.scenes, timings)]
    parts = [f"Total length: {total:.1f}s (cap {cap}s).", f"Loudness: {lufs} LUFS." if lufs else "",
             "Per scene:\n" + "\n".join(rows),
             "Layout warnings (text shrunk / truncated):\n" + ("\n".join(warnings) or "(none)")]
    return "\n\n".join(p for p in parts if p)


def _loudness(video: Path) -> str | None:
    err = subprocess.run(["ffmpeg", "-i", str(video), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", err.split("Summary:")[-1])
    return m.group(1) if m else None


def _bullets(items) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none)"

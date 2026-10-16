"""Claude as editor (plan), director (review + revise) and translator."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .ai import ask, contact_sheets, image, text
from .brand import Brand
from .models import SCENE_TEXT_FIELDS, Scene, VideoPlan

TEMPLATE_GUIDE = """\
# The Summerfield training-video template

Audience: a brand-new team member who has never done this task, watching on a phone or a
kitchen screen. After one viewing they must be able to do the job correctly and safely.
Thorough, but nothing wasted: every scene teaches something.

## Scene kinds (each scene = one narrated line + its visuals)
- title: opening card. heading = task name (Caudex), subheading = series label, still_time = an
  appetising/finished shot of the result.
- overview: "N steps" roadmap from `steps` (each step has a thumbnail still_time). Caption sums up the steps.
- requirements: "What You Need". entries: equipment rows have detail "" (title = item + qty),
  ingredient rows have title = ingredient, detail = exact amount with both units.
- checklist: 2-5 tiles (e.g. "Food Safety First", shelf-life rules). entries: kicker = one-word
  action in caps (WASH, SANITIZE, KEEP COLD), title = the rule, detail = short note.
- chapter: "STEP n OF N" divider before each major step; chapter_index = n; heading = step
  name; still_time = a shot from that step.
- footage: real footage of a sub-step. step_code like "1A", section = sub-step name in caps,
  heading = sub-step title ONLY on the first shot of that sub-step ("" on follow-up shots of the
  same sub-step). clip_start/clip_end pick a clean, steady moment that shows exactly what the
  narration says (3-12 s of source; it is sped up to 2x or slowed to 0.8x to fit the line, held
  on the last frame if needed). callouts (0-3) = the critical numbers/rules for this moment:
  tone do = green ("USE / COLD FILTERED WATER"), dont = red ("NEVER USE / TAP WATER"),
  info = light green ("WHISK / 2-3 MIN"), note = tan pill, no label ("SMOOTH · NO LUMPS").
  insets (0-1) = zoom on a small detail the viewer must read (scale display, fill line,
  label) - crop box in normalised source-frame coordinates, tight around the detail.
  pointers (0-2) = tan arrow at the exact spot (normalised source-frame x,y) with a 1-4 word label.
- compare: RIGHT vs WRONG card with two real stills (wrong_time, right_time) and short labels.
  Use it for the mistakes new staff actually make. If the footage has no wrong example, set
  wrong_time -1 and show only the right one.
- closing: celebratory end card (heading = closing word, still_time = finished product).

## Structure (follow this order)
title -> overview -> requirements -> checklist (safety, if relevant) -> for each step:
chapter -> footage sub-steps (1A, 1B ...) with compare cards right after the step they
correct -> storage / shelf-life / "when is it ready" checklist cards if relevant -> closing.

## Writing rules
- voiceover: plain spoken English for a beginner, imperative, short sentences, one idea per
  scene, max ~28 words. Numbers spoken naturally ("two point four pounds"). No filler,
  no hype, warm and calm (brand: warm, inviting, never overly promotional).
- caption: the single key point, max 60 characters, may use CAPS for the critical word,
  "·" to join facts, "—" for contrast ("Use COLD filtered water — NOT tap water").
- On-screen text is short and intentional (brand rule: avoid excessive typography).
- Every number, temperature, time, quantity and rule must come from the notes, the speech
  or something clearly readable in the footage. List each in facts_used with its source.
  Never invent a number; if something needed is unknown, phrase around it and add it to
  open_questions.
- ui: the template words in the plan's language (STEP, OF, WRONG, RIGHT, EQUIPMENT, INGREDIENTS).

## Length
Hard cap: {max_seconds} seconds total. Shorter is better as long as nothing a beginner needs
is lost. Scene length ~= 0.8 s + spoken words / 2.5 (cards at least 3.5 s, footage at least
2.5 s). Merge or drop anything redundant before cutting anything essential.
"""


def _system(brand: Brand, max_seconds: int) -> str:
    return "\n\n".join([
        "You are the senior video editor and trainer for " + brand.name + ". You turn raw kitchen "
        "footage into finished, on-brand training videos that are identical in style every time.",
        TEMPLATE_GUIDE.format(max_seconds=max_seconds),
        "# Brand style guide (from the official brand guideline and the approved sample video)\n" + brand.style_guide_text(),
        "# Brand configuration\n```yaml\n" + brand.as_prompt_yaml() + "```",
    ])


def _footage_blocks(frames: list[tuple[float, Path]], tile_w: int = 640) -> list[dict]:
    sheets = contact_sheets([(f"{t:.1f}s", p) for t, p in frames], cols=3, rows=3, tile_w=tile_w)
    blocks = [text(f"Raw footage: {len(frames)} frames sampled evenly, labelled with their source time, "
                   f"packed {3 * 3} per sheet (read left->right, top->bottom).")]
    blocks += [image(s) for s in sheets]
    return blocks


def make_plan(brand: Brand, *, duration: float, size: tuple[int, int], frames, transcript: str, notes: str,
              max_seconds: int) -> VideoPlan:
    content = [
        text(f"Source video: {duration:.1f} s, {size[0]}x{size[1]}."),
        text("Task notes / SOP from the team (authoritative for facts):\n" + (notes or "(none provided)")),
        text("Speech transcript with source timestamps:\n" + transcript),
        *_footage_blocks(frames),
        text("Plan the complete training video. Use only source times inside the video. "
             "Pick the clearest frames; avoid blurry, blocked or mid-motion moments for stills."),
    ]
    plan = ask(_system(brand, max_seconds), content, VideoPlan, effort="high")
    return sanitize(plan, duration)


class Issue(BaseModel):
    scene: int  # 0-based scene index, -1 for whole video
    severity: str  # high / medium / low
    problem: str
    fix: str


class Review(BaseModel):
    score: int  # 0-100 against the brand guide and the reference sample
    ready_to_ship: bool
    issues: list[Issue]
    plan: VideoPlan  # the full revised plan (unchanged scenes copied as-is)


def review(brand: Brand, plan: VideoPlan, *, round_no: int, report: str, output_frames: list[tuple[str, Path]],
           reference_frames: list[Path], raw_frames, duration: float, max_seconds: int, text_only: bool = False) -> Review:
    content = [
        text(f"Review round {round_no}. You are the director. Judge the rendered video below against the "
             "brand style guide and the approved reference sample, as a picky creative director AND as a new "
             "hire who must learn the task from it. Then return a revised plan that fixes every problem you can."),
        text("Current plan:\n```json\n" + plan.model_dump_json(indent=1) + "\n```"),
        text("Render report:\n" + report),
    ]
    if reference_frames:
        content.append(text("Approved reference sample (this is the look to match):"))
        content += [image(p, 1024) for p in reference_frames[:12]]
    if output_frames:
        content.append(text("Rendered output - one frame per scene after its animations settle, labelled 'scene N':"))
        content += [image(s) for s in contact_sheets(output_frames, cols=2, rows=2, tile_w=960)]
    if raw_frames and not text_only:
        content += _footage_blocks(raw_frames, tile_w=480)
    rules = [
        "Check: teaching clarity for a total beginner (could they do it alone?), correct order, missing or "
        "unclear steps, facts consistent with notes/speech, clip choice actually shows what is said, insets "
        "and pointers land on the right spot, nothing covers the important action, text short and legible, "
        "no shrunk/truncated text, pacing, on-brand look vs the reference, total length.",
        f"Total length must be under {max_seconds} s (currently {duration:.0f} s). Shorter is better if nothing essential is lost.",
        "Set ready_to_ship only when you would publish it as-is. Score honestly; do not inflate.",
    ]
    if text_only:
        rules.append("This is a translated version: change ONLY wording (voiceover, captions, labels, ui, entries, "
                     "callout/pointer/inset text). Keep every scene, time, position and count identical.")
    content.append(text("\n".join(rules)))
    out = ask(_system(brand, max_seconds), content, Review, effort="high")
    out.plan = sanitize(out.plan, None)
    return out


def translate(brand: Brand, plan: VideoPlan, lang_code: str, lang_name: str, max_seconds: int) -> VideoPlan:
    glossary = ", ".join(brand.glossary) or "(none)"
    content = [
        text(f"Translate this training-video plan into {lang_name} ({lang_code}) for kitchen staff whose first "
             f"language is {lang_name}. Natural, simple spoken {lang_name} - not word-for-word. Keep these terms "
             f"exactly as written: {glossary}. Keep numbers and both units (lb and kg, °F and °C). Keep CAPS emphasis "
             f"on the critical word. Captions stay short (aim <= {brand.template.caption_max_chars + 8} chars); "
             "on-screen labels stay short. Return the same plan with every human-readable string translated and "
             "every number, time, position, tone and list length unchanged."),
        text("```json\n" + plan.model_dump_json(indent=1) + "\n```"),
    ]
    out = ask(_system(brand, max_seconds), content, VideoPlan, effort="medium")
    return merge_text(plan, out)


# ---------------------------------------------------------------- safety nets


def sanitize(plan: VideoPlan, src_duration: float | None) -> VideoPlan:
    """Clamp anything that would break rendering; never trust coordinates blindly."""
    def clamp(v, lo, hi):
        return min(max(v, lo), hi)

    end = src_duration if src_duration else 1e9
    for st in plan.steps:
        st.still_time = clamp(st.still_time, 0, max(end - 0.1, 0))
    for s in plan.scenes:
        for f in ("still_time", "wrong_time", "right_time"):
            v = getattr(s, f)
            if v >= 0:
                setattr(s, f, clamp(v, 0, max(end - 0.1, 0)))
        if s.kind == "footage":
            s.clip_start = clamp(s.clip_start, 0, max(end - 0.5, 0))
            s.clip_end = clamp(s.clip_end, s.clip_start + 0.5, end)
        for p in s.pointers:
            p.x, p.y = clamp(p.x, 0.02, 0.98), clamp(p.y, 0.05, 0.95)
        for i in s.insets:
            i.x, i.y = clamp(i.x, 0, 0.95), clamp(i.y, 0, 0.95)
            i.w, i.h = clamp(i.w, 0.05, 1 - i.x), clamp(i.h, 0.05, 1 - i.y)
    return plan


def merge_text(source: VideoPlan, translated: VideoPlan) -> VideoPlan:
    """Take wording from `translated`, structure (times, positions, kinds, counts) from `source`."""
    out = source.model_copy(deep=True)
    out.title, out.series_label, out.ui = translated.title, translated.series_label, translated.ui
    out.facts_used, out.open_questions = translated.facts_used, translated.open_questions
    if len(translated.steps) == len(out.steps):
        for a, b in zip(out.steps, translated.steps):
            a.name = b.name
    if len(translated.scenes) != len(out.scenes):
        raise ValueError("translation changed the number of scenes")
    for a, b in zip(out.scenes, translated.scenes):
        _merge_scene(a, b)
    return out


def _merge_scene(a: Scene, b: Scene) -> None:
    for f in SCENE_TEXT_FIELDS:
        setattr(a, f, getattr(b, f))
    for lst, fields in (("callouts", ("label", "value")), ("pointers", ("text",)), ("insets", ("label",)),
                        ("entries", ("kicker", "title", "detail"))):
        xs, ys = getattr(a, lst), getattr(b, lst)
        if len(xs) == len(ys):
            for x, y in zip(xs, ys):
                for f in fields:
                    setattr(x, f, getattr(y, f))


def plan_to_json(plan: VideoPlan) -> str:
    return json.dumps(plan.model_dump(), indent=2, ensure_ascii=False)

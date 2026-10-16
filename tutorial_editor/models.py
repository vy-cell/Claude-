"""Data shapes shared across the pipeline.

`VideoPlan` is what Claude produces from the raw footage. It is plain JSON on disk
(plan.json), so a person can review or tweak it before rendering.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Word(BaseModel):
    text: str
    start: float
    end: float


class Transcript(BaseModel):
    words: list[Word]
    duration: float
    language: str | None = None


# ---- Claude's structured output -------------------------------------------------

SceneKind = Literal["title", "overview", "requirements", "checklist", "chapter", "footage", "compare", "closing"]


class Callout(BaseModel):
    """Stacked box in the top-right of a footage scene."""

    label: str  # small line, e.g. "USE", "NEVER USE", "FRIDGE AT"
    value: str  # big line, e.g. "COLD FILTERED WATER"
    tone: Literal["do", "dont", "info", "note"]  # green / red / light green / tan pill


class Pointer(BaseModel):
    """Tan arrow pointing at something visible in the frame."""

    text: str
    x: float  # target point, 0-1 of the output frame width
    y: float  # target point, 0-1 of the output frame height
    side: Literal["left", "right"]  # where the label sits relative to the target


class Inset(BaseModel):
    """Picture-in-picture zoom on a region of the same footage (e.g. the scale display)."""

    x: float  # crop box in the source frame, 0-1
    y: float
    w: float
    h: float
    position: Literal["left", "right"]
    label: str  # small tan pill under the inset; "" for none


class Entry(BaseModel):
    kicker: str  # small caps label ("WASH", "EQUIPMENT", "INGREDIENT")
    title: str  # "Hands 20 sec" / "Chicken thigh"
    detail: str  # "then gloves on" / "40 lb (8 bags x 5 lb)"


class Step(BaseModel):
    name: str  # "Make marinade"
    still_time: float  # source second used as the thumbnail


class Scene(BaseModel):
    kind: SceneKind
    voiceover: str  # what the narrator says during this scene
    caption: str  # short key-point caption in the bottom pill
    heading: str  # card heading, or footage title tag ("Measure the water")
    subheading: str  # small caps line under a card heading ("BEFORE YOU START")
    section: str  # small caps eyebrow, e.g. "FOOD SAFETY" or footage "MEASURE THE WATER"
    step_code: str  # "1A" on footage, "" otherwise
    chapter_index: int  # 1-based step for chapter cards / progress, 0 when n/a
    clip_start: float  # footage: source in-point (s); other kinds: -1
    clip_end: float  # footage: source out-point (s); other kinds: -1
    still_time: float  # source second for card imagery (title/chapter/closing); -1 for none
    callouts: list[Callout]
    pointers: list[Pointer]
    insets: list[Inset]
    entries: list[Entry]  # requirements / checklist content
    wrong_time: float  # compare: source second of the WRONG example; -1 otherwise
    wrong_label: str
    right_time: float  # compare: source second of the RIGHT example; -1 otherwise
    right_label: str


class UiText(BaseModel):
    """Template words, translated with the rest of the plan."""

    step: str  # "STEP"
    of: str  # "OF"
    wrong: str  # "WRONG"
    right: str  # "RIGHT"
    equipment: str  # "EQUIPMENT"
    ingredients: str  # "INGREDIENTS"


class VideoPlan(BaseModel):
    title: str
    series_label: str
    ui: UiText
    steps: list[Step]
    scenes: list[Scene]
    facts_used: list[str]  # every number / rule stated, with its source (notes, speech, visible)
    open_questions: list[str]  # anything a person should confirm before publishing


# Text fields that are translated; everything else (times, positions, kinds) is copied from the source plan.
SCENE_TEXT_FIELDS = ("voiceover", "caption", "heading", "subheading", "section", "wrong_label", "right_label")

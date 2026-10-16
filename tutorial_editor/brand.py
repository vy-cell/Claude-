"""Brand kit: every visual/audio/editing rule that must be identical across videos.

All values live in brand/brand.yaml. `update-guideline` rewrites the colour and font
tokens from a new guideline PDF, so the template below never hard-codes a colour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Colors(BaseModel):
    card_bg: str = "#E3E8E3"  # PANTONE 9042 C - card / slide background
    panel_bg: str = "#D5E1CF"  # title-card photo panel
    text: str = "#231F20"  # PANTONE Black 7 C
    muted_text: str = "#80867B"
    primary: str = "#5E7A56"  # deep sage: step badges, "do" callouts, active progress pill
    primary_light: str = "#8E9F87"  # info callouts
    stripe_a: str = "#B6CFAE"  # PANTONE 2260 C
    stripe_b: str = "#DBE3D7"
    caption_bg: str = "#93817E"
    badge_bg: str = "#A39693"
    accent: str = "#CDA077"  # PANTONE 728 C - pointers / arrows
    accent_light: str = "#D2B196"  # note pills
    danger: str = "#AA4E40"  # "never" callouts, WRONG frames
    surface: str = "#FFFFFF"
    # SOP card: step-number badge colour per phase of the task (cycled)
    phase_a: str = "#56714F"  # deep sage
    phase_b: str = "#94683E"  # caramel brown
    phase_c: str = "#3F6870"  # slate teal
    phase_d: str = "#8B5B2D"  # brown
    tone_green: str = "#6E8C64"  # timeline dots / kickers
    tone_caramel: str = "#CDA077"
    tone_caramel_text: str = "#8B5B2D"
    tone_blue: str = "#7FA3A9"
    tone_blue_text: str = "#5F8189"
    never_ring: str = "#D9A497"
    chip_bg: str = "#F0F2EF"


class Fonts(BaseModel):
    heading: str = "fonts/Caudex-Regular.ttf"  # Caudex - titles, headlines
    body: str = "fonts/WorkSans-VF.ttf"  # Work Sans - labels, captions, callouts
    heading_fallback: str = "fonts/NotoSerif-VF.ttf"  # used when a language needs glyphs the heading font lacks
    body_fallback: str = "fonts/WorkSans-VF.ttf"
    accent: str = "fonts/Yellowtail-Regular.ttf"  # script accents on the SOP card (stand-in for Cote Lumiere)


class Assets(BaseModel):
    logo_horizontal: str = "assets/logo-horizontal-dark.png"
    logo_mark: str = "assets/logomark-dark.png"
    logo_mark_light: str = "assets/logomark-white.png"
    mascot: str | None = "assets/mascot.png"


class CardInfo(BaseModel):
    """Fill-in fields printed on the SOP card footer / QR. Defaults here, override per run."""

    version: str = "1.0"
    effective: str = ""  # empty -> today's date
    approved_by: str = ""
    approver_title: str = ""
    video_url: str = ""  # QR code target; "{lang}" is replaced with the language code
    date_format: str = "%m/%d/%Y"


class Template(BaseModel):
    series_label: str = "BACK OF HOUSE TRAINING"
    badge_text: str = "CONFIDENTIAL MATERIAL"  # "" hides the badge
    closing_word: str = "YAY"
    caption_max_chars: int = 60
    callout_max: int = 3


class Timing(BaseModel):
    lead_in: float = 0.35  # silence before each voiceover line
    tail: float = 0.45  # breath after each line
    min_scene: float = 2.5
    min_card: float = 3.5
    footage_speed_range: tuple[float, float] = (0.8, 2.0)


class LanguageVoice(BaseModel):
    code: str
    name: str
    voice_id: str = ""  # empty -> use voiceover.voice_id


class Voiceover(BaseModel):
    # elevenlabs: real voiceover. preview: timed silence (check the cut without TTS cost).
    # command: any local TTS, e.g. "piper --model x.onnx --output_file {out_file} < {text_file}"
    provider: Literal["elevenlabs", "command", "preview"] = "elevenlabs"
    voice_id: str = ""
    model_id: str = "eleven_multilingual_v2"
    stability: float = 0.55
    similarity_boost: float = 0.75
    speed: float = 1.0
    command: str | None = None
    keep_source_audio: bool = False  # mix the raw camera audio under the voiceover
    source_audio_db: float = -24


class Audio(BaseModel):
    target_lufs: float = -16
    true_peak: float = -1.5
    music_file: str | None = None
    music_db: float = -26


class Output(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 18
    source_language: str = "en"
    languages: list[LanguageVoice] = Field(
        default_factory=lambda: [
            LanguageVoice(code="en", name="English"),
            LanguageVoice(code="vi", name="Vietnamese"),
            LanguageVoice(code="es", name="Spanish"),
        ]
    )


class Brand(BaseModel):
    name: str = "Summerfield Tea Bar"
    voice_and_tone: str = ""
    glossary: list[str] = Field(default_factory=list)  # never translated, always spelled this way
    banned_words: list[str] = Field(default_factory=list)
    style_guide: str = "style_guide.md"
    colors: Colors = Field(default_factory=Colors)
    fonts: Fonts = Field(default_factory=Fonts)
    assets: Assets = Field(default_factory=Assets)
    template: Template = Field(default_factory=Template)
    card_info: CardInfo = Field(default_factory=CardInfo)
    timing: Timing = Field(default_factory=Timing)
    voiceover: Voiceover = Field(default_factory=Voiceover)
    audio: Audio = Field(default_factory=Audio)
    output: Output = Field(default_factory=Output)

    base_dir: Path = Path(".")
    source_file: Path | None = None

    def path(self, p: str | None) -> Path | None:
        if not p:
            return None
        q = Path(p)
        return q if q.is_absolute() else (self.base_dir / q)

    def style_guide_text(self) -> str:
        p = self.path(self.style_guide)
        return p.read_text() if p and p.exists() else ""

    def language(self, code: str) -> LanguageVoice:
        for lang in self.output.languages:
            if lang.code == code:
                return lang
        return LanguageVoice(code=code, name=code)

    def as_prompt_yaml(self) -> str:
        data = self.model_dump(mode="json", exclude={"base_dir", "source_file", "voiceover", "output"})
        return yaml.safe_dump(data, sort_keys=True, allow_unicode=True)


def load_brand(path: str | Path) -> Brand:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    brand = Brand.model_validate(data)
    brand.base_dir = path.parent.resolve()
    brand.source_file = path.resolve()
    return brand


def hex_rgb(h: str, alpha: int | None = None) -> tuple[int, ...]:
    h = h.lstrip("#")
    rgb = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    return rgb if alpha is None else (*rgb, alpha)

# Summerfield training-video agent

Raw kitchen footage in → finished, on-brand training videos out, in **English, Vietnamese and
Spanish**, plus a **one-page step-by-step instruction card** with real photos — edited the same
way every time, with no manual editing.

```
python -m tutorial_editor make raw_footage.mp4 --notes popcorn_chicken_notes.md
```

## What it does

1. **Watches the footage.** Samples frames across the whole clip (and transcribes any speech
   locally with faster-whisper, if installed).
2. **Plans the edit** (Claude). It cuts and orders the video into the approved template:
   - opening scenes: title → overview → What You Need → Food Safety
   - for each step: a "STEP n OF N" chapter card, then footage scenes with on-screen labels,
     callouts, zoom insets and pointer arrows, then Right vs Wrong cards
   - storage rules → closing card

   It writes a narration line and a key-point caption for every scene. It is written for
   someone who has never done the task, and it stays **under 6 minutes** — shorter whenever
   nothing essential is lost. Every number comes from your notes, the speech or what is
   visible, and is logged in `facts_used`.
3. **Renders** with ffmpeg in the Summerfield look:
   - Caudex/Work Sans type and guideline colours, with stripes, the badge, the logo and the mascot
   - every element eases in one after another with soft shadows and rounded corners
   - photos get a slow push-in and scenes cross-dissolve
   - an ElevenLabs voiceover in one consistent voice, loudness-normalised
4. **Director loop — improves itself until it's ready.** Claude watches a frame from every
   rendered scene and compares it against the brand guide and the approved sample. It scores
   the video, lists the problems and returns a revised plan. Only the changed scenes are
   re-rendered. This repeats until it would ship it (score ≥ 90, no high-severity issues, no
   squeezed text, under the length cap), or until four rounds have run. Every round is written
   to `review_log.md`.
5. **Translates** to Vietnamese and Spanish. The voiceover, captions and every on-screen label
   are translated; the cuts and timing structure stay identical. Brand terms are never
   translated, and Caudex falls back to Noto Serif for Vietnamese letters it lacks. Each
   language gets its own director pass.
6. **Instruction card** — one printable page (Letter landscape, PNG + PDF) in every language:
   - every step with a real, clean, mid-action photo cropped to the action
   - rings and arrows pointing at the exact measurement (scale readout, fill line, label spot)
   - short instructions, the key number with an icon, and the one mistake to avoid
   - food-safety and storage rules in the footer
   - reviewed and revised until ready

## Outputs

```
out/<video>/
  en/<video>_en.mp4  <video>_en.srt  <video>_card_en.pdf/.png  plan_en.json  card_en.json
  vi/...             es/...
  review_log.md      # what the director found and changed, round by round
  outputs.json
```

## Setup

```bash
pip install -r requirements.txt          # plus ffmpeg and poppler-utils
export ANTHROPIC_API_KEY=...             # Claude
export ELEVENLABS_API_KEY=...            # voiceover; set voiceover.voice_id in brand/brand.yaml
python tools/extract_brand_assets.py --guideline 2026_Brand_Guideline.pdf --sample Approved_Training.mp4
```

The last command rebuilds the logo, horizontal lockup and mascot into `brand/assets/` on your
machine. **This repository is public**, so brand assets, footage, guideline PDFs and reference
frames are git-ignored and never committed.

Try an edit without paying for voiceover: add `--voice preview` (silent, correctly timed).

## Keeping every video consistent

| You have | Run |
|---|---|
| An updated brand guideline | `python -m tutorial_editor update-guideline new_guideline.pdf` → updates colours/fonts in `brand/brand.yaml` and the guideline section of `brand/style_guide.md` (old versions saved in `brand/history/`) |
| A newly approved finished video | `python -m tutorial_editor learn-example approved.mp4 --name popcorn-chicken` → saves reference frames the director compares against, and updates the template section of the style guide |
| A plan you want to re-render as-is | `python -m tutorial_editor render out/x/en/plan_en.json raw.mp4 --lang en` |

Everything about the look lives in `brand/brand.yaml` (colours, fonts, badge text, timing,
voice, languages) and `brand/style_guide.md` (rules the AI follows).

## Tips for the best result

- **Give it notes** (`brand/notes_template.md`): amounts, temperatures, times, common mistakes.
  It won't invent numbers, so missing facts end up in "open questions" instead.
- Film steady, well-lit, landscape footage; show each readout (scale, thermometer, fill line)
  clearly for a couple of seconds. The agent picks the clips, zooms and pointer spots itself.
- Film the common mistakes too, so it can build the Right vs Wrong cards from real shots.

## Using it from Claude Code

`.claude/agents/video-editor.md` defines a `video-editor` subagent, so you can say *"make the
training video from raw/boba_prep.mp4 with these notes"* and it runs the whole pipeline.

## Development

```
python -m pytest -q      # renders a synthetic video end to end; Claude is mocked
```

Fonts: Caudex, Work Sans and Noto Serif (SIL Open Font License, see `brand/fonts/OFL-*.txt`).
Côte Lumière is commercial and not included; the template doesn't require it.

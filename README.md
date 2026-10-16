# Summerfield Studio

The Summerfield training-video agent.

Raw kitchen footage in → finished, on-brand training videos out, in **English, Vietnamese and
Spanish**, plus the **2-page SOP recipe card** with real step photos — edited the same
way every time, with no manual editing.

```
python -m tutorial_editor make raw_footage.mp4 --notes popcorn_chicken_notes.md
```

## The website and phone app

Everything below also runs as a website your team signs into, and it installs on phones and
tablets like an app (Add to Home Screen on iPhone; Install app on Android/Chrome).

| Page | What people do there |
|---|---|
| **Videos** | Library of every training video with its status |
| **New** | Upload raw footage and recipe notes (or an SOP PDF), choose languages and voice, fill in the card details, press *Make the video*. Live progress shows each stage and the director's score |
| **Video page** | Watch each language, download the video, subtitles and SOP card. Change the card details instantly, ask for a change in plain words, or re-render with the brand voice |
| **Cards** | Upload any SOP card PDF (made by Studio or not), say what to change, get the updated card in every language |
| **Brand** (admins) | Upload a new guideline, an approved example video or an approved card; replace the logo or mascot; set the voice and card defaults |
| **Team** (admins) | Add or remove people; editor vs admin roles |

**Run it on one computer**

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=... ELEVENLABS_API_KEY=...
uvicorn webapp.server:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. The first visit creates the admin account; then upload the brand
guideline and sample video on the **Brand** page to build the logo and mascot.

**Host it for the whole team**

The repo includes a `Dockerfile` with ffmpeg, Chromium and the PDF tools built in.

```bash
cp .env.example .env        # add the API keys
docker compose up -d        # http://<server>:8000
```

- **Where:** any host that runs a Docker container with a persistent disk: Render, Railway,
  Fly.io, DigitalOcean, AWS Lightsail or a small office server.
- **Size:** about 4 CPU cores, 8 GB RAM and 50+ GB of disk for videos. No GPU needed.
- **HTTPS:** put it behind HTTPS (most hosts do this for you) and set `STUDIO_SECURE_COOKIES=1`.
- **Backups:** everything (videos, cards, brand kit, accounts) lives in the `data/` volume.

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
6. **SOP recipe card** — made automatically once the video is finished, in the approved
   2-page format (front: title, batch, tools, steps 1–6, QR to the video; back: batch flow,
   remaining steps, storage timeline, "Never" row, footer). Step photos are frames from the
   finished video, so they already show the zoom insets and arrows on the measurement. Claude
   writes it from the final video plan, a reviewer checks the printed pages against the
   approved card (overflow is measured, not guessed) and it is revised until ready, then
   translated. PDF + page PNGs per language.

## Outputs

```
out/<video>/
  en/<video>_en.mp4  <video>_en.srt  <video>_card_en.pdf (+ page PNGs)  plan_en.json  card_en.json
  vi/...             es/...
  review_log.md      # what the director found and changed, round by round
  outputs.json
```

## Setup

```bash
pip install -r requirements.txt          # plus ffmpeg, poppler-utils and Chromium/Chrome (set CHROME_PATH if not on PATH)
export ANTHROPIC_API_KEY=...             # Claude
export ELEVENLABS_API_KEY=...            # voiceover; set voiceover.voice_id in brand/brand.yaml
python tools/extract_brand_assets.py --guideline 2026_Brand_Guideline.pdf --sample Approved_Training.mp4
```

The last command rebuilds the logo, horizontal lockup and mascot into `brand/assets/` on your
machine. **This repository is public**, so brand assets, footage, guideline PDFs and reference
frames are git-ignored and never committed.

Try an edit without paying for voiceover: add `--voice preview` (silent, correctly timed).

## SOP card: fill-in fields and edits

The footer and QR code come from fields you fill in. Defaults live in `brand/brand.yaml`
(`card_info`); override them on any run:

```
python -m tutorial_editor make raw.mp4 --notes notes.md \
    --approved-by "Karina Fong" --approver-title "R&D Specialist" --version 1.0 \
    --effective 10/16/2026 --video-url "https://…/popcorn-chicken-{lang}"
```

| You want to | Run |
|---|---|
| Fill in / change the fields after the video is done (no re-render) | `python -m tutorial_editor card out/<video> --approved-by "…" --effective 11/01/2026` |
| Change only the fields on an existing card PDF (no AI) | `python -m tutorial_editor edit-card card.pdf --approved-by "…" --version 1.1` |
| Change the content of a card | `python -m tutorial_editor edit-card card.pdf --request "Step 3: stop at 2.45 lb; add 'stir every 30 sec'" --languages vi,es` |
| Set the card format to match | `python -m tutorial_editor learn-card approved_card.pdf` |

Every card PDF made here carries its own editable source (text, photos, fields), so edits are
exact. A card made elsewhere (e.g. your current SOP PDF) can also be uploaded: Claude reads it,
reuses its photos and rebuilds it in the template. Content edits bump the version (1.0 → 1.1)
and set the effective date to today unless you pass them.

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

## Using Summerfield Studio from Claude Code

Open this repository in Claude Code and ask by name:

- *"Summerfield Studio, make the training video from raw/boba_prep.mp4 with these notes"*
- *"Summerfield Studio, update the card: approved by Jamie Tran, Kitchen Manager"*
- *"Summerfield Studio, edit this card: step 3 now stops at 2.45 lb"*

The agent is defined in `.claude/agents/summerfield-studio.md`; you can also pick
`summerfield-studio` from the `/agents` list.

## Development

```
python -m pytest -q      # renders a synthetic video end to end; Claude is mocked
```

Fonts: Caudex, Work Sans and Noto Serif (SIL Open Font License, `brand/fonts/OFL-*.txt`) and
Yellowtail (Apache 2.0) as the script accent on the card. Côte Lumière is commercial and not
included - drop its file in `brand/fonts/` and point `fonts.accent` at it to use it.

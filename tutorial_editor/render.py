"""Scene -> segment -> finished video, all with ffmpeg.

Every scene is rendered to its own cached segment; the improvement loop only
re-renders scenes whose content changed. Segments are joined with soft cross
dissolves and the audio is loudness-normalised to the brand target.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .brand import Brand
from .graphics import H, W, Kit, Layer, SceneGraphic, StillProvider, build_scene, cover
from .media import has_audio, run, video_size
from .models import Scene, VideoPlan
from .voiceover import synthesize

XFADE = 0.35  # cross-dissolve between scenes
ANIM = 0.55  # entrance animation length
SLIDE = 36  # px an element travels while it eases in


@dataclass
class SceneTiming:
    index: int
    vo_path: Path | None
    vo_dur: float
    duration: float
    speed: float = 1.0


@dataclass
class RenderResult:
    video: Path
    srt: Path
    duration: float
    timings: list[SceneTiming]
    warnings: list[str]
    segments: list[Path]


class Renderer:
    def __init__(self, brand: Brand, source: Path, work: Path):
        self.b = brand
        self.src = source
        self.work = work
        self.src_w, self.src_h = video_size(source)
        self.src_audio = has_audio(source)
        self.stills = StillProvider(source, work / "stills")
        self.kit = Kit(brand, self.stills)
        self.fit_mode = "cover" if self.src_w / self.src_h >= 1.3 else "blur"
        brand_files = [brand.source_file] if brand.source_file else []
        self.brand_hash = hashlib.sha256(
            (brand.model_dump_json() + "".join(str(p.stat().st_mtime) for p in brand_files if p.exists())).encode()
        ).hexdigest()[:12]

    # ------------------------------------------------------------ geometry

    def map_point(self, nx: float, ny: float) -> tuple[int, int]:
        """Normalised source-frame point -> design-grid pixel, through the same fit used for the footage."""
        sw, sh = self.src_w, self.src_h
        s = max(W / sw, H / sh) if self.fit_mode == "cover" else min(W / sw, H / sh)
        ox, oy = (W - sw * s) / 2, (H - sh * s) / 2
        return int(ox + nx * sw * s), int(oy + ny * sh * s)

    # ------------------------------------------------------------ timing

    def time_scenes(self, plan: VideoPlan, lang: str) -> list[SceneTiming]:
        t = self.b.timing
        out = []
        for i, s in enumerate(plan.scenes):
            vo, vd = synthesize(self.b, s.voiceover, lang, self.work / "tts")
            floor = t.min_scene if s.kind == "footage" else t.min_card
            dur = max(floor, t.lead_in + vd + t.tail)
            speed = 1.0
            if s.kind == "footage":
                length = max(0.2, s.clip_end - s.clip_start)
                lo, hi = t.footage_speed_range
                speed = min(max(length / dur, lo), hi)
            out.append(SceneTiming(i, vo, vd, round(dur, 3), speed))
        return out

    @staticmethod
    def total(timings: list[SceneTiming]) -> float:
        return sum(x.duration for x in timings) - XFADE * max(len(timings) - 1, 0)

    # ------------------------------------------------------------ scenes

    def render_scene(self, plan: VideoPlan, scene: Scene, timing: SceneTiming, lang: str) -> tuple[Path, list[str]]:
        key_src = json.dumps({
            "scene": scene.model_dump(), "steps": [s.model_dump() for s in plan.steps], "ui": plan.ui.model_dump(),
            "title": plan.title, "series": plan.series_label, "dur": timing.duration, "speed": timing.speed,
            "vo": str(timing.vo_path), "brand": self.brand_hash, "lang": lang, "v": 3,
        }, sort_keys=True)
        key = hashlib.sha256(key_src.encode()).hexdigest()[:16]
        seg_dir = self.work / "segments"
        seg_dir.mkdir(parents=True, exist_ok=True)
        out = seg_dir / f"{lang}_{timing.index:03d}_{key}.mp4"
        warn_file = out.with_suffix(".warnings.json")
        if out.exists() and warn_file.exists():
            return out, json.loads(warn_file.read_text())

        g = build_scene(self.kit, plan, scene, self.src_w / self.src_h, self.map_point)
        layer_dir = self.work / "layers" / out.stem
        layer_dir.mkdir(parents=True, exist_ok=True)
        cmd, fc, vlabel = self._base(scene, g, timing, layer_dir)
        inset_labels = []
        insets = [layer for layer in g.layers if layer.kind == "inset"]
        if insets and scene.kind == "footage":
            # the base chain ended in a split; peel off one branch per inset
            inset_labels = [f"ins{i}" for i in range(len(insets))]
        overlay_idx = 0
        for layer in g.layers:
            label = f"L{overlay_idx}"
            if layer.kind == "image":
                p = layer_dir / f"{overlay_idx:02d}_{layer.name}.png"
                layer.img.save(p)
                cmd += _loop_input(p, timing.duration)
                idx = _count_inputs(cmd) - 1
                fc.append(f"[{idx}:v]format=rgba{_fade(layer)}[{label}]")
            elif layer.kind == "photo":
                p = layer_dir / f"{overlay_idx:02d}_{layer.name}.png"
                cover(self.stills.get(layer.still_time), (layer.w, layer.h)).save(p)
                m = _mask(layer_dir / f"{overlay_idx:02d}_{layer.name}_mask.png", layer.w, layer.h, layer.radius)
                cmd += _loop_input(p, timing.duration)
                pi = _count_inputs(cmd) - 1
                cmd += _loop_input(m, timing.duration)
                mi = _count_inputs(cmd) - 1
                frames = max(int(timing.duration * self.b.output.fps), 1)
                fc.append(
                    f"[{pi}:v]scale={layer.w * 3}:{layer.h * 3},"
                    f"zoompan=z='1+0.06*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={layer.w}x{layer.h}:fps={self.b.output.fps},"
                    f"format=rgba[{label}p];[{mi}:v]format=gray,scale={layer.w}:{layer.h}[{label}m];"
                    f"[{label}p][{label}m]alphamerge{_fade(layer)}[{label}]"
                )
            else:  # inset: live crop of the same footage
                src_label = inset_labels.pop(0)
                m = _mask(layer_dir / f"{overlay_idx:02d}_{layer.name}_mask.png", layer.w, layer.h, layer.radius)
                cmd += _loop_input(m, timing.duration)
                mi = _count_inputs(cmd) - 1
                cx, cy, cw, ch = layer.crop
                fc.append(
                    f"[{src_label}]crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{cx:.4f}:ih*{cy:.4f},scale={layer.w}:{layer.h},setsar=1,format=rgba[{label}p];"
                    f"[{mi}:v]format=gray,scale={layer.w}:{layer.h}[{label}m];[{label}p][{label}m]alphamerge{_fade(layer)}[{label}]"
                )
            nxt = f"V{overlay_idx}"
            fc.append(f"[{vlabel}][{label}]overlay=x='{_ease(layer, 'x')}':y='{_ease(layer, 'y')}':eval=frame:shortest=0[{nxt}]")
            vlabel = nxt
            overlay_idx += 1
        fc.append(f"[{vlabel}]trim=duration={timing.duration},setpts=PTS-STARTPTS,format=yuv420p[vout]")

        # audio: voiceover after a short lead-in, optional low camera audio underneath
        lead_ms = int(self.b.timing.lead_in * 1000)
        if timing.vo_path:
            cmd += ["-i", str(timing.vo_path)]
            ai = _count_inputs(cmd) - 1
            fc.append(f"[{ai}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={lead_ms}:all=1,apad,atrim=0:{timing.duration}[vo]")
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{timing.duration}[vo]")
        if scene.kind == "footage" and self.src_audio and self.b.voiceover.keep_source_audio and 0.5 <= timing.speed <= 2.0:
            fc.append(f"[0:a]atempo={timing.speed:.4f},volume={self.b.voiceover.source_audio_db}dB,"
                      f"aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{timing.duration}[cam];"
                      f"[vo][cam]amix=inputs=2:normalize=0:duration=first[aout]")
        else:
            fc.append("[vo]anull[aout]")

        cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
                "-t", f"{timing.duration}", "-r", str(self.b.output.fps),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
        run(cmd)
        warn_file.write_text(json.dumps(g.warnings))
        return out, g.warnings

    def _base(self, scene: Scene, g: SceneGraphic, timing: SceneTiming, layer_dir: Path):
        fps = self.b.output.fps
        d = timing.duration
        if scene.kind == "footage":
            length = max(0.2, scene.clip_end - scene.clip_start)
            cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(scene.clip_start, 0):.3f}", "-t", f"{length:.3f}", "-i", str(self.src)]
            n_ins = sum(1 for layer in g.layers if layer.kind == "inset")
            head = (f"[0:v]setpts=(PTS-STARTPTS)/{timing.speed:.4f},fps={fps},"
                    f"tpad=stop_mode=clone:stop_duration={d + 1:.2f},trim=duration={d + 0.5:.2f},setpts=PTS-STARTPTS")
            fc = []
            if n_ins:
                fc.append(head + f",split={n_ins + 1}[raw]" + "".join(f"[ins{i}]" for i in range(n_ins)))
            else:
                fc.append(head + "[raw]")
            if self.fit_mode == "cover":
                fc.append(f"[raw]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base]")
            else:
                fc.append(f"[raw]split[r1][r2];[r1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                          f"gblur=sigma=40,eq=brightness=-0.05[bg];[r2]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fg];"
                          f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
            return cmd, fc, "base"
        bg = layer_dir / "background.png"
        g.background.save(bg)
        cmd = ["ffmpeg", "-v", "error", "-y", *_loop_input(bg, d)]
        # very slow push-in on the whole card so a held slide never looks frozen
        frames = max(int(d * fps), 1)
        fc = [f"[0:v]scale={W * 2}:{H * 2},zoompan=z='1+0.015*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={fps},setsar=1[base]"]
        return cmd, fc, "base"

    # ------------------------------------------------------------ whole video

    def render(self, plan: VideoPlan, lang: str, out_video: Path, timings: list[SceneTiming] | None = None) -> RenderResult:
        timings = timings or self.time_scenes(plan, lang)
        segments, warnings = [], []
        for s, t in zip(plan.scenes, timings):
            seg, w = self.render_scene(plan, s, t, lang)
            segments.append(seg)
            warnings += [f"scene {t.index} ({s.kind}): {x}" for x in w]
        joined = out_video.with_name(out_video.stem + ".joined.mp4")
        self._xfade_join(segments, [t.duration for t in timings], joined)
        a = self.b.audio
        ow, oh = self.b.output.width, self.b.output.height
        vf = ["-vf", f"scale={ow}:{oh}:flags=lanczos"] if (ow, oh) != (W, H) else []
        run(["ffmpeg", "-v", "error", "-y", "-i", str(joined), *vf,
             "-af", f"loudnorm=I={a.target_lufs}:TP={a.true_peak}:LRA=11,aresample=48000",
             "-c:v", "libx264" if vf else "copy", *(["-crf", str(self.b.output.crf), "-pix_fmt", "yuv420p"] if vf else []),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_video)])
        joined.unlink(missing_ok=True)
        srt = out_video.with_suffix(".srt")
        write_srt(plan, timings, self.b.timing.lead_in, srt)
        return RenderResult(out_video, srt, self.total(timings), timings, warnings, segments)

    def _xfade_join(self, segments: list[Path], durations: list[float], out: Path) -> None:
        if len(segments) == 1:
            run(["ffmpeg", "-v", "error", "-y", "-i", str(segments[0]), "-c", "copy", str(out)])
            return
        cmd = ["ffmpeg", "-v", "error", "-y"]
        for s in segments:
            cmd += ["-i", str(s)]
        fc, v, a, offset = [], "0:v", "0:a", 0.0
        for i in range(1, len(segments)):
            offset += durations[i - 1] - XFADE
            fc.append(f"[{v}][{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.3f}[v{i}]")
            fc.append(f"[{a}][{i}:a]acrossfade=d={XFADE}:c1=tri:c2=tri[a{i}]")
            v, a = f"v{i}", f"a{i}"
        n = len(segments) - 1
        cmd += ["-filter_complex", ";".join(fc), "-map", f"[v{n}]", "-map", f"[a{n}]",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(self.b.output.crf), "-pix_fmt", "yuv420p",
                "-r", str(self.b.output.fps), "-c:a", "aac", "-b:a", "192k", str(out)]
        run(cmd)


# ------------------------------------------------------------------ helpers


def _loop_input(p: Path, d: float) -> list[str]:
    return ["-loop", "1", "-framerate", "30", "-t", f"{d + 0.5:.2f}", "-i", str(p)]


def _count_inputs(cmd: list[str]) -> int:
    return sum(1 for c in cmd if c == "-i")


def _fade(layer: Layer) -> str:
    if layer.anim == "none":
        return ""
    return f",fade=t=in:st={layer.enter:.2f}:d={ANIM * 0.8:.2f}:alpha=1"


def _ease(layer: Layer, axis: str) -> str:
    """Ease-out-cubic slide: the element decelerates into place instead of snapping."""
    base = layer.x if axis == "x" else layer.y
    dx, dy = {"up": (0, SLIDE), "left": (SLIDE, 0), "right": (-SLIDE, 0), "pop": (0, SLIDE // 2)}.get(layer.anim, (0, 0))
    delta = dx if axis == "x" else dy
    if not delta:
        return str(base)
    p = f"min(1,max(0,(t-{layer.enter:.2f})/{ANIM}))"
    return f"{base}+({delta})*pow(1-{p},3)"


def _mask(p: Path, w: int, h: int, r: int) -> Path:
    if not p.exists():
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
        m.save(p)
    return p


def _fmt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(plan: VideoPlan, timings: list[SceneTiming], lead: float, out: Path) -> None:
    """Full narration as subtitles (sidecar, for accessibility / upload)."""
    cues, start = [], 0.0
    for s, t in zip(plan.scenes, timings):
        if t.vo_dur and s.voiceover.strip():
            words = s.voiceover.split()
            chunks, cur = [], []
            for w in words:
                cur.append(w)
                if len(" ".join(cur)) > 70 or w.endswith((".", "?", "!")) and len(cur) > 3:
                    chunks.append(cur)
                    cur = []
            if cur:
                chunks.append(cur)
            total_chars = sum(len(" ".join(c)) for c in chunks)
            t0 = start + lead
            for c in chunks:
                d = t.vo_dur * len(" ".join(c)) / max(total_chars, 1)
                cues.append((t0, t0 + d, " ".join(c)))
                t0 += d
        start += t.duration - XFADE
    out.write_text("\n".join(f"{i}\n{_fmt(a)} --> {_fmt(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(cues, 1)), encoding="utf-8")

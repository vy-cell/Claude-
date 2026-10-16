from tests.fixtures.make_plan import plan
from tutorial_editor.media import duration
from tutorial_editor.render import XFADE, Renderer


def test_render_small_plan(brand, raw_video, tmp_path):
    p = plan()
    p.scenes = [p.scenes[0], p.scenes[5], p.scenes[6]]  # card, footage with inset/pointer/callouts, compare
    r = Renderer(brand, raw_video, tmp_path / "work")
    timings = r.time_scenes(p, "en")
    res = r.render(p, "en", tmp_path / "out.mp4", timings)
    expected = sum(t.duration for t in timings) - XFADE * 2
    assert abs(duration(res.video) - expected) < 0.25
    assert res.srt.read_text().count("-->") >= 3
    # second render is fully cached (same segment files)
    again = r.render(p, "en", tmp_path / "out2.mp4", timings)
    assert again.segments == res.segments


def test_footage_speed_stays_in_range(brand, raw_video, tmp_path):
    p = plan()
    p.scenes[5].clip_start, p.scenes[5].clip_end = 0, 19  # far too long for the line
    t = Renderer(brand, raw_video, tmp_path).time_scenes(p, "en")[5]
    assert t.speed == brand.timing.footage_speed_range[1]

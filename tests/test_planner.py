import pytest

from tests.fixtures.make_plan import plan
from tutorial_editor.planner import merge_text, sanitize


def test_sanitize_clamps_times_and_boxes():
    p = plan()
    s = p.scenes[5]
    s.clip_start, s.clip_end = 19.9, 50
    s.insets[0].x, s.insets[0].w = 0.9, 0.8
    s.pointers[0].x = 3
    sanitize(p, 20.0)
    assert s.clip_start <= 19.5 and s.clip_end <= 20
    assert s.insets[0].x + s.insets[0].w <= 1.0001
    assert s.pointers[0].x <= 0.98


def test_merge_text_keeps_structure_and_takes_wording():
    src = plan()
    tr = plan()
    tr.scenes[5].voiceover = "Vierta el agua caliente."
    tr.scenes[5].clip_start = 1  # a translation must not move clips
    tr.scenes[5].callouts[0].value = "AGUA CALIENTE"
    out = merge_text(src, tr)
    assert out.scenes[5].voiceover == "Vierta el agua caliente."
    assert out.scenes[5].callouts[0].value == "AGUA CALIENTE"
    assert out.scenes[5].clip_start == src.scenes[5].clip_start


def test_merge_text_rejects_changed_scene_count():
    tr = plan()
    tr.scenes.pop()
    with pytest.raises(ValueError):
        merge_text(plan(), tr)

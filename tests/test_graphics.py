from tests.fixtures.make_plan import plan
from tutorial_editor.graphics import Kit, StillProvider, build_scene, fit, FontBook


def test_every_scene_kind_builds(brand, raw_video, tmp_path):
    kit = Kit(brand, StillProvider(raw_video, tmp_path))
    p = plan()
    for s in p.scenes:
        g = build_scene(kit, p, s, 16 / 9, lambda x, y: (int(x * 1920), int(y * 1080)))
        assert g.layers, s.kind
        assert (g.background is None) == (s.kind == "footage")
        for layer in g.layers:
            if layer.kind == "image":
                assert layer.img.mode == "RGBA"


def test_vietnamese_heading_falls_back_to_a_font_with_the_glyphs(brand):
    fonts = FontBook(brand)
    vi = "Ướp gà viên"
    f = fonts.get("heading", 60, text=vi)
    assert "NotoSerif" in f.path
    assert "Caudex" in fonts.get("heading", 60, text="Popcorn").path


def test_long_text_is_reported_not_silently_cut(brand):
    warnings = []
    _, lines, _ = fit(FontBook(brand), "body", "word " * 80, 400, 40, 30, 1, warnings=warnings, what="x")
    assert lines[-1].endswith("…")
    assert any("truncated" in w for w in warnings)

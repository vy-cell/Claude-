"""End-to-end run of the autonomous loop with Claude replaced by canned answers."""
import json

from tests.fixtures.make_card import card
from tests.fixtures.make_plan import plan
from tutorial_editor import card as card_mod
from tutorial_editor import planner
from tutorial_editor.card import CardReview, SOPCard
from tutorial_editor.models import VideoPlan
from tutorial_editor.pipeline import Options, make
from tutorial_editor.planner import Issue, Review


def test_make_runs_review_rounds_translates_and_builds_card(brand, raw_video, tmp_path, monkeypatch):
    calls = {"review": 0}
    small = plan()
    small.scenes = small.scenes[:2] + small.scenes[5:6] + small.scenes[-1:]

    def fake_ask(system, content, schema, **kw):
        prompt = " ".join(b.get("text", "") for b in content if b["type"] == "text")
        if schema is VideoPlan and "Translate" in prompt:
            t = small.model_copy(deep=True)
            for s in t.scenes:
                s.voiceover = "Traducido: " + s.voiceover
            t.scenes[2].clip_start = 0  # must be ignored: translations cannot move clips
            return t
        if schema is VideoPlan:
            return small.model_copy(deep=True)
        if schema is Review:
            calls["review"] += 1
            p = small.model_copy(deep=True)
            if calls["review"] == 1:  # first round: director asks for a shorter caption
                p.scenes[1].caption = "3 steps"
                return Review(score=70, ready_to_ship=False, plan=p,
                              issues=[Issue(scene=1, severity="medium", problem="caption long", fix="shorten")])
            p.scenes[1].caption = "3 steps"
            return Review(score=95, ready_to_ship=True, issues=[], plan=p)
        if schema is SOPCard:
            return card()
        if schema is CardReview:
            return CardReview(score=95, ready_to_ship=True, issues=[], card=card())
        raise AssertionError(schema)

    monkeypatch.setattr(planner, "ask", fake_ask)
    monkeypatch.setattr(card_mod, "ask", fake_ask)
    opt = Options(languages=["en", "es"], max_rounds=3, translation_rounds=1, card_rounds=1, notes="5 minute steep")
    outputs = make(raw_video, brand, tmp_path, opt)

    assert set(outputs) == {"en", "es"}
    for lang in ("en", "es"):
        assert (tmp_path / "raw" / lang / f"raw_{lang}.mp4").exists()
        assert outputs[lang]["card"].endswith(".pdf")
    final = json.loads((tmp_path / "raw" / "en" / "plan_en.json").read_text())
    assert final["scenes"][1]["caption"] == "3 steps"  # the director's fix was applied
    es = json.loads((tmp_path / "raw" / "es" / "plan_es.json").read_text())
    assert es["scenes"][2]["clip_start"] == small.scenes[2].clip_start
    log = (tmp_path / "raw" / "review_log.md").read_text()
    assert "score **70**" in log and "shipped" in log
    meta = json.loads((tmp_path / "raw" / "render_meta.json").read_text())
    assert len(meta["scene_times"]) == len(small.scenes)

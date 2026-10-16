import subprocess

import pytest

from tests.fixtures.make_card import card
from tutorial_editor.brand import CardInfo
from tutorial_editor.card import PhotoSources, load_embedded, merge_card_text, render_card, resolve_photos
from tutorial_editor.cardflow import bump_version, edit_card_pdf

INFO = CardInfo(version="1.0", effective="10/16/2026", approved_by="Test Person", approver_title="Kitchen Lead",
                video_url="https://example.com/video-{lang}")


@pytest.fixture
def rendered(brand, raw_video, tmp_path):
    c = card()
    photos = tmp_path / "photos"
    assert resolve_photos(c, PhotoSources(photos, raw_video)) == []
    r = render_card(brand, c, INFO, "en", photos, tmp_path / "en" / "card_en.pdf")
    return c, r, photos


def test_two_page_card_with_footer_fields_and_no_overflow(rendered):
    c, r, _ = rendered
    assert len(r.pages) == 2 and r.warnings == []
    txt = subprocess.run(["pdftotext", str(r.pdf), "-"], capture_output=True, text=True).stdout
    assert "Approved by Test Person, Kitchen Lead" in txt and "SOP v1.0" in txt and "Effective 10/16/2026" in txt
    assert "Turn over for steps 7–8" in txt
    assert all(s.photo.file and s.photo.time == -1 for s in c.steps)  # photos were cut from the footage


def test_overflow_is_reported(brand, rendered, tmp_path):
    c, _, photos = rendered
    for s in c.steps:
        s.body = "Very long instruction text that keeps going. " * 12
    r = render_card(brand, c, INFO, "en", photos, tmp_path / "long.pdf")
    assert any("overflow" in w or "footer" in w for w in r.warnings)


def test_embedded_source_allows_editing_fields_without_ai(brand, rendered, tmp_path):
    c, r, _ = rendered
    out = edit_card_pdf(brand, r.pdf, tmp_path / "edit" / "card_en.pdf", "", {"approved_by": "New Name", "version": None,
                        "effective": None, "approver_title": None, "video_url": None}, None, [], print)
    loaded = load_embedded(tmp_path / "edit" / "card_en.pdf", tmp_path / "chk")
    assert loaded and loaded[1].approved_by == "New Name" and loaded[1].version == "1.0"
    assert loaded[0].title == c.title and "en" in out


def test_translation_keeps_photos_and_structure(rendered):
    c, _, _ = rendered
    tr = c.model_copy(deep=True)
    tr.title = "Lote de té helado"
    tr.steps[0].photo.file = "something_else.jpg"
    out = merge_card_text(c, tr)
    assert out.title == "Lote de té helado" and out.steps[0].photo.file == c.steps[0].photo.file
    tr.steps.pop()
    with pytest.raises(ValueError):
        merge_card_text(c, tr)


def test_bump_version():
    assert bump_version("1.0") == "1.1" and bump_version("2.9") == "2.10" and bump_version("draft") == "draft"

from PIL import Image

from tests.fixtures.make_card import card
from tutorial_editor.card import PAGE, render_card
from tutorial_editor.graphics import StillProvider


def test_card_renders_one_page(brand, raw_video, tmp_path):
    png, pdf, warnings = render_card(brand, card(), StillProvider(raw_video, tmp_path / "st"), tmp_path / "card.png")
    assert Image.open(png).size == PAGE
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert warnings == []


def test_crop_keeps_aspect_and_stays_in_frame():
    from tutorial_editor.card import Crop, _crop_rect

    x, y, w, h = _crop_rect(1920, 1080, Crop(x=0.8, y=0.7, w=0.3, h=0.3), 1.2)
    assert abs(w / h - 1.2) < 0.01
    assert 0 <= x and x + w <= 1920.01 and 0 <= y and y + h <= 1080.01

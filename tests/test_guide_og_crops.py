"""The OG crops of the guide screenshots are 1200x630 and are cut, never stretched."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
pytest.importorskip("playwright")
from PIL import Image  # noqa: E402

LIVE = Path(__file__).parent / "live"


@pytest.fixture(scope="module")
def guides(tmp_path_factory):
    sys.path.insert(0, str(LIVE))
    saved = sys.argv
    sys.argv = ["capture_guides.py", str(tmp_path_factory.mktemp("guides"))]
    try:
        yield importlib.import_module("capture_guides")
    finally:
        sys.argv = saved
        sys.path.remove(str(LIVE))


def test_every_crop_is_1200_by_630(guides) -> None:
    assert guides.OG_SIZE == (1200, 630)
    image = Image.new("RGB", (3200, 2400), "white")
    for name, (_, left, top, right) in guides.OG_CROPS.items():
        if name == "web-app-phone":
            continue
        assert guides.og_image(name, image, left, top, right).size == (1200, 630), name


def test_crop_keeps_the_aspect_ratio_of_its_box(guides) -> None:
    image = Image.new("RGB", (2000, 1500), "black")
    image.paste(Image.new("RGB", (200, 200), "white"), (900, 400))
    out = guides.og_image("square", image, 0.25, 0.1, 0.75)
    box = out.convert("L").point(lambda v: 255 if v > 128 else 0).getbbox()
    assert box is not None
    width, height = box[2] - box[0], box[3] - box[1]
    assert abs(width - height) <= 2  # a stretched crop would turn the square into a rectangle


def test_crop_below_the_source_image_fails_loudly(guides) -> None:
    image = Image.new("RGB", (1200, 700), "black")
    with pytest.raises(SystemExit):
        guides.og_image("low", image, 0.0, 0.5, 1.0)


def test_phone_crop_shows_the_whole_screen_on_a_1200_by_630_canvas(guides) -> None:
    phone = Image.new("RGB", (780, 1688), (10, 20, 15))
    out = guides.og_phone(phone)
    assert out.size == (1200, 630)
    assert out.getpixel((2, 300)) == (10, 20, 15)

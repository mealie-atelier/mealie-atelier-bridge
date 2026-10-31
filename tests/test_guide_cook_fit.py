"""The guide capture grows the window until the bounded cooking dialog shows the whole step."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
pytest.importorskip("playwright")

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


class FakePage:
    """A page whose step stage overflows by `overflow` pixels at `base` height and shrinks as it grows."""

    def __init__(self, base: int, overflow: int) -> None:
        self.viewport_size = {"width": 390, "height": base}
        self.base = base
        self.overflow = overflow

    def set_viewport_size(self, size: dict) -> None:
        self.viewport_size = size

    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def evaluate(self, _script: str, args: list) -> int:
        assert args[1] == ".cook-content > .cook-stage"
        return max(0, self.overflow - (self.viewport_size["height"] - self.base))


def test_window_grows_until_the_step_needs_no_scrolling(guides) -> None:
    guides.SIZES["kochmodus-handy"] = (780, 1500)
    page = FakePage(749, 113)
    guides.fit_cook(page, "kochmodus-handy", 749, 940)
    assert 749 + 113 <= page.viewport_size["height"] <= 940
    assert guides.SIZES["kochmodus-handy"][1] == round(page.viewport_size["height"] * 780 / 390)


def test_window_stops_at_the_limit(guides) -> None:
    guides.SIZES["kochmodus-dunkel"] = (1600, 1100)
    page = FakePage(1050, 5000)
    guides.fit_cook(page, "kochmodus-dunkel", 1050, 1200)
    assert page.viewport_size["height"] == 1200

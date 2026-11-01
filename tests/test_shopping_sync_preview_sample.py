"""The preview sample comes from the first line that would really be sent."""

# ruff: noqa: F811

from __future__ import annotations

from custom_components.mealie_atelier.shopping_sync import SyncManager

from .sync_support import FakeMealie, FakeTodo, draft, mealie_item
from .test_sync_engine import (  # noqa: F401
    bring,
    mealie,
    sync,
    todos,
)


async def test_sample_skips_leading_duplicate(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))
    mealie.add(mealie_item("b", note="Brot"))
    bring.seed("1 Milch")

    preview = await sync.async_preview(draft=draft())

    assert [line["action"] for line in preview["lines"]] == ["skip_duplicate", "send"]
    assert preview["sample"]["title"] == "1 Brot"


async def test_sample_is_none_when_everything_is_skipped(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))
    bring.seed("1 Milch")

    preview = await sync.async_preview(draft=draft())

    assert [line["action"] for line in preview["lines"]] == ["skip_duplicate"]
    assert preview["sample"] is None

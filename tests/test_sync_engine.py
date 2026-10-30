"""Sync engine against real Home Assistant To-do services and a mocked Mealie shopping list."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.components.shopping_list.common import _get_shopping_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.mealie_atelier.const import EVENT_SYNC_FINISHED
from custom_components.mealie_atelier.shopping_sync import SyncManager, engine
from custom_components.mealie_atelier.shopping_sync.mapping import fingerprint
from custom_components.mealie_atelier.shopping_sync.store import prune

from .conftest import ENTRY_ID, MEALIE_TOKEN, MEALIE_URL
from .sync_support import (
    PARTY,
    FakeMealie,
    FakeTodo,
    draft,
    food,
    mealie_item,
    setup_todo,
    unit,
)

GRAM = unit("gram", abbreviation="g")
CUP = unit("cup", plural="cups", fraction=True)
POTATOES = food("Kartoffeln")


@pytest.fixture
async def todos(hass: HomeAssistant) -> tuple[FakeTodo, FakeTodo]:
    bring = FakeTodo("Einkauf", "bring-1")
    plain = FakeTodo("Plain list", "plain-1", description=False)
    await setup_todo(hass, bring, plain)
    return bring, plain


@pytest.fixture
def bring(todos) -> FakeTodo:
    return todos[0]


@pytest.fixture
def mealie(mock_mealie: AiohttpClientMocker) -> FakeMealie:
    return FakeMealie(mock_mealie)


@pytest.fixture
async def sync(todos, mealie: FakeMealie, setup_integration: MockConfigEntry) -> SyncManager:
    return setup_integration.runtime_data.sync


@pytest.fixture
def fast_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "SYNC_SERVICE_TIMEOUT", 0.05)


async def save_rule(sync: SyncManager, **overrides: Any) -> str:
    entity_id = overrides.pop("entity_id", "todo.einkauf")
    status = await sync.async_save_rule(
        draft(entity_id, **overrides),
        rule_id=None,
        expected_revision=None,
        reset_records=False,
        user_id="admin-user",
    )
    return status["id"]


async def activate(sync: SyncManager, **overrides: Any) -> tuple[str, dict[str, Any]]:
    rule_id = await save_rule(sync, **overrides)
    result = await sync.async_run(rule_id, "manual", confirm_first_batch=True)
    return rule_id, result


def states(sync: SyncManager, rule_id: str) -> dict[str, str]:
    return {
        record["source_item_id"]: record["state"]
        for record in sync.records.values()
        if record["rule_id"] == rule_id
    }


async def wait_until(condition, rounds: int = 200) -> None:
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


def slow_shopping_lists(sync: SyncManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the source lookup yield so concurrent saves interleave at that await."""
    original = sync.client.shopping_lists

    async def slow() -> list[dict[str, Any]]:
        for _ in range(5):
            await asyncio.sleep(0)
        return await original()

    monkeypatch.setattr(sync.client, "shopping_lists", slow)


async def test_concurrent_saves_for_one_source_create_one_rule(
    sync: SyncManager, monkeypatch: pytest.MonkeyPatch
):
    slow_shopping_lists(sync, monkeypatch)

    results = await asyncio.gather(save_rule(sync), save_rule(sync), return_exceptions=True)

    assert len(sync.rules) == 1
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(errors) == 1
    assert isinstance(errors[0], engine.SyncError)
    assert errors[0].code == "sync_source_taken"


async def test_concurrent_saves_with_same_revision_conflict(
    sync: SyncManager, monkeypatch: pytest.MonkeyPatch
):
    rule_id = await save_rule(sync)
    slow_shopping_lists(sync, monkeypatch)

    async def update(name: str) -> dict[str, Any]:
        return await sync.async_save_rule(
            draft("todo.einkauf", name=name),
            rule_id=rule_id,
            expected_revision=1,
            reset_records=False,
            user_id="admin-user",
        )

    results = await asyncio.gather(update("First"), update("Second"), return_exceptions=True)

    assert sync.rules[rule_id]["revision"] == 2
    saved = [result for result in results if not isinstance(result, Exception)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(saved) == 1
    assert sync.rules[rule_id]["name"] == saved[0]["name"]
    assert len(errors) == 1
    assert isinstance(errors[0], engine.SyncError)
    assert errors[0].code == "conflict"


async def test_copy_mode_maps_items_and_never_writes_to_mealie(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(
        mealie_item("a", food=POTATOES, unit=GRAM, quantity=300, note="festkochend"),
        mealie_item("b", note="Backpapier", quantity=None),
        mealie_item("c", food=food("Milk"), unit=CUP, quantity=1.5),
        mealie_item("d", note="Already bought", checked=True),
        mealie_item("e", quantity=2),
        mealie_item("foreign-1", list_id=PARTY, note="Party item"),
    )
    events = async_capture_events(hass, EVENT_SYNC_FINISHED)

    rule_id, result = await activate(sync)

    assert result["outcome"] == "sent"
    assert result["sent"] == 3
    assert bring.calls == ["300 g Kartoffeln", "Backpapier", "1 1/2 cups Milk"]
    assert bring.descriptions == ["festkochend", None, None]
    assert states(sync, rule_id) == {"a": "sent", "b": "sent", "c": "sent"}
    assert mealie.puts == []
    rule = sync.rules[rule_id]
    assert rule["activated"] is True
    assert rule["last_run"]["outcome"] == "sent"
    assert events[-1].data == {
        "entry_id": ENTRY_ID,
        "rule_id": rule_id,
        "outcome": "sent",
        "count": 3,
    }
    audit = json.dumps(sync.store.data["audit"])
    for text in ("Kartoffeln", "Backpapier", "festkochend", "Milk", MEALIE_TOKEN, MEALIE_URL):
        assert text not in audit

    # A second run has nothing new and never sends twice.
    again = await sync.async_run(rule_id, "manual")
    assert again["outcome"] == "nothing_to_send"
    assert len(bring.calls) == 3


async def test_note_goes_into_title_without_description_support(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie, todos
):
    _bring, plain = todos
    mealie.add(mealie_item("a", food=POTATOES, unit=GRAM, quantity=300, note="festkochend"))
    hass.config.language = "de"

    await activate(sync, entity_id="todo.plain_list")

    assert plain.calls == ["300 g Kartoffeln (festkochend)"]
    assert plain.descriptions == [None]


async def test_move_mode_completes_only_confirmed_items(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))
    bring.reject = {"1 Brot"}

    rule_id, result = await activate(sync, mode="move")

    assert result["outcome"] == "partial"
    assert result["completed_in_source"] == 1
    assert states(sync, rule_id) == {"a": "source_completed", "b": "failed"}
    assert mealie.puts == ["a"]
    assert mealie.items["a"]["checked"] is True
    assert mealie.items["b"]["checked"] is False
    assert sync.records[f"{rule_id}:b"]["last_error"] == "destination_rejected"


async def test_failed_add_never_completes_the_source(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    """Release gate from the product spec: a rejected add leaves Mealie untouched."""
    mealie.add(*(mealie_item(f"i{n}", note=f"Item {n}") for n in range(5)))
    bring.mode = "reject"

    rule_id, result = await activate(sync, mode="move")

    assert result["outcome"] == "failed"
    # Three failures in a row stop the batch; the rest stays pending for the next run.
    assert result["failed"] == 3
    assert result["pending_left"] == 2
    assert mealie.puts == []
    assert not any(item["checked"] for item in mealie.items.values())
    assert sync.rules[rule_id]["activated"] is False


async def test_confirmed_duplicate_completes_in_move_mode(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    bring.seed("1 Milch")
    bring.seed("1 Brot", completed=True)
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))

    rule_id, result = await activate(sync, mode="move")

    assert result["skipped_duplicates"] == 1
    # A completed destination line was already bought; it is not a duplicate.
    assert bring.calls == ["1 Brot"]
    record = sync.records[f"{rule_id}:a"]
    assert record["state"] == "source_completed"
    assert record["duplicate_of"] == bring._attr_todo_items[0].uid
    assert sorted(mealie.puts) == ["a", "b"]


async def test_switching_copy_to_move_keeps_earlier_items_open(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))
    rule_id, _ = await activate(sync)
    mealie.add(mealie_item("c", note="Käse"))

    await sync.async_save_rule(
        draft("todo.einkauf", mode="move"),
        rule_id=rule_id,
        expected_revision=sync.rules[rule_id]["revision"],
        reset_records=False,
        user_id="admin-user",
    )
    result = await sync.async_run(rule_id, "manual")

    assert result["sent"] == 1
    assert result["completed_in_source"] == 1
    assert sorted(mealie.puts) == ["c"]
    assert states(sync, rule_id) == {"a": "sent", "b": "sent", "c": "source_completed"}


async def test_run_count_ignores_skipped_duplicates(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    bring.seed("1 Milch")
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))

    rule_id, result = await activate(sync)

    assert result["sent"] == 1
    assert result["skipped_duplicates"] == 1
    assert sync.rules[rule_id]["last_run"]["count"] == 1
    assert sync.store.data["audit"][-1]["count"] == 1


async def test_in_batch_duplicate_follows_a_rejected_lead(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    """A duplicate inside one batch is never completed before its lead was accepted."""
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="milch"))
    bring.reject = {"1 Milch"}

    rule_id, result = await activate(sync, mode="move")

    assert result["outcome"] == "failed"
    assert result["skipped_duplicates"] == 0
    assert bring.calls == ["1 Milch"]
    assert states(sync, rule_id) == {"a": "failed", "b": "failed"}
    assert mealie.puts == []

    bring.reject = set()
    sync.rules[rule_id]["activated"] = True
    result = await sync.async_retry(rule_id, None)

    assert result["sent"] == 1
    assert result["skipped_duplicates"] == 1
    assert bring.calls == ["1 Milch", "1 Milch"]
    assert states(sync, rule_id) == {"a": "source_completed", "b": "source_completed"}
    assert sync.records[f"{rule_id}:b"]["duplicate_of"] == f"{rule_id}:a"
    assert sorted(mealie.puts) == ["a", "b"]


async def test_in_batch_duplicate_follows_an_uncertain_lead(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo, fast_timeout
):
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="milch"))
    bring.mode = "hang"

    rule_id, result = await activate(sync, mode="move")

    assert result["needs_reconciliation"] == 2
    assert states(sync, rule_id) == {"a": "needs_reconciliation", "b": "needs_reconciliation"}
    assert mealie.puts == []


async def test_item_edited_between_send_and_completion_is_not_completed(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))

    def edit_in_mealie(_summary: str) -> None:
        mealie.items["a"] = {**mealie.items["a"], "quantity": 2}

    bring.on_create = edit_in_mealie

    rule_id, result = await activate(sync, mode="move")

    assert result["sent"] == 1
    assert states(sync, rule_id) == {"a": "changed_after_send"}
    assert mealie.puts == []


async def test_unavailable_destination_keeps_items_pending(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync, mode="move")
    mealie.add(mealie_item("b", note="Brot"))
    bring.set_available(False)

    result = await sync.async_run(rule_id, "manual")

    assert result["outcome"] == "destination_unavailable"
    assert bring.calls == ["1 Milch"]
    assert sync.store.data["audit"][-1]["outcome"] == "destination_unavailable"

    bring.set_available(True)
    result = await sync.async_run(rule_id, "manual")
    assert result["outcome"] == "sent"
    assert states(sync, rule_id) == {"a": "source_completed", "b": "source_completed"}


async def test_timeout_after_store_is_reconciled_without_duplicate(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo, fast_timeout
):
    mealie.add(mealie_item("a", note="Milch"))
    bring.mode = "hang_after_store"

    rule_id, result = await activate(sync, mode="move")

    assert result["outcome"] == "failed"
    assert result["needs_reconciliation"] == 1
    assert states(sync, rule_id) == {"a": "needs_reconciliation"}
    assert sync.records[f"{rule_id}:a"]["last_error"] == "destination_timeout"
    assert mealie.puts == []

    bring.mode = "ok"
    # Confirming again reconciles first: the line is already there, so nothing is sent twice.
    result = await sync.async_run(rule_id, "manual", confirm_first_batch=True)

    assert result["outcome"] == "sent"
    assert sync.rules[rule_id]["activated"] is True
    assert bring.calls == ["1 Milch"]
    assert bring.summaries == ["1 Milch"]
    assert states(sync, rule_id) == {"a": "source_completed"}
    assert any(row["outcome"] == "reconciled" for row in sync.store.data["audit"])


async def test_timeout_without_store_is_sent_again_once(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo, fast_timeout
):
    mealie.add(mealie_item("a", note="Milch"))
    bring.mode = "hang"
    rule_id, _ = await activate(sync, mode="copy")
    sync.rules[rule_id]["activated"] = True
    assert states(sync, rule_id) == {"a": "needs_reconciliation"}

    bring.mode = "ok"
    result = await sync.async_run(rule_id, "manual")

    assert result["sent"] == 1
    assert bring.calls == ["1 Milch", "1 Milch"]
    assert bring.summaries == ["1 Milch"]
    assert states(sync, rule_id) == {"a": "sent"}


async def test_ambiguous_reconciliation_waits_for_the_user(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo, fast_timeout
):
    mealie.add(mealie_item("a", note="Milch"))
    bring.mode = "hang"
    rule_id, _ = await activate(sync, duplicate_policy="none")
    sync.rules[rule_id]["activated"] = True
    bring.seed("1 Milch")
    bring.seed("1 Milch")
    bring.mode = "ok"

    await sync.async_run(rule_id, "manual")

    record = sync.records[f"{rule_id}:a"]
    assert record["state"] == "needs_reconciliation"
    assert record["last_error"] == "ambiguous"
    assert bring.calls == ["1 Milch"]

    result = await sync.async_resolve(rule_id, record["key"], "dismiss")
    assert result["record"]["state"] == "sent"
    assert "fingerprint" not in result["record"]


async def test_changes_after_send_are_flagged_and_destination_untouched(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))
    rule_id, _ = await activate(sync)
    mealie.items["a"]["quantity"] = 3
    del mealie.items["b"]

    await sync.async_run(rule_id, "manual")

    assert states(sync, rule_id) == {"a": "changed_after_send", "b": "removed_after_send"}
    assert bring.summaries == ["1 Milch", "1 Brot"]
    assert len(bring.calls) == 2

    removed = await sync.async_resolve(rule_id, f"{rule_id}:b", "dismiss")
    assert removed["deleted"] is True
    await sync.async_resolve(rule_id, f"{rule_id}:a", "resend")
    result = await sync.async_run(rule_id, "manual")

    assert result["sent"] == 1
    assert bring.calls[-1] == "3 Milch"
    assert states(sync, rule_id) == {"a": "sent"}


async def test_dismissed_change_on_completed_item_is_stable(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync, mode="move")
    mealie.items["a"]["checked"] = False

    await sync.async_run(rule_id, "manual")
    assert states(sync, rule_id) == {"a": "changed_after_send"}
    dismissed = await sync.async_resolve(rule_id, f"{rule_id}:a", "dismiss")
    # Deviation from contract A.7 (`sent`): `sent` would let move mode check it off again.
    assert dismissed["record"]["state"] == "source_completed"
    await sync.async_run(rule_id, "manual")

    assert states(sync, rule_id) == {"a": "source_completed"}
    assert mealie.puts == ["a"]
    assert bring.calls == ["1 Milch"]


async def test_merge_policy_sends_one_line_and_completes_every_member(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(
        mealie_item("a", food=POTATOES, unit=GRAM, quantity=300),
        mealie_item("b", food=POTATOES, unit=GRAM, quantity=200),
        mealie_item("c", food=POTATOES, unit=unit("kilogram", abbreviation="kg"), quantity=1),
    )

    rule_id, result = await activate(
        sync, mode="move", duplicate_policy="skip_exact_merge_same_unit"
    )

    assert bring.calls == ["500 g Kartoffeln", "1 kg Kartoffeln"]
    assert result["merged"] == 1
    assert sync.records[f"{rule_id}:b"]["merged_into"] == f"{rule_id}:a"
    assert states(sync, rule_id) == {
        "a": "source_completed",
        "b": "source_completed",
        "c": "source_completed",
    }


async def test_runs_of_one_rule_are_serialized(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync)
    mealie.add(mealie_item("b", note="Brot"))
    bring.gate = asyncio.Event()

    first = asyncio.create_task(sync.async_run(rule_id, "manual"))
    await wait_until(lambda: bring.active == 1)
    assert sync.rule_status(sync.rules[rule_id])["running"] is True
    assert await sync.async_run(rule_id, "manual") == {"queued": True}
    mealie.add(mealie_item("c", note="Eier"))
    bring.gate.set()
    await first
    await hass.async_block_till_done()

    assert bring.calls == ["1 Milch", "1 Brot", "1 Eier"]
    assert bring.max_active == 1


async def test_two_rules_run_concurrently(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie, todos
):
    bring, plain = todos
    mealie.add(mealie_item("a", note="Milch"), mealie_item("p", list_id=PARTY, note="Chips"))
    weekly, _ = await activate(sync)
    party, _ = await activate(sync, entity_id="todo.plain_list", source_list_id=PARTY)
    mealie.add(mealie_item("b", note="Brot"), mealie_item("q", list_id=PARTY, note="Saft"))
    bring.gate = asyncio.Event()
    plain.gate = asyncio.Event()

    runs = [
        asyncio.create_task(sync.async_run(weekly, "manual")),
        asyncio.create_task(sync.async_run(party, "manual")),
    ]
    await wait_until(lambda: bring.active == 1 and plain.active == 1)
    bring.gate.set()
    plain.gate.set()
    results = await asyncio.gather(*runs)

    assert [result["outcome"] for result in results] == ["sent", "sent"]
    assert bring.calls == ["1 Milch", "1 Brot"]
    assert plain.calls == ["1 Chips", "1 Saft"]


async def test_debounce_collapses_bursts_into_one_run(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync)
    events = async_capture_events(hass, EVENT_SYNC_FINISHED)
    for n in range(5):
        mealie.add(mealie_item(f"n{n}", note=f"Neu {n}"))
        sync.notify_source_changed("list-weekly", "after_atelier_add")
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert events == []
    assert sync.rule_status(sync.rules[rule_id])["next_run_at"] is not None

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert len(bring.calls) == 6
    assert sync.store.data["audit"][-1]["trigger"] == "after_atelier_add"


async def test_paused_or_unconfirmed_rules_ignore_automatic_triggers(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id = await save_rule(sync)
    sync.notify_source_changed("list-weekly", "after_atelier_add")
    assert sync.rule_status(sync.rules[rule_id])["next_run_at"] is None

    await sync.async_run(rule_id, "manual", confirm_first_batch=True)
    await sync.async_set_paused(rule_id, True)
    mealie.add(mealie_item("b", note="Brot"))
    sync.notify_source_changed("list-weekly", "after_atelier_add")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert bring.calls == ["1 Milch"]

    # A manual run of a paused rule is an explicit action and still allowed.
    result = await sync.async_run(rule_id, "manual")
    assert result["sent"] == 1
    assert sync.rules[rule_id]["paused"] is True

    await sync.async_set_paused(rule_id, False)
    mealie.add(mealie_item("c", note="Eier"))
    sync.notify_source_changed("list-weekly", "after_atelier_add")
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert bring.calls[-1] == "1 Eier"


async def test_watcher_polls_only_active_watch_rules(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    mealie.add(mealie_item("a", note="Milch"))
    plain_rule, _ = await activate(sync)
    assert sync.counts()["watchers"] == 0
    assert any(entry["rule_id"] == plain_rule for entry in sync.store.data["audit"])
    await sync.async_delete_rule(plain_rule)
    assert not any(entry["rule_id"] == plain_rule for entry in sync.store.data["audit"])
    sync.store.data["audit"].append({"at": "", "rule_id": plain_rule, "count": 1})
    assert sync.state()["audit"] == []

    rule_id, _ = await activate(
        sync, triggers={"after_atelier_add": False, "watch": True}, poll_interval_seconds=60
    )
    assert sync.counts()["watchers"] == 1

    async def tick(seconds: int) -> None:
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    reads = mealie.list_reads
    await tick(61)
    assert mealie.list_reads == reads + 1
    assert sync.rule_status(sync.rules[rule_id])["next_run_at"] is None

    mealie.add(mealie_item("b", note="Brot"))
    await tick(61)
    assert sync.rule_status(sync.rules[rule_id])["next_run_at"] is not None
    await tick(31)
    assert bring.calls[-1] == "1 Brot"
    assert sync.store.data["audit"][-1]["trigger"] == "watch"

    await sync.async_set_paused(rule_id, True)
    assert sync.counts()["watchers"] == 0
    reads = mealie.list_reads
    await tick(200)
    assert mealie.list_reads == reads

    await sync.async_set_paused(rule_id, False)
    assert sync.counts()["watchers"] == 1
    await sync.async_delete_rule(rule_id)
    assert sync.counts()["watchers"] == 0


async def test_reload_keeps_outbox_and_startup_run_reconciles(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))
    rule_id, _ = await activate(sync, mode="move")
    mealie.add(mealie_item("c", note="Eier"))
    # Simulate a stop right after the destination stored the item, before success was recorded.
    bring.seed("1 Eier")
    record = {
        **sync.records[f"{rule_id}:a"],
        "key": f"{rule_id}:c",
        "source_item_id": "c",
        "state": "sending",
        "title": "1 Eier",
        "fingerprint": fingerprint(mealie.items["c"]),
        "completed_at": None,
    }
    sync.records[record["key"]] = record
    await sync.store.async_save()

    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()
    reloaded = setup_integration.runtime_data.sync
    assert reloaded is not sync
    assert reloaded.rules[rule_id]["activated"] is True
    assert states(reloaded, rule_id) == {
        "a": "source_completed",
        "b": "source_completed",
        "c": "needs_reconciliation",
    }
    assert reloaded.rule_status(reloaded.rules[rule_id])["next_run_at"] is not None

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert states(reloaded, rule_id)["c"] == "source_completed"
    assert bring.calls == ["1 Milch", "1 Brot"]
    assert reloaded.store.data["audit"][-1]["trigger"] == "startup"


async def test_unload_cancels_background_work(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    sync: SyncManager,
    mealie: FakeMealie,
):
    mealie.add(mealie_item("a", note="Milch"))
    await activate(sync, triggers={"after_atelier_add": True, "watch": True})
    sync.notify_source_changed("list-weekly", "after_atelier_add")
    assert sync.counts()["watchers"] == 1

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()

    assert sync.counts()["watchers"] == 0
    assert sync._scheduled == {}
    sync.notify_source_changed("list-weekly", "after_atelier_add")
    assert sync._scheduled == {}


async def test_source_read_failure_is_reported(sync: SyncManager, mealie: FakeMealie):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync)
    mealie.fail_reads = True

    result = await sync.async_run(rule_id, "manual")

    assert result["outcome"] == "source_unavailable"


async def test_corrupt_store_is_set_aside_and_setup_succeeds(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    tmp_path,
):
    key = f"mealie_atelier_sync_{ENTRY_ID}"
    hass.config.config_dir = str(tmp_path)
    (tmp_path / ".storage").mkdir()
    (tmp_path / ".storage" / key).write_text("{not json")
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": "garbage"}
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.sync.rules == {}
    assert (tmp_path / ".storage" / f"{key}.corrupt").exists()
    assert not (tmp_path / ".storage" / key).exists()
    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_pruning_drops_only_old_finished_records(sync: SyncManager, mealie: FakeMealie):
    mealie.add(mealie_item("a", note="Milch"), mealie_item("b", note="Brot"))
    rule_id, _ = await activate(sync, mode="move")
    old = (dt_util.utcnow() - timedelta(days=40)).isoformat()
    for record in sync.records.values():
        record["updated_at"] = old
    sync.records[f"{rule_id}:b"]["state"] = "failed"

    dropped = prune(sync.store.data, present={rule_id: set()})

    assert dropped == 1
    assert list(states(sync, rule_id)) == ["b"]


async def test_core_shopping_list_as_real_destination(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie
):
    mealie.add(mealie_item("a", food=POTATOES, unit=GRAM, quantity=300, note="festkochend"))
    with (
        patch("homeassistant.components.shopping_list.common.ShoppingData.save"),
        patch(
            "homeassistant.components.shopping_list.common.ShoppingData.async_load",
            AsyncMock(),
        ),
    ):
        entry = MockConfigEntry(domain="shopping_list")
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = next(
            state.entity_id
            for state in hass.states.async_all("todo")
            if er_platform(hass, state.entity_id) == "shopping_list"
        )

        rule_id, result = await activate(sync, entity_id=entity_id, mode="move")

        assert result["outcome"] == "sent"
        # The core shopping list has no description field, so the note stays in the title.
        assert [item["name"] for item in _get_shopping_data(hass).items] == [
            "300 g Kartoffeln (festkochend)"
        ]
        assert states(sync, rule_id) == {"a": "source_completed"}
        again = await sync.async_run(rule_id, "manual")
        assert again["outcome"] == "nothing_to_send"
        assert len(_get_shopping_data(hass).items) == 1


def er_platform(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else None

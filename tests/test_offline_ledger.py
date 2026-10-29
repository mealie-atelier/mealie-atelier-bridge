"""Offline shopping ledger: idempotency, conflicts, crash safety, pruning and `offline_replay`."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import CLIENT_ID

from custom_components.mealie_atelier.api import MealieError
from custom_components.mealie_atelier.const import CONF_READ_ONLY, CONF_WRITE_USERS
from custom_components.mealie_atelier.errors import AtelierError
from custom_components.mealie_atelier.offline_ledger import (
    OfflineLedger,
    prune,
    validate_operation,
)

from .conftest import ENTRY_ID

KEY = f"mealie_atelier_offline_{ENTRY_ID}"
DAY = 86400


def uid(n: int) -> str:
    return f"6f1c1c1e-2f6a-4c0e-8d7b-{n:012d}"


def toggle(n: int = 1, *, checked=True, expected=False, item="item-1") -> dict[str, Any]:
    return {
        "id": uid(n),
        "action": "shopping_toggle",
        "payload": {"item_id": item, "checked": checked, "expected_checked": expected},
    }


def add(n: int = 1, note: str = "Milk") -> dict[str, Any]:
    return {"id": uid(n), "action": "shopping_add", "payload": {"list_id": "list-1", "note": note}}


def delete(n: int = 1, item="item-1") -> dict[str, Any]:
    return {"id": uid(n), "action": "shopping_delete", "payload": {"item_id": item}}


def clear(n: int = 1, ids: tuple[str, ...] = ("item-1", "item-2")) -> dict[str, Any]:
    return {
        "id": uid(n),
        "action": "shopping_clear_checked",
        "payload": {"list_id": "list-1", "expected_ids": list(ids)},
    }


def update(n: int = 1, **payload: Any) -> dict[str, Any]:
    payload = payload or {"note": "Oat milk", "expected_note": "Milk"}
    return {
        "id": uid(n),
        "action": "shopping_update",
        "payload": {"item_id": "item-1", **payload},
    }


def stored(hass_storage: dict) -> dict[str, Any]:
    return hass_storage[KEY]["data"]


@pytest.fixture
def client() -> SimpleNamespace:
    return SimpleNamespace(
        shopping_item=AsyncMock(return_value={"id": "item-1", "checked": False}),
        mutate=AsyncMock(return_value={"id": "item-1", "checked": True}),
        set_shopping_item_checked=AsyncMock(return_value={"id": "item-1", "checked": True}),
        update_shopping_item=AsyncMock(return_value={"id": "item-1", "note": "Oat milk"}),
        shopping_items=AsyncMock(
            return_value=[
                {"id": "item-1", "checked": True},
                {"id": "item-2", "checked": True},
                {"id": "item-3", "checked": False},
            ]
        ),
    )


@pytest.fixture
async def ledger(hass: HomeAssistant, client) -> OfflineLedger:
    return await OfflineLedger.async_load(hass, ENTRY_ID, client)


async def code_of(awaitable) -> str:
    with pytest.raises(AtelierError) as caught:
        await awaitable
    return caught.value.code


# Validation (H.1)


@pytest.mark.parametrize(
    "operation",
    [
        None,
        [],
        {**toggle(), "id": "not-a-uuid"},
        {**toggle(), "id": uid(1).replace("-4c0e-", "-9c0e-")},
        {**toggle(), "action": "shopping_rename"},
        {**delete(), "payload": {"item_id": "bad id!"}},
        {**clear(), "payload": {"list_id": "", "expected_ids": ["item-1"]}},
        {**clear(), "payload": {"list_id": "list-1"}},
        {**clear(), "payload": {"list_id": "list-1", "expected_ids": []}},
        {**clear(), "payload": {"list_id": "list-1", "expected_ids": ["bad id!"]}},
        {**update(), "payload": {"item_id": "item-1", "name": "Sugar"}},
        {**update(), "payload": {"item_id": "item-1", "name": " ", "expected_name": "A"}},
        {
            **update(),
            "payload": {"item_id": "item-1", "name": "S", "expected_name": "A", "note": "x"},
        },
        {**update(), "payload": {"item_id": "item-1"}},
        {**update(), "payload": {"item_id": "item-1", "note": "Oat milk"}},
        {**update(), "payload": {"item_id": "item-1", "note": "x" * 251, "expected_note": "M"}},
        {**update(), "payload": {"item_id": "item-1", "note": "x", "expected_note": 5}},
        {**update(), "payload": {"item_id": "item-1", "quantity": 2}},
        {**update(), "payload": {"item_id": "item-1", "quantity": True, "expected_quantity": 1}},
        {**update(), "payload": {"item_id": "item-1", "quantity": -1, "expected_quantity": 1}},
        {
            **update(),
            "payload": {"item_id": "item-1", "quantity": float("inf"), "expected_quantity": 1},
        },
        {**toggle(), "payload": None},
        {**toggle(), "payload": {"item_id": "x", "checked": 1, "expected_checked": False}},
        {**toggle(), "payload": {"item_id": "x", "checked": True}},
        {**toggle(), "payload": {"item_id": "bad id!", "checked": True, "expected_checked": False}},
        {**add(), "payload": {"list_id": "l", "note": "   "}},
        {**add(), "payload": {"list_id": "l", "note": "x" * 251}},
        {**add(), "payload": {"list_id": "", "note": "Milk"}},
        {**add(), "payload": {"list_id": "l", "note": 5}},
    ],
)
async def test_invalid_operations_are_rejected_before_any_state(ledger, client, operation, hass):
    assert await code_of(ledger.process(operation)) == "invalid_request"
    assert ledger.data == {}
    client.shopping_item.assert_not_awaited()
    client.mutate.assert_not_awaited()


def test_validation_keeps_only_known_payload_keys():
    op = toggle()
    op["payload"]["extra"] = "ignored"
    assert validate_operation(op) == toggle()
    assert validate_operation(add(note="x" * 250))["payload"]["note"] == "x" * 250


# Toggle


async def test_toggle_applies_and_persists_completed(ledger, client, hass_storage):
    result = await ledger.process(toggle())
    assert result == {"ok": True, "result": {"id": "item-1", "checked": True}}
    client.set_shopping_item_checked.assert_awaited_once_with(
        "item-1", True, {"id": "item-1", "checked": False}
    )
    client.mutate.assert_not_awaited()
    record = stored(hass_storage)[uid(1)]
    assert record["state"] == "completed"
    assert record["result"] == result


async def test_completed_replay_returns_stored_result_without_a_second_write(
    ledger, client, hass_storage
):
    first = await ledger.process(toggle())
    again = await ledger.process(toggle())
    assert again == {**first, "replayed": True}
    client.set_shopping_item_checked.assert_awaited_once()
    assert client.shopping_item.await_count == 1
    assert "replayed" not in stored(hass_storage)[uid(1)]["result"]


async def test_toggle_already_applied_is_completed_without_a_write(ledger, client, hass_storage):
    client.shopping_item.return_value = {"id": "item-1", "checked": True}
    result = await ledger.process(toggle())
    assert result == {
        "ok": True,
        "already_applied": True,
        "item": {"id": "item-1", "checked": True},
    }
    client.set_shopping_item_checked.assert_not_awaited()
    assert stored(hass_storage)[uid(1)]["state"] == "completed"


async def test_toggle_conflict_is_recorded_and_sticky(ledger, client, hass_storage):
    # Mealie has False; the user wants True but saw True, so Mealie moved since then.
    op = toggle(checked=True, expected=True)
    assert await code_of(ledger.process(op)) == "offline_conflict"
    client.set_shopping_item_checked.assert_not_awaited()
    assert stored(hass_storage)[uid(1)]["state"] == "conflict"
    # Replaying the same operation keeps failing without touching Mealie again.
    client.shopping_item.reset_mock()
    assert await code_of(ledger.process(op)) == "offline_conflict"
    client.shopping_item.assert_not_awaited()


async def test_toggle_read_failure_goes_back_to_checking_and_reraises(ledger, client, hass_storage):
    client.shopping_item.side_effect = MealieError("down")
    with pytest.raises(MealieError):
        await ledger.process(toggle())
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    client.set_shopping_item_checked.assert_not_awaited()
    # A later replay retries cleanly.
    client.shopping_item.side_effect = None
    client.shopping_item.return_value = {"id": "item-1", "checked": False}
    assert (await ledger.process(toggle()))["ok"]


async def test_toggle_invalid_item_from_mealie_is_a_mealie_error(ledger, client):
    client.shopping_item.return_value = {"id": "item-1"}
    assert await code_of(ledger.process(toggle())) == "mealie_error"


async def test_toggle_write_failure_returns_to_checking_and_reraises(ledger, client, hass_storage):
    client.set_shopping_item_checked.side_effect = MealieError("boom")
    with pytest.raises(MealieError):
        await ledger.process(toggle())
    record = stored(hass_storage)[uid(1)]
    assert record["state"] == "checking"
    assert record["message"] == "boom"
    client.set_shopping_item_checked.side_effect = None
    assert (await ledger.process(toggle()))["ok"]


async def test_toggle_stuck_in_sending_is_rechecked_not_blocked(ledger, client, hass_storage):
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {
            uid(1): {"operation": toggle(), "state": "sending", "updated_at": time.time()},
        },
    }
    ledger = await OfflineLedger.async_load(ledger._store.hass, ENTRY_ID, client)
    client.shopping_item.return_value = {"id": "item-1", "checked": True}
    assert (await ledger.process(toggle()))["already_applied"] is True


# Update, delete and clear ticked


async def test_update_writes_the_compared_item_and_completes(ledger, client, hass_storage):
    client.shopping_item.return_value = {"id": "item-1", "note": "Milk", "quantity": 1}
    op = update(note="Oat milk", expected_note="Milk", quantity=2, expected_quantity=1)
    result = await ledger.process(op)
    assert result == {"ok": True, "result": {"id": "item-1", "note": "Oat milk"}}
    client.update_shopping_item.assert_awaited_once_with(
        "item-1",
        {"note": "Oat milk", "quantity": 2},
        {"id": "item-1", "note": "Milk", "quantity": 1},
    )
    assert stored(hass_storage)[uid(1)]["state"] == "completed"
    assert (await ledger.process(op))["replayed"] is True
    client.update_shopping_item.assert_awaited_once()


async def test_update_already_applied_skips_the_write(ledger, client):
    client.shopping_item.return_value = {"id": "item-1", "note": "Oat milk"}
    result = await ledger.process(update())
    assert result["already_applied"] is True
    client.update_shopping_item.assert_not_awaited()


async def test_update_conflict_when_another_device_changed_it(ledger, client, hass_storage):
    client.shopping_item.return_value = {"id": "item-1", "note": "Soy milk"}
    assert await code_of(ledger.process(update())) == "offline_conflict"
    assert stored(hass_storage)[uid(1)]["state"] == "conflict"
    client.update_shopping_item.assert_not_awaited()


async def test_update_of_a_deleted_item_reports_the_same_code_on_replay(
    ledger, client, hass_storage
):
    client.shopping_item.side_effect = MealieError("gone", status=404)
    assert await code_of(ledger.process(update())) == "shopping_item_not_found"
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    assert await code_of(ledger.process(update())) == "shopping_item_not_found"


async def test_update_read_and_write_failures_stay_retryable(ledger, client, hass_storage):
    client.shopping_item.side_effect = MealieError("down", status=500)
    with pytest.raises(MealieError):
        await ledger.process(update())
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    client.shopping_item.side_effect = None
    client.shopping_item.return_value = {"id": "item-1", "note": "Milk"}
    client.update_shopping_item.side_effect = MealieError("boom")
    with pytest.raises(MealieError):
        await ledger.process(update())
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    client.update_shopping_item.side_effect = None
    assert (await ledger.process(update()))["ok"]


async def test_delete_calls_mealie_once_and_replays(ledger, client, hass_storage):
    client.mutate.return_value = None
    result = await ledger.process(delete())
    assert result == {"ok": True, "result": None}
    client.mutate.assert_awaited_once_with("shopping_delete", {"item_id": "item-1"})
    assert (await ledger.process(delete()))["replayed"] is True
    client.mutate.assert_awaited_once()


async def test_delete_of_a_missing_item_is_already_applied(ledger, client, hass_storage):
    client.mutate.return_value = {"ok": True, "already_applied": True}
    result = await ledger.process(delete())
    assert result == {"ok": True, "already_applied": True}
    assert stored(hass_storage)[uid(1)]["state"] == "completed"


async def test_delete_failure_is_retryable_not_uncertain(ledger, client, hass_storage):
    client.mutate.side_effect = MealieError("down", status=500)
    with pytest.raises(MealieError):
        await ledger.process(delete())
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    client.mutate.side_effect = None
    assert (await ledger.process(delete()))["ok"]


async def test_clear_checked_is_retryable_after_a_crash(ledger, client, hass_storage):
    client.mutate.side_effect = RuntimeError("process died")
    with pytest.raises(RuntimeError):
        await ledger.process(clear())
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    client.mutate.side_effect = None
    client.mutate.return_value = {"deleted": 2}
    assert await ledger.process(clear()) == {"ok": True, "result": {"deleted": 2}}
    client.mutate.assert_awaited_with(
        "shopping_clear_checked", {"list_id": "list-1", "expected_ids": ["item-1", "item-2"]}
    )


async def test_clear_checked_conflicts_on_an_item_ticked_elsewhere(ledger, client, hass_storage):
    client.shopping_items.return_value = [
        {"id": "item-1", "checked": True},
        {"id": "item-2", "checked": True},
        {"id": "item-4", "checked": True},
    ]
    assert await code_of(ledger.process(clear())) == "offline_conflict"
    assert stored(hass_storage)[uid(1)]["state"] == "conflict"
    client.mutate.assert_not_awaited()


async def test_clear_checked_conflicts_on_an_item_unticked_elsewhere(ledger, client):
    client.shopping_items.return_value = [
        {"id": "item-1", "checked": True},
        {"id": "item-2", "checked": False},
    ]
    assert await code_of(ledger.process(clear())) == "offline_conflict"
    client.mutate.assert_not_awaited()


async def test_clear_checked_ignores_seen_items_deleted_meanwhile(ledger, client):
    client.shopping_items.return_value = [{"id": "item-1", "checked": True}]
    client.mutate.return_value = {"deleted": 1}
    assert (await ledger.process(clear()))["ok"]
    client.mutate.assert_awaited_once()


async def test_clear_checked_read_failure_stays_retryable(ledger, client, hass_storage):
    client.shopping_items.side_effect = MealieError("down", status=500)
    with pytest.raises(MealieError):
        await ledger.process(clear())
    assert stored(hass_storage)[uid(1)]["state"] == "checking"
    client.mutate.assert_not_awaited()


FOOD_ITEM = {
    "id": "item-1",
    "note": "",
    "display": "2 g Zucker",
    "isFood": True,
    "food": {"name": "Zucker"},
    "quantity": 2,
}


async def test_rename_of_a_food_item_is_written_after_the_name_check(ledger, client):
    client.shopping_item.return_value = FOOD_ITEM
    op = update(name="Rohrzucker", expected_name="2 g Zucker")
    assert (await ledger.process(op))["ok"]
    client.update_shopping_item.assert_awaited_once_with(
        "item-1", {"name": "Rohrzucker"}, FOOD_ITEM
    )


async def test_rename_conflicts_when_the_name_changed_elsewhere(ledger, client):
    client.shopping_item.return_value = {**FOOD_ITEM, "display": "Brauner Zucker"}
    op = update(name="Rohrzucker", expected_name="2 g Zucker")
    assert await code_of(ledger.process(op)) == "offline_conflict"
    client.update_shopping_item.assert_not_awaited()


async def test_rename_already_applied_when_unlinked_with_that_name(ledger, client):
    client.shopping_item.return_value = {"id": "item-1", "note": "Rohrzucker", "isFood": False}
    op = update(name="Rohrzucker", expected_name="2 g Zucker")
    assert (await ledger.process(op))["already_applied"] is True
    client.update_shopping_item.assert_not_awaited()


async def test_removing_the_note_of_a_food_item_is_allowed(ledger, client):
    client.shopping_item.return_value = {**FOOD_ITEM, "note": "bio"}
    assert (await ledger.process(update(note="", expected_note="bio")))["ok"]
    client.update_shopping_item.assert_awaited_once()


async def test_emptying_the_note_of_a_plain_item_is_invalid(ledger, client, hass_storage):
    client.shopping_item.return_value = {"id": "item-1", "note": "Milk", "food": None}
    client.update_shopping_item.side_effect = AssertionError("must not write")
    # The real validation lives in apply_shopping_changes; the ledger reports it before writing.
    assert await code_of(ledger.process(update(note="", expected_note="Milk"))) == "invalid_request"
    assert stored(hass_storage)[uid(1)]["state"] == "checking"


async def test_stuck_sending_delete_is_rechecked_not_blocked(ledger, client, hass_storage):
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {uid(1): {"operation": delete(), "state": "sending", "updated_at": time.time()}},
    }
    ledger = await OfflineLedger.async_load(ledger._store.hass, ENTRY_ID, client)
    assert (await ledger.process(delete()))["ok"]


# Crash safety and add


async def test_sending_is_persisted_before_the_mealie_write(ledger, client, hass_storage):
    seen: list[str] = []

    async def crash(action, payload):
        seen.append(stored(hass_storage)[uid(1)]["state"])
        raise RuntimeError("process died")

    client.mutate.side_effect = crash
    with pytest.raises(RuntimeError):
        await ledger.process(add())
    assert seen == ["sending"]
    assert stored(hass_storage)[uid(1)]["state"] == "uncertain"


async def test_toggle_sending_is_persisted_before_the_mealie_write(ledger, client, hass_storage):
    seen: list[str] = []

    async def crash(*_args):
        seen.append(stored(hass_storage)[uid(1)]["state"])
        raise MealieError("x")

    client.set_shopping_item_checked.side_effect = crash
    with pytest.raises(MealieError):
        await ledger.process(toggle())
    assert seen == ["sending"]


async def test_add_completes_once_and_replays(ledger, client, hass_storage):
    client.mutate.return_value = {"id": "new"}
    assert await ledger.process(add()) == {"ok": True, "result": {"id": "new"}}
    assert await ledger.process(add()) == {"ok": True, "result": {"id": "new"}, "replayed": True}
    client.mutate.assert_awaited_once_with("shopping_add", {"list_id": "list-1", "note": "Milk"})


async def test_failed_add_is_uncertain_and_never_replayed(ledger, client, hass_storage):
    client.mutate.side_effect = MealieError("timeout")
    with pytest.raises(MealieError):
        await ledger.process(add())
    assert stored(hass_storage)[uid(1)]["state"] == "uncertain"
    client.mutate.reset_mock()
    client.mutate.side_effect = None
    assert await code_of(ledger.process(add())) == "offline_add_uncertain"
    client.mutate.assert_not_awaited()


async def test_add_left_in_sending_by_a_crash_is_uncertain_after_restart(
    hass, client, hass_storage
):
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {uid(1): {"operation": add(), "state": "sending", "updated_at": time.time()}},
    }
    ledger = await OfflineLedger.async_load(hass, ENTRY_ID, client)
    assert await code_of(ledger.process(add())) == "offline_add_uncertain"
    client.mutate.assert_not_awaited()


async def test_add_left_in_checking_is_sent(hass, client, hass_storage):
    hass_storage[KEY] = {
        "version": 1,
        "key": KEY,
        "data": {uid(1): {"operation": add(), "state": "checking", "updated_at": time.time()}},
    }
    ledger = await OfflineLedger.async_load(hass, ENTRY_ID, client)
    assert (await ledger.process(add()))["ok"]


async def test_operation_id_reuse_with_other_content_is_refused(ledger, client):
    await ledger.process(add(note="Milk"))
    assert await code_of(ledger.process(add(note="Bread"))) == "offline_op_reused"
    assert await code_of(ledger.process({**toggle(), "id": uid(1)})) == "offline_op_reused"
    client.mutate.assert_awaited_once()


# Pruning, cap, damaged store


def record(state="completed", age_days=0.0, now=None, operation=None) -> dict[str, Any]:
    return {
        "operation": operation or toggle(),
        "state": state,
        "updated_at": (now or time.time()) - age_days * DAY,
        "result": {"ok": True},
    }


async def test_old_completed_records_are_pruned_on_each_call(ledger, hass_storage):
    ledger.data.update(
        {
            "old": record(age_days=31),
            "fresh": record(age_days=29),
            "old-conflict": record("conflict", age_days=90),
            "old-checking": record("checking", age_days=90),
            "old-uncertain": record("uncertain", age_days=90),
            "old-sending": record("sending", age_days=90),
        }
    )
    await ledger.process(add(2))
    # Old toggles may be forgotten (a replay re-reads the item); a possibly sent add may not.
    assert set(stored(hass_storage)) == {"fresh", "old-uncertain", "old-sending", uid(2)}


def test_cap_drops_oldest_completed_first_and_keeps_open_records():
    now = time.time()
    data = {f"c{i}": record(age_days=i / 1000, now=now) for i in range(2000)}
    data["added"] = record(age_days=20, now=now, operation=add())
    data["open"] = record("uncertain", age_days=20, now=now)
    data["conflict"] = record("conflict", age_days=20, now=now)
    prune(data, now)
    assert len(data) == 2000
    assert "open" in data and "conflict" in data and "added" in data
    # The three oldest completed toggles are gone; a completed add only ages out.
    assert "c1999" not in data and "c1997" not in data and "c1996" in data


def test_cap_drops_old_toggles_after_completed_but_never_uncertain_adds():
    now = time.time()
    data = {f"k{i}": record("checking", age_days=i / 1000, now=now) for i in range(2001)}
    data["done"] = record(age_days=0, now=now)
    data["maybe-sent"] = record("uncertain", age_days=29, now=now)
    prune(data, now)
    assert len(data) == 2000
    assert "done" not in data and "maybe-sent" in data
    assert "k2000" not in data and "k1999" not in data and "k1998" in data


def test_cap_never_drops_records_that_need_attention():
    now = time.time()
    data = {f"u{i}": record("uncertain", now=now) for i in range(2005)}
    prune(data, now)
    assert len(data) == 2005


async def test_a_full_ledger_still_keeps_the_record_of_the_operation_in_hand(
    ledger, client, hass_storage
):
    ledger.data.update({f"u{i}": record("uncertain") for i in range(2000)})
    client.mutate.side_effect = MealieError("connection reset")
    with pytest.raises(MealieError):
        await ledger.process(add(7))
    assert stored(hass_storage)[uid(7)]["state"] == "uncertain"
    # The same operation again must not post a second time.
    assert await code_of(ledger.process(add(7))) == "offline_add_uncertain"
    assert client.mutate.await_count == 1


async def test_a_full_ledger_answers_a_replayed_add_without_posting_again(ledger, client):
    ledger.data.update({f"u{i}": record("uncertain", operation=add()) for i in range(2000)})
    first = await ledger.process(add(8))
    await ledger.process(toggle(9))
    # The answer to the add was lost; the card sends it again after another device wrote.
    again = await ledger.process(add(8))
    assert again == {**first, "replayed": True}
    assert client.mutate.await_count == 1


async def test_damaged_store_starts_empty(hass, client, hass_storage):
    hass_storage[KEY] = {"version": 1, "key": KEY, "data": {"x": "garbage"}}
    ledger = await OfflineLedger.async_load(hass, ENTRY_ID, client)
    assert ledger.data == {}
    assert (await ledger.process(add()))["ok"]


async def test_unreadable_store_starts_empty(hass, client, monkeypatch):
    async def boom(self):
        raise ValueError("corrupt")

    monkeypatch.setattr("homeassistant.helpers.storage.Store.async_load", boom)
    ledger = await OfflineLedger.async_load(hass, ENTRY_ID, client)
    assert ledger.data == {}


async def test_ledger_survives_a_reload(hass, client, hass_storage):
    first = await OfflineLedger.async_load(hass, ENTRY_ID, client)
    await first.process(add())
    second = await OfflineLedger.async_load(hass, ENTRY_ID, client)
    assert (await second.process(add()))["replayed"] is True
    client.mutate.assert_awaited_once()


# WebSocket command and entry lifecycle


async def send(client, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


def command(operation: dict[str, Any]) -> dict[str, Any]:
    return {"type": "mealie_atelier/offline_replay", "operation": operation}


@pytest.fixture
def wired(setup_integration):
    mealie = setup_integration.runtime_data.client
    mealie.shopping_item = AsyncMock(return_value={"id": "item-1", "checked": False})
    mealie.mutate = AsyncMock(return_value={"id": "item-1", "checked": True})
    mealie.set_shopping_item_checked = AsyncMock(return_value={"id": "item-1", "checked": True})
    return setup_integration


async def test_command_applies_replays_and_reports_codes(wired, hass_ws_client, hass_storage):
    client = await hass_ws_client()
    first = await send(client, command(toggle()))
    assert first["result"] == {"ok": True, "result": {"id": "item-1", "checked": True}}
    again = await send(client, command(toggle()))
    assert again["result"]["replayed"] is True
    reused = await send(client, command({**toggle(), "payload": toggle(checked=False)["payload"]}))
    assert reused["error"]["code"] == "offline_op_reused"
    invalid = await send(client, command({"id": "nope"}))
    assert invalid["error"]["code"] == "invalid_request"
    conflict = await send(client, command(toggle(2, checked=True, expected=True)))
    assert conflict["error"]["code"] == "offline_conflict"
    assert hass_storage[KEY]["data"][uid(2)]["state"] == "conflict"


async def test_command_maps_mealie_failures_and_flags_the_add(wired, hass_ws_client):
    wired.runtime_data.client.mutate.side_effect = MealieError("down")
    client = await hass_ws_client()
    assert (await send(client, command(add())))["error"]["code"] == "mealie_error"
    uncertain = await send(client, command(add()))
    assert uncertain["error"]["code"] == "offline_add_uncertain"


async def test_command_add_notifies_sync_once(wired, hass_ws_client):
    notify = wired.runtime_data.sync.notify_source_changed = MagicMock()
    client = await hass_ws_client()
    await send(client, command(add()))
    await send(client, command(add()))
    notify.assert_called_once_with("list-1", origin="after_atelier_add")


async def test_reader_is_refused_without_touching_mealie_or_the_ledger(
    hass, wired, hass_ws_client, hass_storage
):
    user = await hass.auth.async_create_user("Reader", group_ids=[GROUP_ID_USER])
    hass.config_entries.async_update_entry(wired, options={CONF_WRITE_USERS: ["someone-else"]})
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    client = await hass_ws_client(hass, hass.auth.async_create_access_token(refresh))
    response = await send(client, command(toggle()))
    assert response["error"]["code"] == "write_not_permitted"
    wired.runtime_data.client.shopping_item.assert_not_awaited()
    wired.runtime_data.client.set_shopping_item_checked.assert_not_awaited()
    assert KEY not in hass_storage


async def test_read_only_entry_refuses(hass, wired, hass_ws_client, hass_storage):
    hass.config_entries.async_update_entry(wired, options={CONF_READ_ONLY: True})
    response = await send(await hass_ws_client(), command(toggle()))
    assert response["error"]["code"] == "read_only"
    wired.runtime_data.client.set_shopping_item_checked.assert_not_awaited()
    assert KEY not in hass_storage


async def test_status_advertises_the_offline_queue(wired, hass_ws_client):
    response = await send(await hass_ws_client(), {"type": "mealie_atelier/status"})
    assert response["result"]["entries"][0]["features"]["offline_queue"] is True


async def test_damaged_store_file_never_fails_setup(
    hass, mock_config_entry, mock_mealie, hass_storage
):
    hass_storage[KEY] = {"version": 1, "key": KEY, "data": ["not", "a", "dict"]}
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.runtime_data.offline.data == {}


async def test_removing_the_entry_deletes_the_ledger_file(
    hass, wired, hass_ws_client, hass_storage
):
    await send(await hass_ws_client(), command(toggle()))
    assert KEY in hass_storage
    await hass.config_entries.async_remove(wired.entry_id)
    await hass.async_block_till_done()
    assert KEY not in hass_storage


def test_cap_tolerates_a_stored_operation_without_action():
    now = time.time()
    data = {f"u{i}": record("uncertain", now=now, operation=add()) for i in range(2000)}
    data["odd"] = record(now=now, operation={"payload": {}})
    prune(data, now)
    assert "odd" not in data and len(data) == 2000


async def test_status_advertises_shopping_edit(wired, hass_ws_client):
    response = await send(await hass_ws_client(), {"type": "mealie_atelier/status"})
    assert response["result"]["entries"][0]["features"]["shopping_edit"] is True


async def test_command_replays_a_delete_and_a_clear(wired, hass_ws_client, hass_storage):
    wired.runtime_data.client.mutate.return_value = {"deleted": 1}
    wired.runtime_data.client.shopping_items = AsyncMock(
        return_value=[{"id": "item-1", "checked": True}, {"id": "item-2", "checked": True}]
    )
    client = await hass_ws_client()
    deleted = await send(client, command(delete()))
    assert deleted["success"] and deleted["result"]["ok"] is True
    cleared = await send(client, command(clear(2)))
    assert cleared["result"]["result"] == {"deleted": 1}
    assert hass_storage[KEY]["data"][uid(2)]["state"] == "completed"

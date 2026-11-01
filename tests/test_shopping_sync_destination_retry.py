"""A destination that was unavailable is retried when it returns, with a bounded fallback."""

# ruff: noqa: F811

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.mealie_atelier.shopping_sync import SyncManager
from custom_components.mealie_atelier.shopping_sync.engine import DESTINATION_RETRY_BACKOFF

from .sync_support import FakeMealie, FakeTodo, mealie_item

# Fixtures are reused from the engine tests; the imports register them with pytest.
from .test_sync_engine import (  # noqa: F401
    activate,
    bring,
    mealie,
    save_rule,
    sync,
    todos,
)


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def waiting_rule(sync: SyncManager, mealie: FakeMealie, bring: FakeTodo) -> str:
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync)
    mealie.add(mealie_item("b", note="Brot"))
    bring.set_available(False)
    result = await sync.async_run(rule_id, "after_atelier_add")
    assert result["outcome"] == "destination_unavailable"
    return rule_id


async def test_destination_returning_triggers_one_run_that_sends(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    rule_id = await waiting_rule(sync, mealie, bring)
    assert rule_id in sync._dest_listeners

    bring.set_available(True)
    await hass.async_block_till_done()
    assert rule_id not in sync._dest_listeners
    await tick(hass, freezer, 31)

    assert bring.calls == ["1 Milch", "1 Brot"]
    assert sync.rules[rule_id]["last_run"]["outcome"] == "sent"
    # A successful run leaves no timer or attempt counter behind.
    assert not sync._dest_timers
    assert not sync._dest_attempts


async def test_state_change_that_stays_unavailable_does_not_run(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    rule_id = await waiting_rule(sync, mealie, bring)

    hass.states.async_set("todo.einkauf", "unknown")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert rule_id in sync._dest_listeners
    assert rule_id not in sync._scheduled
    assert bring.calls == ["1 Milch"]


async def test_paused_rule_does_not_arm(sync: SyncManager, mealie: FakeMealie, bring: FakeTodo):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id, _ = await activate(sync)
    await sync.async_set_paused(rule_id, True)
    bring.set_available(False)

    result = await sync.async_run(rule_id, "manual")

    assert result["outcome"] == "destination_unavailable"
    assert not sync._dest_listeners
    assert not sync._dest_timers


async def test_inactive_rule_does_not_arm(sync: SyncManager, mealie: FakeMealie, bring: FakeTodo):
    mealie.add(mealie_item("a", note="Milch"))
    rule_id = await save_rule(sync)
    bring.set_available(False)

    result = await sync.async_run(rule_id, "manual")

    assert result["outcome"] == "destination_unavailable"
    assert not sync._dest_listeners
    assert not sync._dest_timers


async def test_repeated_unavailable_runs_never_stack_listeners(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    rule_id = await waiting_rule(sync, mealie, bring)
    first = sync._dest_listeners[rule_id]

    await sync.async_run(rule_id, "manual")

    assert sync._dest_listeners == {rule_id: first}
    assert len(sync._dest_timers) == 1


async def test_stop_removes_listener_and_timer(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    rule_id = await waiting_rule(sync, mealie, bring)
    assert rule_id in sync._dest_timers

    await sync.async_stop()

    assert not sync._dest_listeners
    assert not sync._dest_timers
    assert not sync._dest_attempts


async def test_pause_delete_and_rule_change_disarm(
    sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    rule_id = await waiting_rule(sync, mealie, bring)
    await sync.async_set_paused(rule_id, True)
    assert not sync._dest_listeners
    assert not sync._dest_timers

    await sync.async_set_paused(rule_id, False)
    await sync.async_run(rule_id, "manual")
    assert rule_id in sync._dest_listeners
    await sync.async_delete_rule(rule_id)
    assert not sync._dest_listeners
    assert not sync._dest_timers


async def test_fallback_timer_fires_once_without_state_event(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    rule_id = await waiting_rule(sync, mealie, bring)
    # The entity comes back without a state event reaching the manager.
    sync._dest_listeners.pop(rule_id)()
    bring.set_available(True)
    await hass.async_block_till_done()
    assert rule_id not in sync._scheduled

    await tick(hass, freezer, DESTINATION_RETRY_BACKOFF[0] + 1)

    assert bring.calls == ["1 Milch", "1 Brot"]
    assert not sync._dest_timers
    assert not sync._dest_attempts
    await tick(hass, freezer, 3600)
    assert bring.calls == ["1 Milch", "1 Brot"]


async def test_fallback_backoff_is_bounded(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    rule_id = await waiting_rule(sync, mealie, bring)
    runs = 0

    for delay in DESTINATION_RETRY_BACKOFF:
        before = len(sync.store.data["audit"])
        await tick(hass, freezer, delay + 1)
        runs += len(sync.store.data["audit"]) - before
    assert runs == len(DESTINATION_RETRY_BACKOFF)
    assert not sync._dest_timers

    before = len(sync.store.data["audit"])
    await tick(hass, freezer, 7200)
    assert len(sync.store.data["audit"]) == before
    assert rule_id in sync._dest_listeners


async def restart(sync: SyncManager, hass: HomeAssistant) -> None:
    """Drop the in-memory retry state the way a Home Assistant restart does, then run startup."""
    for rule_id in list(sync.rules):
        sync._disarm_destination_retry(rule_id)
    sync._on_started(hass)
    await hass.async_block_till_done()


async def test_restart_rearms_waiting_rule_and_sends_when_destination_returns(
    hass: HomeAssistant,
    sync: SyncManager,
    mealie: FakeMealie,
    bring: FakeTodo,
    freezer: FrozenDateTimeFactory,
):
    rule_id = await waiting_rule(sync, mealie, bring)
    await restart(sync, hass)
    assert rule_id in sync._dest_listeners

    bring.set_available(True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 31)

    assert bring.calls == ["1 Milch", "1 Brot"]
    assert sync.rules[rule_id]["last_run"]["outcome"] == "sent"


async def test_restart_does_not_arm_paused_or_inactive_rules(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    rule_id = await waiting_rule(sync, mealie, bring)
    await sync.async_set_paused(rule_id, True)
    await restart(sync, hass)
    assert not sync._dest_listeners
    assert not sync._dest_timers

    await sync.async_set_paused(rule_id, False)
    sync._disarm_destination_retry(rule_id)
    sync.rules[rule_id]["activated"] = False
    await restart(sync, hass)
    assert not sync._dest_listeners
    assert not sync._dest_timers


async def test_restart_does_not_arm_rule_without_unavailable_outcome(
    hass: HomeAssistant, sync: SyncManager, mealie: FakeMealie, bring: FakeTodo
):
    mealie.add(mealie_item("a", note="Milch"))
    await activate(sync)
    await restart(sync, hass)
    assert not sync._dest_listeners

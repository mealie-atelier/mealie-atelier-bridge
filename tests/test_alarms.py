"""Kitchen timers: validation, persistence, ownership and the delivery scheduler."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.mealie_atelier.alarms import TimerManager

STORAGE_KEY = "mealie_atelier_alarms_e1"


def now_ms() -> int:
    return int(time.time() * 1000)


def timer(deadline_in_ms: int = 60_000, **overrides: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "label": "Pasta",
        "deadline": now_ms() + deadline_in_ms,
        **overrides,
    }


def stored_alarm(**overrides: Any) -> dict[str, Any]:
    deadline = overrides.pop("deadline", now_ms() - 5_000)
    return {
        "id": str(uuid4()),
        "label": "Pasta",
        "deadline": deadline,
        "owner": "user-1",
        "status": "scheduled",
        "attempts": 0,
        "channels": [],
        "createdAt": deadline - 60_000,
        "nextAttemptAt": deadline,
        **overrides,
    }


def prefill_storage(hass_storage: dict[str, Any], alarms: list[dict[str, Any]]) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"alarms": alarms},
    }


def with_clock(seconds: float):
    """Pin the clock the scheduler sees, without touching the global time module."""
    return patch(
        "custom_components.mealie_atelier.alarms.time", SimpleNamespace(time=lambda: seconds)
    )


@pytest.fixture
def manager(hass: HomeAssistant) -> TimerManager:
    return TimerManager(hass, "e1")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not-a-dict",
        {"id": "nope", "label": "Pasta", "deadline": 1},
        {"label": "Pasta", "deadline": 1},
    ],
)
async def test_create_rejects_malformed_timers(manager: TimerManager, bad: Any):
    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_create("user-1", bad)

    assert caught.value.translation_key == "invalid_timer"
    assert manager.alarms == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"label": "   "},
        {"label": "x" * 101},
        {"label": 5},
        {"deadline": "soon"},
        {"deadline": True},
        {"deadline_in_ms": 100},
        {"deadline_in_ms": 25 * 3600_000},
    ],
)
async def test_create_validates_label_and_deadline(manager: TimerManager, overrides: dict):
    overrides = dict(overrides)
    spec = timer(overrides.pop("deadline_in_ms", 60_000), **overrides)

    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_create("user-1", spec)

    assert caught.value.translation_key == "invalid_timer"


async def test_create_persists_the_timer(manager: TimerManager, hass_storage: dict[str, Any]):
    spec = timer(label="  Pasta  ")

    created = await manager.async_create("user-1", spec)

    assert created["label"] == "Pasta"
    assert created["status"] == "scheduled"
    assert created["nextAttemptAt"] == spec["deadline"]
    assert hass_storage[STORAGE_KEY]["data"]["alarms"] == [created]


async def test_create_is_idempotent_but_rejects_conflicting_reuse(manager: TimerManager):
    spec = timer()
    first = await manager.async_create("user-1", spec)

    assert await manager.async_create("user-1", spec) == first
    assert len(manager.alarms) == 1
    for clash in ({"label": "Soup"}, {"deadline": spec["deadline"] + 1000}):
        with pytest.raises(ServiceValidationError) as caught:
            await manager.async_create("user-1", {**spec, **clash})
        assert caught.value.translation_key == "timer_exists"
    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_create("user-2", spec)
    assert caught.value.translation_key == "timer_exists"


async def test_create_caps_the_number_of_active_timers(manager: TimerManager):
    manager.alarms = [
        stored_alarm(status="scheduled", owner=f"user-{i}", deadline=now_ms() + 60_000)
        for i in range(300)
    ]

    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_create("new-user", timer())

    assert caught.value.translation_key == "too_many_timers"


async def test_finished_timers_are_pruned_and_do_not_count_against_the_cap(
    manager: TimerManager, hass_storage: dict[str, Any]
):
    old = now_ms() - 3 * 24 * 3600_000
    manager.alarms = [
        stored_alarm(status=status, owner=f"user-{i}", deadline=old)
        for i, status in enumerate(["sent", "cancelled", "expired", "failed"] * 76)
    ]
    recent = stored_alarm(status="sent", deadline=now_ms() - 5_000)
    manager.alarms.append(recent)

    created = await manager.async_create("user-1", timer())

    assert [a["id"] for a in manager.alarms] == [recent["id"], created["id"]]
    assert [a["id"] for a in hass_storage[STORAGE_KEY]["data"]["alarms"]] == [
        recent["id"],
        created["id"],
    ]


async def test_finished_timers_do_not_count_against_the_cap_even_when_recent(
    manager: TimerManager,
):
    manager.alarms = [stored_alarm(status="sent", owner=f"user-{i}") for i in range(301)]

    await manager.async_create("user-1", timer())


async def test_start_prunes_old_finished_timers(hass: HomeAssistant, hass_storage: dict[str, Any]):
    old = stored_alarm(status="sent", deadline=now_ms() - 3 * 24 * 3600_000)
    keep = stored_alarm(status="scheduled", deadline=now_ms() + 60_000)
    prefill_storage(hass_storage, [old, keep])
    manager = TimerManager(hass, "e1")

    await manager.async_start()
    await manager.async_stop()

    assert [a["id"] for a in manager.alarms] == [keep["id"]]
    assert [a["id"] for a in hass_storage[STORAGE_KEY]["data"]["alarms"]] == [keep["id"]]


async def test_create_caps_active_timers_per_user(manager: TimerManager):
    for _ in range(20):
        await manager.async_create("user-1", timer())

    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_create("user-1", timer())
    assert caught.value.translation_key == "too_many_timers"

    await manager.async_create("user-2", timer())


async def test_cancel_rules(manager: TimerManager, hass_storage: dict[str, Any]):
    mine = await manager.async_create("user-1", timer())

    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_cancel("user-2", mine["id"])
    assert caught.value.translation_key == "timer_not_permitted"
    with pytest.raises(ServiceValidationError) as caught:
        await manager.async_cancel("user-1", str(uuid4()))
    assert caught.value.translation_key == "timer_not_found"

    cancelled = await manager.async_cancel("user-2", mine["id"], is_admin=True)
    assert cancelled["status"] == "cancelled"
    assert hass_storage[STORAGE_KEY]["data"]["alarms"][0]["status"] == "cancelled"


async def test_cancelling_a_timer_that_is_being_delivered_succeeds(manager: TimerManager):
    manager.alarms.append(stored_alarm(status="sending"))
    result = await manager.async_cancel("user-1", manager.alarms[-1]["id"])
    assert result["status"] == "cancelled"
    assert manager.alarms[-1]["status"] == "cancelled"


async def test_cancelling_a_finished_timer_succeeds_without_changing_it(manager: TimerManager):
    manager.alarms.append(stored_alarm(status="sent"))
    result = await manager.async_cancel("user-1", manager.alarms[-1]["id"])
    assert result["status"] == "sent"
    assert manager.alarms[-1]["status"] == "sent"


async def test_list_is_scoped_to_the_owner_unless_admin(manager: TimerManager):
    await manager.async_create("user-1", timer(label="Mine"))
    await manager.async_create("user-2", timer(label="Theirs"))

    assert [a["label"] for a in manager.list("user-1")["alarms"]] == ["Mine"]
    assert len(manager.list("user-1", admin=True)["alarms"]) == 2
    # Callers get copies, not the live scheduler state.
    manager.list("user-1")["alarms"][0]["label"] = "changed"
    assert manager.alarms[0]["label"] == "Mine"


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("notify.mobile_app_phone", "notify.mobile_app_phone"),
        ("mobile_app_phone", ""),
        ("notify.bad name", ""),
        ("notify.", ""),
        ("", ""),
        (None, ""),
    ],
)
async def test_notify_service_is_validated(hass: HomeAssistant, service: Any, expected: str):
    assert TimerManager(hass, "e1", service).notify_service == expected


async def test_tick_ignores_timers_that_are_not_due(hass: HomeAssistant, manager: TimerManager):
    calls = async_mock_service(hass, "persistent_notification", "create")
    await manager.async_create("user-1", timer())

    assert await manager.async_tick() is False
    assert calls == []


async def test_tick_delivers_through_persistent_notification_and_mobile_push(
    hass: HomeAssistant, hass_storage: dict[str, Any]
):
    manager = TimerManager(hass, "e1", "notify.mobile_app_phone")
    persistent = async_mock_service(hass, "persistent_notification", "create")
    push = async_mock_service(hass, "notify", "mobile_app_phone")
    alarm = stored_alarm()
    manager.alarms = [alarm]

    assert await manager.async_tick() is True

    assert alarm["status"] == "sent"
    assert alarm["attempts"] == 1
    assert alarm["channels"] == ["ha_persistent", "mobile_push_accepted"]
    assert persistent[0].data["notification_id"] == f"mealie_atelier_{alarm['id']}"
    assert "Pasta" in persistent[0].data["message"]
    assert push[0].data["data"] == {"tag": f"mealie-atelier-{alarm['id']}"}
    assert hass_storage[STORAGE_KEY]["data"]["alarms"][0]["status"] == "sent"
    assert await manager.async_tick() is False


async def test_tick_retries_with_backoff_then_fails(hass: HomeAssistant, manager: TimerManager):
    # No notification service is registered, so every delivery attempt yields no channel.
    alarm = stored_alarm()
    manager.alarms = [alarm]
    clock = alarm["deadline"] / 1000 + 10

    with with_clock(clock):
        assert await manager.async_tick() is True
    assert alarm["status"] == "retry"
    assert alarm["attempts"] == 1
    assert alarm["nextAttemptAt"] == int(clock * 1000) + 2000 * 2

    # Not due again before the backoff elapsed.
    with with_clock(clock + 1):
        assert await manager.async_tick() is False

    alarm["attempts"] = 9
    alarm["nextAttemptAt"] = 0
    with with_clock(clock + 600):
        assert await manager.async_tick() is True
    assert alarm["status"] == "failed"


async def test_tick_expires_timers_missed_for_over_a_day(
    hass: HomeAssistant, manager: TimerManager
):
    calls = async_mock_service(hass, "persistent_notification", "create")
    alarm = stored_alarm(deadline=now_ms() - 25 * 3600_000)
    manager.alarms = [alarm]

    assert await manager.async_tick() is False

    assert alarm["status"] == "expired"
    assert calls == []


async def test_scheduler_loads_stored_timers_and_delivers_due_ones(
    hass: HomeAssistant, hass_storage: dict[str, Any]
):
    due = stored_alarm(label="Due")
    later = stored_alarm(label="Later", deadline=now_ms() + 3600_000)
    prefill_storage(hass_storage, [due, later])
    calls = async_mock_service(hass, "persistent_notification", "create")
    manager = TimerManager(hass, "e1")

    await manager.async_start()
    try:
        assert [a["label"] for a in manager.alarms] == ["Due", "Later"]
        async with asyncio.timeout(5):
            while not calls:
                await asyncio.sleep(0.01)
    finally:
        await manager.async_stop()

    assert manager.task is None
    statuses = {a["label"]: a["status"] for a in manager.alarms}
    assert statuses == {"Due": "sent", "Later": "scheduled"}
    saved = {a["label"]: a["status"] for a in hass_storage[STORAGE_KEY]["data"]["alarms"]}
    assert saved == statuses


async def test_timers_survive_a_restart(hass: HomeAssistant, hass_storage: dict[str, Any]):
    first = TimerManager(hass, "e1")
    created = await first.async_create("user-1", timer(label="Roast", deadline_in_ms=3600_000))

    second = TimerManager(hass, "e1")
    await second.async_start()
    await second.async_stop()

    assert second.alarms == [created]


async def test_stop_without_start_is_harmless(manager: TimerManager):
    await manager.async_stop()

    assert manager.task is None


@pytest.mark.parametrize(
    ("language", "title", "message"),
    [
        ("de", "Mealie Atelier Bridge · Küchentimer", "Pasta ist fertig!"),
        ("de-CH", "Mealie Atelier Bridge · Küchentimer", "Pasta ist fertig!"),
        ("en", "Mealie Atelier Bridge · Kitchen timer", "Pasta is ready!"),
        ("fr", "Mealie Atelier Bridge · Kitchen timer", "Pasta is ready!"),
    ],
)
async def test_delivery_text_follows_the_home_assistant_language(
    hass: HomeAssistant, manager: TimerManager, language: str, title: str, message: str
):
    hass.config.language = language
    persistent = async_mock_service(hass, "persistent_notification", "create")
    manager.alarms = [stored_alarm()]

    assert await manager.async_tick() is True
    assert persistent[0].data["title"] == title
    assert persistent[0].data["message"] == message


async def test_notify_persistent_notification_does_not_duplicate(hass: HomeAssistant):
    manager = TimerManager(hass, "e1", "notify.persistent_notification")
    persistent = async_mock_service(hass, "persistent_notification", "create")
    duplicate = async_mock_service(hass, "notify", "persistent_notification")
    manager.alarms = [stored_alarm()]

    assert await manager.async_tick() is True
    assert len(persistent) == 1
    assert duplicate == []


def test_notify_send_message_is_ignored(hass: HomeAssistant):
    assert TimerManager(hass, "e1", "notify.send_message").notify_service == ""

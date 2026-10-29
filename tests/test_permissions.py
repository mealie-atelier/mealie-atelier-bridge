"""Per-user write grants: pure rules, enforcement on every mutating command, status payload."""

from __future__ import annotations

import inspect
import time
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components.websocket_api.const import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier import http
from custom_components.mealie_atelier.const import (
    ADMIN_ACTIONS,
    CONF_READ_ONLY,
    CONF_WRITE_USERS,
    WRITE_ACTIONS,
)
from custom_components.mealie_atelier.permissions import can_admin, can_write, deny_reason

from .conftest import ENTRY_ID

PREFIX = "mealie_atelier/"

# Every WebSocket command must be classified here. A new command that is missing from all of
# these lists fails test_every_registered_command_is_classified until it gets a gate and a row.
READ_COMMANDS = {
    "status",
    "recipes",
    "recipe",
    "mealplan",
    "shopping",
    "metadata",
    "nutrition_prepare",
    "nutrition_search",
    "nutrition_ai",
    "nutrition_calculate",
    "studio_state",
    "studio_substitute",
    "studio_ai_audit",
    "alarms",
    "alarm_capabilities",
    "foods",
    "units",
    "ingredients_get",
    "recipe_backups",
    "sync_state",
    "sync_destinations",
    "sync_preview",
    "sync_records",
}
WRITE_COMMANDS = {
    "mutate",
    "studio_update",
    "sync_run",
    "sync_pause",
    "sync_resume",
    "sync_retry",
    "sync_resolve",
    "offline_replay",
}
ADMIN_COMMANDS = {
    "mutate",
    "nutrition_save",
    "food_create",
    "unit_create",
    "ingredients_save",
    "recipe_restore",
    "upload_begin",
    "upload_chunk",
    "recipe_image_set",
    "recipe_image_delete",
    "step_image_add",
    "step_image_remove",
    "sync_rule_save",
    "sync_rule_delete",
    "sync_run",
}
PERSONAL_COMMANDS = {"studio_update", "alarm_create", "alarm_cancel"}

STUDIO_PANTRY = {"action": "pantry_put", "payload": {"name": "Rice", "amount": 100, "unit": "g"}}


def _mutate(action: str) -> dict[str, Any]:
    return {"type": f"{PREFIX}mutate", "action": action, "payload": {"slug": "gnocchi"}}


# (id, message) of every gated write or admin command; mutate rows come from the constants so a
# new action added there is covered automatically.
WRITE_MESSAGES = [pytest.param(_mutate(a), id=f"mutate:{a}") for a in sorted(WRITE_ACTIONS)] + [
    pytest.param({"type": f"{PREFIX}studio_update", **STUDIO_PANTRY}, id="studio_update:pantry"),
    pytest.param(
        {
            "type": f"{PREFIX}studio_update",
            "action": "profile_set",
            "payload": {"people": 2, "maxMinutes": 30},
        },
        id="studio_update:profile",
    ),
    pytest.param({"type": f"{PREFIX}sync_run", "rule_id": "r1"}, id="sync_run"),
    pytest.param({"type": f"{PREFIX}sync_pause", "rule_id": "r1"}, id="sync_pause"),
    pytest.param({"type": f"{PREFIX}sync_resume", "rule_id": "r1"}, id="sync_resume"),
    pytest.param({"type": f"{PREFIX}sync_retry", "rule_id": "r1"}, id="sync_retry"),
    pytest.param(
        {
            "type": f"{PREFIX}offline_replay",
            "operation": {
                "id": "6f1c1c1e-2f6a-4c0e-8d7b-3a1f5b9d2c10",
                "action": "shopping_toggle",
                "payload": {"item_id": "item-1", "checked": True, "expected_checked": False},
            },
        },
        id="offline_replay",
    ),
    pytest.param(
        {"type": f"{PREFIX}sync_resolve", "rule_id": "r1", "record_key": "k", "action": "dismiss"},
        id="sync_resolve",
    ),
]
ADMIN_MESSAGES = [pytest.param(_mutate(a), id=f"mutate:{a}") for a in sorted(ADMIN_ACTIONS)] + [
    pytest.param(
        {
            "type": f"{PREFIX}nutrition_save",
            "slug": "gnocchi",
            "expected_hash": "x",
            "entries": [],
        },
        id="nutrition_save",
    ),
    pytest.param({"type": f"{PREFIX}food_create", "name": "Leek"}, id="food_create"),
    pytest.param({"type": f"{PREFIX}unit_create", "name": "Pinch"}, id="unit_create"),
    pytest.param(
        {"type": f"{PREFIX}ingredients_save", "slug": "gnocchi", "expected_hash": "x", "rows": []},
        id="ingredients_save",
    ),
    pytest.param(
        {"type": f"{PREFIX}recipe_restore", "slug": "gnocchi", "backup_id": "b1"},
        id="recipe_restore",
    ),
    pytest.param(
        {
            "type": f"{PREFIX}upload_begin",
            "slug": "gnocchi",
            "purpose": "cover",
            "size": 10,
            "mime": "image/png",
            "sha256": "0" * 64,
        },
        id="upload_begin",
    ),
    pytest.param(
        {"type": f"{PREFIX}upload_chunk", "upload_id": "u1", "index": 0, "data": ""},
        id="upload_chunk",
    ),
    pytest.param(
        {
            "type": f"{PREFIX}recipe_image_set",
            "slug": "gnocchi",
            "upload_id": "u1",
            "expected_image": "x",
        },
        id="recipe_image_set",
    ),
    pytest.param(
        {"type": f"{PREFIX}recipe_image_delete", "slug": "gnocchi", "expected_image": "x"},
        id="recipe_image_delete",
    ),
    pytest.param(
        {"type": f"{PREFIX}step_image_add", "slug": "gnocchi", "step_index": 0, "upload_id": "u1"},
        id="step_image_add",
    ),
    pytest.param(
        {
            "type": f"{PREFIX}step_image_remove",
            "slug": "gnocchi",
            "step_index": 0,
            "file_name": "step.png",
        },
        id="step_image_remove",
    ),
    pytest.param({"type": f"{PREFIX}sync_rule_save", "rule": {}}, id="sync_rule_save"),
    pytest.param({"type": f"{PREFIX}sync_rule_delete", "rule_id": "r1"}, id="sync_rule_delete"),
    pytest.param(
        {"type": f"{PREFIX}sync_run", "rule_id": "r1", "confirm_first_batch": True},
        id="sync_run:confirm_first_batch",
    ),
]


GATE_CODES = {"read_only", "unauthorized", "write_not_permitted"}


def _passed_gate(response: dict) -> bool:
    """Allowed means the gate let it through; a later domain error (unknown rule) still counts."""
    return response["success"] or response["error"]["code"] not in GATE_CODES


def _types(params) -> set[str]:
    return {p.values[0]["type"].removeprefix(PREFIX) for p in params}


def test_every_write_and_admin_command_has_a_refusal_message():
    """A command listed as write or admin without a message row would silently skip the gate test."""
    assert _types(WRITE_MESSAGES) == WRITE_COMMANDS
    assert _types(ADMIN_MESSAGES) == ADMIN_COMMANDS
    mutate_write = {p.values[0]["action"] for p in WRITE_MESSAGES if "mutate" in p.id}
    mutate_admin = {p.values[0]["action"] for p in ADMIN_MESSAGES if "mutate" in p.id}
    assert mutate_write == set(WRITE_ACTIONS)
    assert mutate_admin == set(ADMIN_ACTIONS)


def _user(*, admin=False, system=False, user_id="u1"):
    return SimpleNamespace(is_admin=admin, system_generated=system, id=user_id)


def _entry(**options):
    return SimpleNamespace(options=options)


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.parametrize("grants", [[], ["u1"], ["other"]], ids=["empty", "listed", "unlisted"])
@pytest.mark.parametrize("read_only", [True, False])
@pytest.mark.parametrize("system", [True, False])
def test_can_write_truth_table(admin, grants, read_only, system):
    entry = _entry(**{CONF_WRITE_USERS: grants, CONF_READ_ONLY: read_only})
    expected = not read_only and not system and (admin or not grants or "u1" in grants)
    assert can_write(entry, _user(admin=admin, system=system)) is expected


def test_missing_option_and_no_user():
    assert can_write(_entry(), _user())
    assert not can_write(_entry(), None)
    assert not can_admin(_entry(), None)


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.parametrize("read_only", [True, False])
def test_can_admin_ignores_grants(admin, read_only):
    entry = _entry(**{CONF_WRITE_USERS: ["u1"], CONF_READ_ONLY: read_only})
    assert can_admin(entry, _user(admin=admin)) is (admin and not read_only)


def test_deny_reason_order():
    plain = _user()
    assert deny_reason(_entry(), plain, "write") is None
    assert deny_reason(_entry(), plain, "admin") == "unauthorized"
    assert deny_reason(_entry(**{CONF_WRITE_USERS: ["x"]}), plain, "write") == "write_not_permitted"
    assert deny_reason(_entry(**{CONF_WRITE_USERS: ["u1"]}), plain, "admin") == "unauthorized"
    # Read-only wins over every other reason, for admins too.
    locked = _entry(**{CONF_READ_ONLY: True, CONF_WRITE_USERS: ["x"]})
    assert deny_reason(locked, plain, "write") == "read_only"
    assert deny_reason(locked, _user(admin=True), "admin") == "read_only"


def test_http_views_do_not_write():
    """The HTTP surface is read-only; a view with a write verb needs a gate and a test here."""
    views = [
        cls
        for _, cls in inspect.getmembers(http, inspect.isclass)
        if cls.__module__ == http.__name__ and hasattr(cls, "url")
    ]
    assert views
    for view in views:
        for verb in ("post", "put", "patch", "delete"):
            assert not hasattr(view, verb), f"{view.__name__}.{verb} needs a permission gate"


async def _token(hass: HomeAssistant, name: str, *, admin=False):
    group = GROUP_ID_ADMIN if admin else GROUP_ID_USER
    user = await hass.auth.async_create_user(name, group_ids=[group])
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return user, hass.auth.async_create_access_token(refresh)


async def _send(client, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


@pytest.fixture
async def guest(hass: HomeAssistant, setup_integration, hass_ws_client: WebSocketGenerator):
    """A non-admin user who is not on the grant list while a grant list exists."""
    user, token = await _token(hass, "Guest")
    _, granted_token = await _token(hass, "Cook")
    granted = [u.id for u in await hass.auth.async_get_users() if u.name == "Cook"]
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: granted})
    return await hass_ws_client(hass, token)


async def test_every_registered_command_is_classified(hass, setup_integration):
    registered = {
        n.removeprefix(PREFIX) for n in hass.data[WEBSOCKET_DOMAIN] if n.startswith(PREFIX)
    }
    classified = READ_COMMANDS | WRITE_COMMANDS | ADMIN_COMMANDS | PERSONAL_COMMANDS
    assert registered == classified


@pytest.mark.parametrize("message", WRITE_MESSAGES)
async def test_non_granted_user_is_refused_on_every_write_command(
    guest, setup_integration, aioclient_mock: AiohttpClientMocker, hass_storage, message
):
    calls = aioclient_mock.call_count
    response = await _send(guest, message)
    assert response["error"]["code"] == "write_not_permitted"
    assert aioclient_mock.call_count == calls
    assert f"mealie_atelier_studio_{ENTRY_ID}" not in hass_storage


@pytest.mark.parametrize("message", ADMIN_MESSAGES)
async def test_non_admin_is_refused_on_every_admin_command(
    guest, setup_integration, aioclient_mock: AiohttpClientMocker, message
):
    calls = aioclient_mock.call_count
    response = await _send(guest, message)
    assert response["error"]["code"] == "unauthorized"
    assert aioclient_mock.call_count == calls


@pytest.mark.parametrize("message", ADMIN_MESSAGES)
async def test_granted_user_still_cannot_use_admin_commands(
    hass, setup_integration, hass_ws_client, aioclient_mock, message
):
    user, token = await _token(hass, "Cook")
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: [user.id]})
    calls = aioclient_mock.call_count
    response = await _send(await hass_ws_client(hass, token), message)
    assert response["error"]["code"] == "unauthorized"
    assert aioclient_mock.call_count == calls


async def test_unknown_mutate_action_is_invalid_request_for_everyone(guest, setup_integration):
    response = await _send(guest, _mutate("no_such_action"))
    assert response["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("message", WRITE_MESSAGES)
async def test_granted_user_and_admin_are_allowed(hass, setup_integration, hass_ws_client, message):
    setup_integration.runtime_data.client.mutate = AsyncMock(return_value={"ok": True})
    user, token = await _token(hass, "Cook")
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: [user.id]})
    for client in (await hass_ws_client(hass, token), await hass_ws_client()):
        assert _passed_gate(await _send(client, message))


async def test_granted_user_cannot_edit_recipes(hass, setup_integration, hass_ws_client):
    mutate = AsyncMock(return_value={"ok": True})
    setup_integration.runtime_data.client.mutate = mutate
    user, token = await _token(hass, "Cook")
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: [user.id]})
    client = await hass_ws_client(hass, token)
    assert (await _send(client, _mutate("shopping_toggle")))["success"]
    response = await _send(client, _mutate("recipe_update"))
    assert response["error"]["code"] == "unauthorized"
    mutate.assert_awaited_once()


@pytest.mark.parametrize("message", WRITE_MESSAGES)
async def test_empty_grant_list_lets_every_user_write(
    hass, setup_integration, hass_ws_client, message
):
    setup_integration.runtime_data.client.mutate = AsyncMock(return_value={"ok": True})
    _, token = await _token(hass, "Guest")
    client = await hass_ws_client(hass, token)
    assert _passed_gate(await _send(client, message))
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: []})
    assert _passed_gate(await _send(client, message))


@pytest.mark.parametrize("message", [*WRITE_MESSAGES, *ADMIN_MESSAGES])
async def test_read_only_wins_over_grants_and_admin(
    hass, setup_integration, hass_ws_client, aioclient_mock, message
):
    user, token = await _token(hass, "Cook")
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_READ_ONLY: True, CONF_WRITE_USERS: [user.id]}
    )
    calls = aioclient_mock.call_count
    for client in (await hass_ws_client(hass, token), await hass_ws_client()):
        assert (await _send(client, message))["error"]["code"] == "read_only"
    assert aioclient_mock.call_count == calls


async def test_family_member_stays_admin_only_for_granted_user(
    hass, setup_integration, hass_ws_client
):
    user, token = await _token(hass, "Cook")
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: [user.id]})
    response = await _send(
        await hass_ws_client(hass, token),
        {"type": f"{PREFIX}studio_update", "action": "family_member", "payload": {"name": "Kid"}},
    )
    assert response["error"]["code"] == "unauthorized"


def _timer() -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "label": "Pasta", "deadline": int(time.time() * 1000) + 60_000}


async def test_personal_actions_work_without_grant_but_not_in_read_only(
    guest, hass, setup_integration
):
    journal = {
        "type": f"{PREFIX}studio_update",
        "action": "journal_set",
        "payload": {"slug": "gnocchi", "note": "More sage", "rating": 5},
    }
    timer = _timer()
    assert (await _send(guest, journal))["success"]
    assert (await _send(guest, {"type": f"{PREFIX}alarm_create", "timer": timer}))["success"]
    assert (await _send(guest, {"type": f"{PREFIX}alarm_cancel", "timer_id": timer["id"]}))[
        "success"
    ]

    hass.config_entries.async_update_entry(setup_integration, options={CONF_READ_ONLY: True})
    assert (await _send(guest, journal))["error"]["code"] == "read_only"
    create = {"type": f"{PREFIX}alarm_create", "timer": _timer()}
    assert (await _send(guest, create))["error"]["code"] == "read_only"
    cancel = {"type": f"{PREFIX}alarm_cancel", "timer_id": timer["id"]}
    assert (await _send(guest, cancel))["error"]["code"] == "read_only"


async def test_status_reports_permissions_of_the_calling_user(
    hass, setup_integration: MockConfigEntry, hass_ws_client
):
    cook, cook_token = await _token(hass, "Cook")
    _, guest_token = await _token(hass, "Guest")
    hass.config_entries.async_update_entry(setup_integration, options={CONF_WRITE_USERS: [cook.id]})

    async def status(client):
        return (await _send(client, {"type": f"{PREFIX}status"}))["result"]["entries"][0]

    admin = await status(await hass_ws_client())
    assert admin["permissions"] == {"can_read": True, "can_write": True, "can_admin": True}
    granted = await status(await hass_ws_client(hass, cook_token))
    assert granted["permissions"] == {"can_read": True, "can_write": True, "can_admin": False}
    denied = await status(await hass_ws_client(hass, guest_token))
    assert denied["permissions"] == {"can_read": True, "can_write": False, "can_admin": False}
    for row in (admin, granted, denied):
        assert row["can_write"] is row["permissions"]["can_write"]
        assert row["can_manage"] is row["permissions"]["can_admin"]
        assert row["can_edit_ingredients"] is False
        assert set(row["features"]) == {
            "shopping_sync",
            "structured_ingredients",
            "uploads",
            "recipe_filters",
            "offline_queue",
            "ai",
        }
        assert row["limits"] == {"upload_max_bytes": 10485760, "upload_chunk_bytes": 262144}


async def test_status_read_only_clears_every_write_permission(
    hass, setup_integration, hass_ws_client
):
    hass.config_entries.async_update_entry(setup_integration, options={CONF_READ_ONLY: True})
    row = (await _send(await hass_ws_client(), {"type": f"{PREFIX}status"}))["result"]["entries"][0]
    assert row["permissions"] == {"can_read": True, "can_write": False, "can_admin": False}
    assert row["read_only"] is True
    assert not row["can_write"]
    assert not row["can_manage"]

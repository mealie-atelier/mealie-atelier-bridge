"""WebSocket commands of the shopping sync (`mealie_atelier/sync_*`)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..api import MealieAuthError, MealieError
from ..const import DOMAIN
from ..models import get_loaded_entry
from ..permissions import check
from .engine import SyncManager
from .model import STATES, SyncError

type Level = Literal["read", "write", "admin"]


def async_register_commands(hass: HomeAssistant) -> None:
    for command in (
        ws_sync_state,
        ws_sync_destinations,
        ws_sync_preview,
        ws_sync_rule_save,
        ws_sync_rule_delete,
        ws_sync_run,
        ws_sync_pause,
        ws_sync_resume,
        ws_sync_retry,
        ws_sync_resolve,
        ws_sync_records,
    ):
        websocket_api.async_register_command(hass, command)


def _error(connection, msg: dict, code: str, message: str) -> None:
    connection.send_error(
        msg["id"], code, message[:220], translation_key=code, translation_domain=DOMAIN
    )


async def _handle(
    hass: HomeAssistant,
    connection,
    msg: dict,
    level: Level,
    func: Callable[[SyncManager], Awaitable[Any]],
) -> None:
    entry = get_loaded_entry(hass, msg.get("entry_id"))
    if entry is None:
        _error(connection, msg, "not_configured", "Configure Mealie Atelier Bridge first")
        return
    # Gates are shared with every other command: read-only mode, grants and admin-only setup.
    if level != "read" and not check(connection, msg, entry, level):
        return
    try:
        result = await func(entry.runtime_data.sync)
    except SyncError as exc:
        _error(connection, msg, exc.code, str(exc))
    except (ValueError, TypeError) as exc:
        _error(connection, msg, "invalid_request", str(exc))
    except MealieAuthError:
        _error(connection, msg, "mealie_auth", "Mealie refused access: verify API token")
    except (MealieError, HomeAssistantError):
        _error(connection, msg, "mealie_error", "Mealie is unreachable or returned an error")
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {probatio.Required("type"): "mealie_atelier/sync_state", probatio.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_sync_state(hass, connection, msg):
    async def run(sync: SyncManager):
        return sync.state()

    await _handle(hass, connection, msg, "read", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_destinations",
        probatio.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_sync_destinations(hass, connection, msg):
    async def run(sync: SyncManager):
        return {"entities": sync.destinations()}

    await _handle(hass, connection, msg, "read", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_preview",
        probatio.Optional("entry_id"): str,
        probatio.Exclusive("rule_id", "target"): str,
        probatio.Exclusive("rule", "target"): dict,
    }
)
@websocket_api.async_response
async def ws_sync_preview(hass, connection, msg):
    async def run(sync: SyncManager):
        if "rule_id" in msg:
            return await sync.async_preview(rule_id=msg["rule_id"])
        if "rule" in msg:
            return await sync.async_preview(draft=msg["rule"])
        raise ValueError("Send rule_id or rule")

    await _handle(hass, connection, msg, "read", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_rule_save",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule"): dict,
        probatio.Optional("rule_id"): str,
        probatio.Optional("expected_revision"): int,
        probatio.Optional("reset_records", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_sync_rule_save(hass, connection, msg):
    async def run(sync: SyncManager):
        rule = await sync.async_save_rule(
            msg["rule"],
            rule_id=msg.get("rule_id"),
            expected_revision=msg.get("expected_revision"),
            reset_records=msg["reset_records"],
            user_id=connection.user.id,
        )
        return {"rule": rule}

    await _handle(hass, connection, msg, "admin", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_rule_delete",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
    }
)
@websocket_api.async_response
async def ws_sync_rule_delete(hass, connection, msg):
    async def run(sync: SyncManager):
        await sync.async_delete_rule(msg["rule_id"])
        return {"deleted": True}

    await _handle(hass, connection, msg, "admin", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_run",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
        probatio.Optional("confirm_first_batch", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_sync_run(hass, connection, msg):
    confirm = msg["confirm_first_batch"]

    async def run(sync: SyncManager):
        rule = sync.rules.get(msg["rule_id"])
        if rule is None:
            raise SyncError("sync_rule_not_found", "Unknown sync rule")
        if not rule.get("activated") and not confirm:
            raise SyncError("sync_not_activated", "Confirm the first batch before this rule runs")
        return await sync.async_run(msg["rule_id"], "manual", confirm_first_batch=confirm)

    # Confirming the first batch activates automatic syncing, which is part of the setup.
    await _handle(hass, connection, msg, "admin" if confirm else "write", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_pause",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
    }
)
@websocket_api.async_response
async def ws_sync_pause(hass, connection, msg):
    async def run(sync: SyncManager):
        return {"rule": await sync.async_set_paused(msg["rule_id"], True)}

    await _handle(hass, connection, msg, "write", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_resume",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
    }
)
@websocket_api.async_response
async def ws_sync_resume(hass, connection, msg):
    async def run(sync: SyncManager):
        return {"rule": await sync.async_set_paused(msg["rule_id"], False)}

    await _handle(hass, connection, msg, "write", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_retry",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
        probatio.Optional("record_keys"): probatio.All([str], probatio.Length(max=2000)),
    }
)
@websocket_api.async_response
async def ws_sync_retry(hass, connection, msg):
    async def run(sync: SyncManager):
        return await sync.async_retry(msg["rule_id"], msg.get("record_keys"))

    await _handle(hass, connection, msg, "write", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_resolve",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
        probatio.Required("record_key"): str,
        probatio.Required("action"): probatio.In(("dismiss", "resend")),
    }
)
@websocket_api.async_response
async def ws_sync_resolve(hass, connection, msg):
    async def run(sync: SyncManager):
        return await sync.async_resolve(msg["rule_id"], msg["record_key"], msg["action"])

    await _handle(hass, connection, msg, "write", run)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/sync_records",
        probatio.Optional("entry_id"): str,
        probatio.Required("rule_id"): str,
        probatio.Optional("states"): [probatio.In(STATES)],
        probatio.Optional("limit", default=50): probatio.All(int, probatio.Range(min=1, max=200)),
    }
)
@websocket_api.async_response
async def ws_sync_records(hass, connection, msg):
    async def run(sync: SyncManager):
        return {"records": sync.list_records(msg["rule_id"], msg.get("states"), msg["limit"])}

    await _handle(hass, connection, msg, "read", run)

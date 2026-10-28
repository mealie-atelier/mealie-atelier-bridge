"""Authenticated WebSocket API with allowlisted paths and constrained write actions."""

from __future__ import annotations

import logging
from copy import deepcopy

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MealieAuthError, MealieError, recipe_update_changes, safe_slug
from .const import (
    VERSION,
    ADMIN_ACTIONS,
    CONF_AI_KEY,
    CONF_EXPERIMENTAL_INGREDIENTS,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    DOMAIN,
    WRITE_ACTIONS,
)
from .nutrition import (
    ai_suggest,
    mealie_nutrition,
)
from .nutrition import (
    calculate as calculate_nutrition,
)
from .nutrition import (
    prepare as prepare_nutrition,
)
from .nutrition import (
    search as search_nutrition,
)
from .errors import AtelierError, send_atelier_error, structured_editor_required
from .models import MealieAtelierConfigEntry, get_loaded_entry
from .permissions import can_admin, can_write, check, check_personal
from .signing import sign_images as _sign_images
from .studio import apply as studio_apply
from .studio_ai import audit_recipe as ai_audit
from .studio_ai import substitutes as ai_substitutes

_LOGGER = logging.getLogger(__name__)

# Mutations that add items to a shopping list and may feed a shopping sync rule.
SYNC_SOURCE_ACTIONS = frozenset({"shopping_from_recipe", "shopping_add"})


# Advertised to cards through `status` as part of the shared status contract.
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 256 * 1024


def async_register_commands(hass: HomeAssistant) -> None:
    for command in (
        ws_status,
        ws_recipes,
        ws_recipe,
        ws_mealplan,
        ws_shopping,
        ws_metadata,
        ws_mutation,
        ws_nutrition_prepare,
        ws_nutrition_search,
        ws_nutrition_ai,
        ws_nutrition_calculate,
        ws_nutrition_save,
        ws_studio_state,
        ws_studio_update,
        ws_studio_substitute,
        ws_studio_ai_audit,
        ws_alarm_create,
        ws_alarm_cancel,
        ws_alarms,
        ws_alarm_capabilities,
    ):
        websocket_api.async_register_command(hass, command)


def _entry(hass: HomeAssistant, msg: dict) -> MealieAtelierConfigEntry | None:
    """Resolve the addressed loaded entry; without an entry_id use the first loaded one."""
    return get_loaded_entry(hass, msg.get("entry_id"))


def _translated(key: str) -> ServiceValidationError:
    """Build a translated validation error that the user can act on."""
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def _fail(connection, msg: dict, exc: Exception) -> None:
    """Return an error; no tokens, tracebacks or upstream URLs in frontend payload."""
    if isinstance(exc, AtelierError):
        send_atelier_error(connection, msg, exc)
        return
    if isinstance(exc, (ValueError, ServiceValidationError)):
        code = "invalid_request"
    elif isinstance(exc, MealieAuthError):
        code = "mealie_auth"
    else:
        code = "mealie_error"
    connection.send_error(msg["id"], code, str(exc)[:220])


async def _respond(hass, connection, msg, func) -> None:
    entry = _entry(hass, msg)
    if entry is None:
        connection.send_error(
            msg["id"], "not_configured", "Install and configure Mealie Atelier Bridge"
        )
        return
    try:
        result = await func(entry.runtime_data.client)
        connection.send_result(msg["id"], result)
    except (MealieError, ValueError, TypeError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command({probatio.Required("type"): "mealie_atelier/status"})
@websocket_api.async_response
async def ws_status(hass, connection, msg):
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        read_only = bool(entry.options.get(CONF_READ_ONLY, False))
        writer = can_write(entry, connection.user)
        admin = can_admin(entry, connection.user)
        structured = bool(entry.options.get(CONF_EXPERIMENTAL_INGREDIENTS, False)) and not read_only
        entries.append(
            {
                "entry_id": entry.entry_id,
                "name": entry.title,
                "connected": True,
                "read_only": read_only,
                "permissions": {"can_read": True, "can_write": writer, "can_admin": admin},
                "features": {
                    "shopping_sync": True,
                    "structured_ingredients": structured,
                    "uploads": True,
                    "recipe_filters": True,
                },
                "limits": {
                    "upload_max_bytes": UPLOAD_MAX_BYTES,
                    "upload_chunk_bytes": UPLOAD_CHUNK_BYTES,
                },
                # Legacy mirrors for cards that predate `permissions`.
                "can_write": writer,
                "can_manage": admin,
                "can_edit_ingredients": admin and structured,
            }
        )
    connection.send_result(msg["id"], {"entries": entries, "version": VERSION})


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/recipes",
        probatio.Optional("entry_id"): str,
        probatio.Optional("page", default=1): int,
        probatio.Optional("per_page", default=24): int,
        probatio.Optional("search", default=""): str,
    }
)
@websocket_api.async_response
async def ws_recipes(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Configure Mealie Atelier Bridge")
        return
    client = entry.runtime_data.client
    try:
        results = await client.recipes(msg["page"], msg["per_page"], msg["search"])
        connection.send_result(msg["id"], _sign_images(hass, entry.entry_id, results))
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/recipe",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
    }
)
@websocket_api.async_response
async def ws_recipe(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Configure Mealie Atelier Bridge")
        return
    client = entry.runtime_data.client
    try:
        result = await client.recipe(msg["slug"])
        connection.send_result(msg["id"], _sign_images(hass, entry.entry_id, result))
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/mealplan",
        probatio.Optional("entry_id"): str,
        probatio.Required("start"): str,
        probatio.Required("end"): str,
    }
)
@websocket_api.async_response
async def ws_mealplan(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Configure Mealie Atelier Bridge")
        return
    client = entry.runtime_data.client
    try:
        results = await client.mealplan(msg["start"], msg["end"])
        connection.send_result(msg["id"], _sign_images(hass, entry.entry_id, results))
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/shopping",
        probatio.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_shopping(hass, connection, msg):
    await _respond(hass, connection, msg, lambda c: c.shopping())


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/metadata",
        probatio.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_metadata(hass, connection, msg):
    await _respond(hass, connection, msg, lambda c: c.metadata())


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/mutate",
        probatio.Optional("entry_id"): str,
        probatio.Required("action"): str,
        probatio.Required("payload"): dict,
    }
)
@websocket_api.async_response
async def ws_mutation(hass, connection, msg):
    entry = _entry(hass, msg)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Configure Mealie Atelier Bridge")
        return
    action = msg["action"]
    # Unknown actions fail before any permission decision can reveal which actions exist.
    if action not in ADMIN_ACTIONS and action not in WRITE_ACTIONS:
        connection.send_error(msg["id"], "invalid_request", "Unsupported operation")
        return
    if not check(connection, msg, entry, "admin" if action in ADMIN_ACTIONS else "write"):
        return
    if action == "recipe_update" and "ingredients" in msg["payload"]:
        send_atelier_error(connection, msg, structured_editor_required())
        return

    if action == "recipe_update":
        # Invalid payloads fail before a snapshot, so they never rotate out real restore points.
        try:
            recipe_update_changes(msg["payload"])
        except ValueError as exc:
            _fail(connection, msg, exc)
            return

    async def run(client):
        if action == "recipe_update":
            # Snapshot first: a failed backup means no Mealie write at all.
            payload = msg["payload"]
            recipe = await client.recipe(payload.get("slug"))
            kind = "instructions" if set(payload) <= {"slug", "instructions"} else "basics"
            await entry.runtime_data.backups.async_snapshot(client, recipe, kind, connection.user)
        result = await client.mutate(action, msg["payload"])
        if action in SYNC_SOURCE_ACTIONS:
            # Mealie confirmed the write; the sync debounces on its own and never delays or
            # fails this response.
            try:
                entry.runtime_data.sync.notify_source_changed(
                    msg["payload"].get("list_id"), origin="after_atelier_add"
                )
            except Exception:
                _LOGGER.exception("Shopping sync could not be notified")
        return result

    await _respond(hass, connection, msg, run)


# Nutrition endpoints: per-ingredient validation and nutrition writes require HA admins.
def _nutrition_entry(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        raise _translated("not_configured")
    return entry.runtime_data.client, entry


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/nutrition_prepare",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
    }
)
@websocket_api.async_response
async def ws_nutrition_prepare(hass, connection, msg):
    try:
        client, entry = _nutrition_entry(hass, connection, msg)
        recipe = await client.recipe(msg["slug"])
        result = prepare_nutrition(
            recipe,
            fdc_enabled=bool(entry.options.get(CONF_FDC_KEY)),
            ai_enabled=bool(entry.options.get(CONF_AI_KEY)),
        )
        connection.send_result(msg["id"], result)
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/nutrition_search",
        probatio.Optional("entry_id"): str,
        probatio.Required("query"): str,
    }
)
@websocket_api.async_response
async def ws_nutrition_search(hass, connection, msg):
    try:
        _, entry = _nutrition_entry(hass, connection, msg)
        result = await search_nutrition(
            async_get_clientsession(hass), entry.options.get(CONF_FDC_KEY, ""), msg["query"]
        )
        connection.send_result(msg["id"], result)
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/nutrition_ai",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("expected_hash"): str,
    }
)
@websocket_api.async_response
async def ws_nutrition_ai(hass, connection, msg):
    try:
        client, entry = _nutrition_entry(hass, connection, msg)
        recipe = await client.recipe(msg["slug"])
        preview = prepare_nutrition(recipe, fdc_enabled=False, ai_enabled=True)
        if msg["expected_hash"] != preview["hash"]:
            raise _translated("recipe_changed")
        result = await ai_suggest(
            async_get_clientsession(hass),
            entry.options.get(CONF_AI_KEY, ""),
            [row["text"] for row in preview["ingredients"]],
        )
        connection.send_result(msg["id"], result)
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/nutrition_calculate",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("expected_hash"): str,
        probatio.Required("entries"): list,
    }
)
@websocket_api.async_response
async def ws_nutrition_calculate(hass, connection, msg):
    try:
        client, entry = _nutrition_entry(hass, connection, msg)
        recipe = await client.recipe(msg["slug"])
        result = await calculate_nutrition(
            async_get_clientsession(hass),
            entry.options.get(CONF_FDC_KEY, ""),
            recipe,
            msg["entries"],
            expected_hash=msg["expected_hash"],
        )
        connection.send_result(msg["id"], result)
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/nutrition_save",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("expected_hash"): str,
        probatio.Required("entries"): list,
        probatio.Optional("overwrite_existing", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_nutrition_save(hass, connection, msg):
    try:
        client, entry = _nutrition_entry(hass, connection, msg)
        if not check(connection, msg, entry, "admin"):
            return
        recipe = await client.recipe(msg["slug"])
        existing = recipe.get("nutrition") or {}
        if (
            isinstance(existing, dict)
            and any(x not in (None, "") for x in existing.values())
            and not msg["overwrite_existing"]
        ):
            raise _translated("existing_values_protected")
        result = await calculate_nutrition(
            async_get_clientsession(hass),
            entry.options.get(CONF_FDC_KEY, ""),
            recipe,
            msg["entries"],
            expected_hash=msg["expected_hash"],
        )
        changes = {
            "nutrition": mealie_nutrition(result),
            "settings": {**(recipe.get("settings") or {}), "showNutrition": True},
        }
        await client.request("PATCH", f"/api/recipes/{safe_slug(msg['slug'])}", data=changes)
        connection.send_result(msg["id"], {"saved": True, "report": result})
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


# Kitchen Intelligence: durable state stored in Home Assistant's .storage per Mealie entry.
@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/studio_state",
        probatio.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_studio_state(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Studio storage unavailable")
        return
    result = deepcopy(entry.runtime_data.studio.state)
    result["journal"] = result.get("journal", {}).get(connection.user.id, {})
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/studio_update",
        probatio.Optional("entry_id"): str,
        probatio.Required("action"): str,
        probatio.Required("payload"): dict,
        probatio.Optional("expected_revision"): int,
    }
)
@websocket_api.async_response
async def ws_studio_update(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Studio storage unavailable")
        return
    ctx = entry.runtime_data.studio
    action = msg["action"]
    if action == "journal_set":
        if not check_personal(connection, msg, entry):
            return
    elif not check(connection, msg, entry, "write"):
        return
    if action == "family_member" and not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin required to edit family members")
        return
    try:
        async with ctx.lock:
            current = ctx.state
            if "expected_revision" in msg and current["revision"] != msg["expected_revision"]:
                connection.send_error(
                    msg["id"], "conflict", "Another device changed the kitchen. Refresh first"
                )
                return
            scoped = deepcopy(current)
            if action == "journal_set":
                scoped["journal"] = deepcopy(current.get("journal", {}).get(connection.user.id, {}))
            payload = msg["payload"]
            if action == "session_task":
                session = current.get("family", {}).get("session")
                existing = next(
                    (
                        row
                        for row in (session or {}).get("tasks", [])
                        if row.get("id") == payload.get("id")
                    ),
                    None,
                )
                # Checking/unchecking an existing, previously approved task is allowed to
                # authenticated users; editing or approving tasks requires an HA admin.
                toggle = (
                    existing is not None
                    and set(payload) == {"session_id", "id", "done"}
                    and isinstance(payload.get("done"), bool)
                    and (
                        existing.get("adultApproved") is True
                        or not next(
                            (
                                m
                                for m in current["family"]["members"]
                                if m["id"] == existing["memberId"]
                            ),
                            {},
                        ).get("child", False)
                    )
                )
                if not connection.user.is_admin and not toggle:
                    connection.send_error(
                        msg["id"],
                        "unauthorized",
                        "Only admins can create, edit or approve helper tasks",
                    )
                    return
                if toggle:
                    payload = {
                        **existing,
                        "session_id": payload["session_id"],
                        "done": payload["done"],
                    }
            updated = studio_apply(scoped, action, payload)
            if action == "journal_set":
                updated["journal"] = {
                    **current.get("journal", {}),
                    connection.user.id: updated["journal"],
                }
            await ctx.store.async_save(updated)
            ctx.state = updated
        manager = entry.runtime_data.alarms
        warnings = []
        if manager and action in ("session_timer", "session_end"):
            old_timers = (
                current.get("family", {}).get("session", {}).get("timers", [])
                if current.get("family", {}).get("session")
                else []
            )
            new_timers = (
                updated.get("family", {}).get("session", {}).get("timers", [])
                if updated.get("family", {}).get("session")
                else []
            )
            for timer in new_timers:
                if not any(old["id"] == timer["id"] for old in old_timers):
                    try:
                        await manager.async_create(
                            connection.user.id,
                            {
                                "id": timer["id"],
                                "label": timer["name"],
                                "deadline": timer["deadline"],
                            },
                        )
                    except (ValueError, TypeError, HomeAssistantError) as exc:
                        warnings.append(str(exc))
            for timer in old_timers:
                if not any(new["id"] == timer["id"] for new in new_timers):
                    try:
                        await manager.async_cancel(
                            connection.user.id, timer["id"], connection.user.is_admin
                        )
                    except (ValueError, TypeError, HomeAssistantError):
                        pass
        result = deepcopy(updated)
        if warnings:
            result["alarmWarnings"] = warnings
        result["journal"] = result.get("journal", {}).get(connection.user.id, {})
        connection.send_result(msg["id"], result)
    except (ValueError, TypeError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/studio_substitute",
        probatio.Optional("entry_id"): str,
        probatio.Required("ingredient"): str,
        probatio.Required("recipe"): str,
    }
)
@websocket_api.async_response
async def ws_studio_substitute(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Studio unavailable")
        return
    try:
        result = await ai_substitutes(
            async_get_clientsession(hass),
            entry.options.get(CONF_AI_KEY, ""),
            msg["ingredient"],
            msg["recipe"],
        )
        connection.send_result(msg["id"], result)
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/studio_ai_audit",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
    }
)
@websocket_api.async_response
async def ws_studio_ai_audit(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Studio unavailable")
        return
    try:
        recipe = await entry.runtime_data.client.recipe(msg["slug"])
        result = await ai_audit(
            async_get_clientsession(hass), entry.options.get(CONF_AI_KEY, ""), recipe
        )
        connection.send_result(msg["id"], result)
    except (ValueError, MealieError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


# Persistent kitchen timers: server-owned, independent of browser tabs.
@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/alarm_capabilities",
        probatio.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_alarm_capabilities(hass, connection, msg):
    entry = _entry(hass, msg)
    connection.send_result(
        msg["id"],
        {
            "homeAssistant": bool(entry),
            "mobilePush": bool(entry and entry.options.get(CONF_TIMER_NOTIFY_SERVICE)),
            "webPush": False,
        },
    )


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/alarm_create",
        probatio.Optional("entry_id"): str,
        probatio.Required("timer"): dict,
    }
)
@websocket_api.async_response
async def ws_alarm_create(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Kitchen timer service unavailable")
        return
    if not check_personal(connection, msg, entry):
        return
    manager = entry.runtime_data.alarms
    try:
        connection.send_result(
            msg["id"], await manager.async_create(connection.user.id, msg["timer"])
        )
    except (TypeError, ValueError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/alarm_cancel",
        probatio.Optional("entry_id"): str,
        probatio.Required("timer_id"): str,
    }
)
@websocket_api.async_response
async def ws_alarm_cancel(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Kitchen timer service unavailable")
        return
    if not check_personal(connection, msg, entry):
        return
    manager = entry.runtime_data.alarms
    try:
        connection.send_result(
            msg["id"],
            await manager.async_cancel(
                connection.user.id, msg["timer_id"], connection.user.is_admin
            ),
        )
    except (TypeError, ValueError, HomeAssistantError) as exc:
        _fail(connection, msg, exc)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/alarms",
        probatio.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_alarms(hass, connection, msg):
    entry = _entry(hass, msg)
    if not entry:
        connection.send_error(msg["id"], "not_configured", "Kitchen timer service unavailable")
        return
    manager = entry.runtime_data.alarms
    connection.send_result(msg["id"], manager.list(connection.user.id, connection.user.is_admin))

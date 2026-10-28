"""Authenticated WebSocket API with allowlisted paths and constrained write actions."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import timedelta
from typing import Any

import probatio
from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MealieAuthError, MealieError, safe_id, safe_slug
from .const import (
    ADMIN_ACTIONS,
    CONF_AI_KEY,
    CONF_EXPERIMENTAL_INGREDIENTS,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    DOMAIN,
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
from .models import MealieAtelierConfigEntry, get_loaded_entry
from .studio import apply as studio_apply
from .studio_ai import audit_recipe as ai_audit
from .studio_ai import substitutes as ai_substitutes


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


def _sign_images(hass: HomeAssistant, entry_id: str, result: Any) -> Any:
    """Allow <img> display without placing Mealie API tokens in the browser."""

    def decorate(item: Any):
        if isinstance(item, list):
            return [decorate(row) for row in item]
        if not isinstance(item, dict):
            return item
        item = {**item}
        if item.get("image") and item.get("id"):
            path = f"/api/mealie_atelier/image/{entry_id}/{item['id']}"
            item["imageUrl"] = async_sign_path(hass, path, timedelta(hours=2))
        if isinstance(item.get("recipeInstructions"), list) and item.get("id"):
            recipe_id = safe_id(item["id"])
            instructions = []
            for raw_step in item["recipeInstructions"]:
                if not isinstance(raw_step, dict) or not isinstance(raw_step.get("text"), str):
                    instructions.append(raw_step)
                    continue
                step = {**raw_step}
                image_urls = []

                def replace_image(match, image_urls=image_urls):
                    tag = match.group(0)
                    source = re.search(
                        r"\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag, re.IGNORECASE
                    )
                    src = (
                        next((value for value in source.groups() if value is not None), "")
                        if source
                        else ""
                    )
                    prefix = f"/api/media/recipes/{item['id']}/assets/"
                    filename = src[len(prefix) :] if src.startswith(prefix) else ""
                    if not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename
                    ) or filename in (".", ".."):
                        return ""
                    asset_path = (
                        f"/api/mealie_atelier/recipe_asset/{entry_id}/{recipe_id}/{filename}"
                    )
                    image_urls.append(async_sign_path(hass, asset_path, timedelta(hours=2)))
                    return ""

                step["text"] = re.sub(
                    r"<img\b[^>]*>", replace_image, step["text"], flags=re.IGNORECASE
                ).strip()
                step["imageUrls"] = image_urls
                instructions.append(step)
            item["recipeInstructions"] = instructions
        if isinstance(item.get("recipe"), dict):
            item["recipe"] = decorate(item["recipe"])
        if isinstance(item.get("items"), list):
            item["items"] = decorate(item["items"])
        return item

    return decorate(result)


def _fail(connection, msg: dict, exc: Exception) -> None:
    """Return an error; no tokens, tracebacks or upstream URLs in frontend payload."""
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
    entries = [
        {
            "entry_id": entry.entry_id,
            "name": entry.title,
            "connected": True,
            "read_only": bool(entry.options.get(CONF_READ_ONLY, False)),
            "can_write": not entry.options.get(CONF_READ_ONLY, False),
            "can_manage": bool(connection.user.is_admin)
            and not entry.options.get(CONF_READ_ONLY, False),
            "can_edit_ingredients": False,  # No safe WS write command exposed until HA path is reviewed against live Mealie.
        }
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
    ]
    connection.send_result(msg["id"], {"entries": entries, "version": "0.9.5"})


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
    if bool(entry.options.get(CONF_READ_ONLY, False)):
        connection.send_error(msg["id"], "read_only", "Mealie Atelier is in read-only mode")
        return
    action = msg["action"]
    if (
        action == "recipe_update"
        and "ingredients" in msg["payload"]
        and not entry.options.get(CONF_EXPERIMENTAL_INGREDIENTS, False)
    ):
        connection.send_error(
            msg["id"],
            "disabled",
            "Ingredient editing is experimental and disabled in integration options",
        )
        return
    if action in ADMIN_ACTIONS and not connection.user.is_admin:
        connection.send_error(
            msg["id"], "unauthorized", "Only Home Assistant admins can manage recipes"
        )
        return
    await _respond(hass, connection, msg, lambda c: c.mutate(action, msg["payload"]))


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
        if not connection.user.is_admin:
            connection.send_error(
                msg["id"], "unauthorized", "Only Home Assistant admins may update nutrition"
            )
            return
        client, entry = _nutrition_entry(hass, connection, msg)
        if entry.options.get(CONF_READ_ONLY, False):
            connection.send_error(msg["id"], "read_only", "Mealie Atelier is read-only")
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
    if entry.options.get(CONF_READ_ONLY, False):
        connection.send_error(msg["id"], "read_only", "Kitchen is read-only")
        return
    action = msg["action"]
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

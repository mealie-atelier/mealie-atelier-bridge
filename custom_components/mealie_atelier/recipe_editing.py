"""WebSocket commands for structured recipe editing, backups and photos."""

from __future__ import annotations

import copy
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import MealieAuthError, MealieClient, MealieError, safe_id, safe_slug
from .const import CONF_EXPERIMENTAL_INGREDIENTS, DOMAIN
from .errors import AtelierError, send_atelier_error
from .models import MealieAtelierConfigEntry, get_loaded_entry
from .permissions import check
from .recipe_safety import fingerprint, merge, preview, verified
from .signing import sign_images

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$", re.ASCII)
_Search = probatio.All(str, probatio.Length(max=80))


def async_register_commands(hass: HomeAssistant) -> None:
    for command in (
        ws_foods,
        ws_units,
        ws_food_create,
        ws_unit_create,
        ws_ingredients_get,
        ws_ingredients_save,
        ws_recipe_backups,
        ws_recipe_restore,
        ws_upload_begin,
        ws_upload_chunk,
        ws_recipe_image_set,
        ws_recipe_image_delete,
        ws_step_image_add,
        ws_step_image_remove,
    ):
        websocket_api.async_register_command(hass, command)


def _send_failure(connection, msg: dict, exc: Exception) -> None:
    """Return an error without tokens, tracebacks or upstream URLs."""
    if isinstance(exc, AtelierError):
        send_atelier_error(connection, msg, exc)
        return
    if isinstance(exc, (ValueError, ServiceValidationError)):
        code = "invalid_request"
    elif isinstance(exc, MealieAuthError):
        code = "mealie_auth"
    else:
        code = "mealie_error"
    connection.send_error(
        msg["id"], code, str(exc)[:220], translation_key=code, translation_domain=DOMAIN
    )


async def _run(
    hass: HomeAssistant,
    connection,
    msg: dict,
    level: str | None,
    func: Callable[[MealieAtelierConfigEntry], Awaitable[Any]],
) -> None:
    """Resolve the entry, apply the permission level before any Mealie request, answer."""
    entry = get_loaded_entry(hass, msg.get("entry_id"))
    if entry is None:
        connection.send_error(
            msg["id"],
            "not_configured",
            "Configure Mealie Atelier Bridge",
            translation_key="not_configured",
            translation_domain=DOMAIN,
        )
        return
    if level is not None and not check(connection, msg, entry, level):
        return
    try:
        connection.send_result(msg["id"], await func(entry))
    except (MealieError, ValueError, TypeError, HomeAssistantError) as exc:
        _send_failure(connection, msg, exc)


def _changed() -> AtelierError:
    return AtelierError(
        "recipe_changed", "The recipe changed since it was loaded. Reload it before saving"
    )


def _unverified(backup_id: str) -> AtelierError:
    return AtelierError(
        "write_unverified",
        f"Mealie accepted the change but the saved recipe differs. Backup {backup_id} can undo it",
        {"backup_id": backup_id},
    )


def _step_missing() -> AtelierError:
    return AtelierError("step_not_found", "That step no longer matches the recipe")


def _signed(hass: HomeAssistant, entry: MealieAtelierConfigEntry, recipe: dict) -> dict:
    return sign_images(hass, entry.entry_id, recipe)


# Food and unit catalogue


def _project(item: dict, keys: tuple[str, ...]) -> dict:
    return {key: item.get(key) for key in keys}


async def _catalog(entry, msg: dict, kind: str, keys: tuple[str, ...]) -> dict:
    client = entry.runtime_data.client
    fetch = client.foods if kind == "foods" else client.units
    result = await fetch(msg["search"], msg["page"], msg["per_page"])
    return {
        "items": [_project(item, keys) for item in result["items"] if isinstance(item, dict)],
        "page": result.get("page", msg["page"]),
        "total": result.get("total", len(result["items"])),
    }


_CATALOG_SCHEMA = {
    probatio.Optional("entry_id"): str,
    probatio.Optional("search", default=""): _Search,
    probatio.Optional("page", default=1): int,
    probatio.Optional("per_page", default=20): int,
}


@websocket_api.websocket_command(
    {probatio.Required("type"): "mealie_atelier/foods", **_CATALOG_SCHEMA}
)
@websocket_api.async_response
async def ws_foods(hass, connection, msg):
    await _run(
        hass,
        connection,
        msg,
        None,
        lambda entry: _catalog(entry, msg, "foods", ("id", "name", "pluralName")),
    )


@websocket_api.websocket_command(
    {probatio.Required("type"): "mealie_atelier/units", **_CATALOG_SCHEMA}
)
@websocket_api.async_response
async def ws_units(hass, connection, msg):
    keys = ("id", "name", "pluralName", "abbreviation", "useAbbreviation", "fraction")
    await _run(hass, connection, msg, None, lambda entry: _catalog(entry, msg, "units", keys))


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/food_create",
        probatio.Optional("entry_id"): str,
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=120)),
        probatio.Optional("plural_name", default=""): probatio.All(str, probatio.Length(max=120)),
    }
)
@websocket_api.async_response
async def ws_food_create(hass, connection, msg):
    async def create(entry):
        food = await entry.runtime_data.client.create_food(msg["name"], msg["plural_name"])
        return _project(food, ("id", "name", "pluralName", "existing"))

    await _run(hass, connection, msg, "admin", create)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/unit_create",
        probatio.Optional("entry_id"): str,
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=60)),
        probatio.Optional("abbreviation", default=""): probatio.All(str, probatio.Length(max=20)),
    }
)
@websocket_api.async_response
async def ws_unit_create(hass, connection, msg):
    async def create(entry):
        unit = await entry.runtime_data.client.create_unit(msg["name"], msg["abbreviation"])
        return _project(unit, ("id", "name", "abbreviation", "existing"))

    await _run(hass, connection, msg, "admin", create)


# Structured ingredients


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/ingredients_get",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
    }
)
@websocket_api.async_response
async def ws_ingredients_get(hass, connection, msg):
    async def load(entry):
        return preview(await entry.runtime_data.client.recipe(msg["slug"]))

    await _run(hass, connection, msg, None, load)


def _resolver(client: MealieClient):
    async def resolve(kind: str, key: str) -> dict | None:
        try:
            return await (client.food if kind == "food" else client.unit)(key)
        except MealieError as exc:
            if exc.status == 404:
                return None
            raise

    return resolve


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/ingredients_save",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("expected_hash"): str,
        probatio.Required("rows"): list,
    }
)
@websocket_api.async_response
async def ws_ingredients_save(hass, connection, msg):
    async def save(entry):
        if not entry.options.get(CONF_EXPERIMENTAL_INGREDIENTS, False):
            raise AtelierError(
                "disabled", "Structured ingredient editing is switched off in the options"
            )
        data = entry.runtime_data
        client = data.client
        slug = safe_slug(msg["slug"])
        recipe = await client.recipe(slug)
        if fingerprint(recipe) != msg["expected_hash"]:
            raise _changed()
        rows = await merge(recipe, msg["rows"], _resolver(client))
        snapshot = await data.backups.async_snapshot(client, recipe, "ingredients", connection.user)
        updated = await client.patch_recipe(slug, {"recipeIngredient": rows})
        fresh = await client.recipe(updated.get("slug") or slug)
        if not verified(rows, fresh):
            raise _unverified(snapshot["backup_id"])
        return {
            "saved": True,
            "backup_id": snapshot["backup_id"],
            "expected_hash": fingerprint(fresh),
            "recipe": _signed(hass, entry, fresh),
        }

    await _run(hass, connection, msg, "admin", save)


# Backups


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/recipe_backups",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
    }
)
@websocket_api.async_response
async def ws_recipe_backups(hass, connection, msg):
    async def listing(entry):
        recipe = await entry.runtime_data.client.recipe(msg["slug"])
        return {"backups": entry.runtime_data.backups.list(safe_id(recipe.get("id")))}

    await _run(hass, connection, msg, None, listing)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/recipe_restore",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("backup_id"): str,
    }
)
@websocket_api.async_response
async def ws_recipe_restore(hass, connection, msg):
    async def restore(entry):
        data = entry.runtime_data
        client = data.client
        slug = safe_slug(msg["slug"])
        recipe = await client.recipe(slug)
        snapshot = data.backups.get(safe_id(recipe.get("id")), msg["backup_id"])
        with_image = snapshot["kind"] == "image" and bool(snapshot.get("image_file"))
        photo = await data.backups.read_image(snapshot) if with_image else None
        # The restore point comes first, so the restore itself can be undone.
        undo = await data.backups.async_snapshot(
            client, recipe, "restore", connection.user, image=with_image
        )
        changes = {key: value for key, value in snapshot["fields"].items() if value is not None}
        current_slug = slug
        if changes:
            updated = await client.patch_recipe(slug, changes)
            current_slug = updated.get("slug") or slug
        if photo is not None:
            await client.upload_image(current_slug, photo, "webp")
        fresh = await client.recipe(current_slug)
        if "recipeIngredient" in changes and not verified(changes["recipeIngredient"], fresh):
            raise _unverified(undo["backup_id"])
        return {
            "restored": True,
            "backup_id": undo["backup_id"],
            "recipe": _signed(hass, entry, fresh),
        }

    await _run(hass, connection, msg, "admin", restore)


# Uploads


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/upload_begin",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("purpose"): str,
        probatio.Required("size"): int,
        probatio.Required("mime"): str,
        probatio.Required("sha256"): str,
    }
)
@websocket_api.async_response
async def ws_upload_begin(hass, connection, msg):
    async def begin(entry):
        return entry.runtime_data.uploads.begin(
            connection.user.id,
            safe_slug(msg["slug"]),
            msg["purpose"],
            msg["size"],
            msg["mime"],
            msg["sha256"],
        )

    await _run(hass, connection, msg, "admin", begin)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/upload_chunk",
        probatio.Optional("entry_id"): str,
        probatio.Required("upload_id"): str,
        probatio.Required("index"): int,
        probatio.Required("data"): str,
    }
)
@websocket_api.async_response
async def ws_upload_chunk(hass, connection, msg):
    async def chunk(entry):
        return entry.runtime_data.uploads.chunk(
            connection.user.id, msg["upload_id"], msg["index"], msg["data"]
        )

    await _run(hass, connection, msg, "admin", chunk)


# Cover photo


def _image_value(recipe: dict) -> str:
    return recipe.get("image") or ""


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/recipe_image_set",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("upload_id"): str,
        probatio.Required("expected_image"): str,
    }
)
@websocket_api.async_response
async def ws_recipe_image_set(hass, connection, msg):
    async def replace(entry):
        data = entry.runtime_data
        client = data.client
        slug = safe_slug(msg["slug"])
        session = data.uploads.ready(connection.user.id, msg["upload_id"], "cover", slug)
        recipe = await client.recipe(slug)
        if _image_value(recipe) != msg["expected_image"]:
            raise _changed()
        snapshot = await data.backups.async_snapshot(
            client, recipe, "image", connection.user, image=True
        )
        await client.upload_image(slug, bytes(session.buffer), session.extension)
        data.uploads.discard(session.upload_id)
        fresh = await client.recipe(slug)
        if not _image_value(fresh):
            raise _unverified(snapshot["backup_id"])
        return {
            "saved": True,
            "backup_id": snapshot["backup_id"],
            "recipe": _signed(hass, entry, fresh),
        }

    await _run(hass, connection, msg, "admin", replace)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/recipe_image_delete",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("expected_image"): str,
    }
)
@websocket_api.async_response
async def ws_recipe_image_delete(hass, connection, msg):
    async def delete(entry):
        data = entry.runtime_data
        client = data.client
        slug = safe_slug(msg["slug"])
        recipe = await client.recipe(slug)
        if _image_value(recipe) != msg["expected_image"]:
            raise _changed()
        if not _image_value(recipe):
            raise ValueError("The recipe has no photo")
        snapshot = await data.backups.async_snapshot(
            client, recipe, "image", connection.user, image=True
        )
        await client.delete_image(slug)
        fresh = await client.recipe(slug)
        return {
            "deleted": True,
            "backup_id": snapshot["backup_id"],
            "recipe": _signed(hass, entry, fresh),
        }

    await _run(hass, connection, msg, "admin", delete)


# Step photos


def _tag_pattern(file_name: str) -> str:
    return rf"<img\b[^>]*/assets/{re.escape(file_name)}(?![A-Za-z0-9._-])[^>]*>"


def _references(text: str, file_name: str) -> bool:
    return re.search(_tag_pattern(file_name), text, re.IGNORECASE) is not None


def _strip_tag(text: str, file_name: str) -> str:
    return re.sub(r"\s*" + _tag_pattern(file_name), "", text, flags=re.IGNORECASE)


def _find_step(recipe: dict, step_index: int, step_id: str | None) -> dict:
    steps = recipe.get("recipeInstructions")
    if not isinstance(steps, list) or not 0 <= step_index < len(steps):
        raise _step_missing()
    step = steps[step_index]
    if not isinstance(step, dict) or not isinstance(step.get("text"), str):
        raise _step_missing()
    if step.get("id") and step_id != step["id"]:
        raise _step_missing()
    return step


def _file_name(value: str, step: dict) -> str:
    if not _FILE_NAME.fullmatch(value) or value in (".", ".."):
        raise ValueError("Invalid photo file name")
    if not _references(step["text"], value):
        raise _step_missing()
    return value


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/step_image_add",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("step_index"): int,
        probatio.Optional("step_id"): str,
        probatio.Required("upload_id"): str,
        probatio.Optional("replace_file_name"): str,
    }
)
@websocket_api.async_response
async def ws_step_image_add(hass, connection, msg):
    async def add(entry):
        data = entry.runtime_data
        client = data.client
        slug = safe_slug(msg["slug"])
        index = msg["step_index"]
        session = data.uploads.ready(connection.user.id, msg["upload_id"], "step", slug)
        recipe = await client.recipe(slug)
        step = _find_step(recipe, index, msg.get("step_id"))
        replace = _file_name(msg["replace_file_name"], step) if "replace_file_name" in msg else None
        snapshot = await data.backups.async_snapshot(client, recipe, "step_image", connection.user)
        asset = await client.upload_asset(
            slug,
            f"atelier-step-{index + 1}-{uuid4().hex[:8]}",
            session.extension,
            bytes(session.buffer),
        )
        data.uploads.discard(session.upload_id)
        file_name = asset["fileName"]
        recipe_id = safe_id(recipe.get("id"))
        instructions = copy.deepcopy(recipe["recipeInstructions"])
        text = _strip_tag(step["text"], replace) if replace else step["text"]
        instructions[index]["text"] = (
            f'{text}\n\n<img src="/api/media/recipes/{recipe_id}/assets/{file_name}" alt="" />'
        )
        assets = [
            item
            for item in recipe.get("assets") or []
            if not (replace and isinstance(item, dict) and item.get("fileName") == replace)
        ]
        # One PATCH keeps the asset list and the step text consistent.
        await client.patch_recipe(
            slug, {"assets": [*assets, asset], "recipeInstructions": instructions}
        )
        fresh = await client.recipe(slug)
        saved = fresh.get("recipeInstructions") or []
        if index >= len(saved) or not _references(str(saved[index].get("text")), file_name):
            raise _unverified(snapshot["backup_id"])
        return {
            "saved": True,
            "backup_id": snapshot["backup_id"],
            "file_name": file_name,
            "recipe": _signed(hass, entry, fresh),
        }

    await _run(hass, connection, msg, "admin", add)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/step_image_remove",
        probatio.Optional("entry_id"): str,
        probatio.Required("slug"): str,
        probatio.Required("step_index"): int,
        probatio.Optional("step_id"): str,
        probatio.Required("file_name"): str,
    }
)
@websocket_api.async_response
async def ws_step_image_remove(hass, connection, msg):
    async def remove(entry):
        data = entry.runtime_data
        client = data.client
        slug = safe_slug(msg["slug"])
        index = msg["step_index"]
        recipe = await client.recipe(slug)
        step = _find_step(recipe, index, msg.get("step_id"))
        file_name = _file_name(msg["file_name"], step)
        snapshot = await data.backups.async_snapshot(client, recipe, "step_image", connection.user)
        instructions = copy.deepcopy(recipe["recipeInstructions"])
        instructions[index]["text"] = _strip_tag(step["text"], file_name)
        assets = [
            item
            for item in recipe.get("assets") or []
            if not (isinstance(item, dict) and item.get("fileName") == file_name)
        ]
        # Mealie has no asset delete endpoint: the file stays on its disk, only the references go.
        await client.patch_recipe(slug, {"assets": assets, "recipeInstructions": instructions})
        fresh = await client.recipe(slug)
        saved = fresh.get("recipeInstructions") or []
        if index >= len(saved) or _references(str(saved[index].get("text")), file_name):
            raise _unverified(snapshot["backup_id"])
        return {
            "removed": True,
            "backup_id": snapshot["backup_id"],
            "recipe": _signed(hass, entry, fresh),
        }

    await _run(hass, connection, msg, "admin", remove)

"""WebSocket commands against real Home Assistant with a mocked Mealie server."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.websocket_api.const import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier import websocket
from custom_components.mealie_atelier.const import (
    CONF_AI_KEY,
    CONF_READ_ONLY,
    CONF_WRITE_USERS,
)

from .conftest import ENTRY_ID, JSON, MEALIE_URL

RECIPE = {
    "id": "recipe-42",
    "slug": "gnocchi",
    "image": "image.webp",
    "recipeIngredient": [],
    "recipeInstructions": [
        {
            "title": "Prepare",
            "text": 'Cut vegetables. <img src="/api/media/recipes/recipe-42/assets/step-1.jpg" width="100%"><img src="https://unsafe.example/step.jpg">',
        }
    ],
}


async def send(client, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


async def test_registration_and_signed_recipe_images(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    commands = [
        name
        for name in hass.data[WEBSOCKET_DOMAIN]
        if name.startswith("mealie_atelier/") and not name.startswith("mealie_atelier/sync_")
    ]
    assert len(commands) == 35
    for name in ("alarm_create", "alarm_cancel", "alarm_capabilities"):
        assert f"mealie_atelier/{name}" in commands

    mock_mealie.get(f"{MEALIE_URL}/api/recipes/gnocchi", json=RECIPE, headers=JSON)
    client = await hass_ws_client()
    response = await send(client, {"type": "mealie_atelier/recipe", "slug": "gnocchi"})

    assert response["success"]
    result = response["result"]
    assert result["imageUrl"].startswith(f"/api/mealie_atelier/image/{ENTRY_ID}/recipe-42?authSig=")
    step = result["recipeInstructions"][0]
    assert step["text"] == "Cut vegetables."
    assert len(step["imageUrls"]) == 1
    assert step["imageUrls"][0].startswith(
        f"/api/mealie_atelier/recipe_asset/{ENTRY_ID}/recipe-42/step-1.jpg?authSig="
    )
    assert "api_token" not in str(result).lower()
    assert "MOCK_TOKEN" not in str(result)


async def test_only_admin_can_manage_recipes_and_plain_ingredients_are_gone(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    mock_mealie: AiohttpClientMocker,
):
    entry = setup_integration
    mutate = AsyncMock(return_value={"saved": True})
    entry.runtime_data.client.mutate = mutate

    ordinary = await hass_ws_client(hass, hass_read_only_access_token)
    response = await send(
        ordinary,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": "gnocchi", "name": "New name"},
        },
    )
    assert response["error"]["code"] == "unauthorized"
    mutate.assert_not_called()

    admin = await hass_ws_client()
    ingredients = {
        "type": "mealie_atelier/mutate",
        "action": "recipe_update",
        "payload": {"slug": "gnocchi", "ingredients": ["200 g flour"]},
    }
    response = await send(admin, ingredients)
    assert response["error"]["code"] == "use_structured_editor"
    mutate.assert_not_called()

    mock_mealie.get(
        f"{MEALIE_URL}/api/recipes/gnocchi",
        json={"id": "recipe-42", "slug": "gnocchi", "name": "Gnocchi"},
        headers=JSON,
    )
    response = await send(
        admin,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": "gnocchi", "name": "New name"},
        },
    )
    assert response["success"]
    assert mutate.await_args.args[0] == "recipe_update"


async def test_readonly_denies_all_writes_and_exposes_status(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_storage: dict[str, Any],
):
    entry = setup_integration
    hass.config_entries.async_update_entry(entry, options={CONF_READ_ONLY: True})
    mutate = AsyncMock()
    entry.runtime_data.client.mutate = mutate
    client = await hass_ws_client()

    response = await send(client, {"type": "mealie_atelier/status"})
    status = response["result"]["entries"][0]
    assert status["entry_id"] == ENTRY_ID
    assert not status["can_write"]
    assert not status["can_manage"]
    assert status["permissions"] == {"can_read": True, "can_write": False, "can_admin": False}

    response = await send(
        client,
        {
            "type": "mealie_atelier/mutate",
            "action": "shopping_add",
            "payload": {"list_id": "list-1", "note": "Bread"},
        },
    )
    assert response["error"]["code"] == "read_only"
    response = await send(
        client,
        {
            "type": "mealie_atelier/studio_update",
            "action": "pantry_put",
            "payload": {"name": "Rice", "amount": 100, "unit": "g"},
        },
    )
    assert response["error"]["code"] == "read_only"
    assert f"mealie_atelier_studio_{ENTRY_ID}" not in hass_storage
    mutate.assert_not_called()


async def test_studio_revision_conflict_and_user_isolation(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    hass_storage: dict[str, Any],
):
    first = await hass_ws_client()
    second = await hass_ws_client(hass, hass_read_only_access_token)

    response = await send(
        first,
        {
            "type": "mealie_atelier/studio_update",
            "action": "journal_set",
            "payload": {"slug": "gnocchi", "note": "More sage", "rating": 5, "favorite": True},
            "expected_revision": 0,
        },
    )
    assert response["success"]
    stored = hass_storage[f"mealie_atelier_studio_{ENTRY_ID}"]
    assert stored["data"]["revision"] == 1

    response = await send(
        second,
        {
            "type": "mealie_atelier/studio_update",
            "action": "journal_set",
            "payload": {"slug": "gnocchi", "note": "Less salt", "rating": 4},
            "expected_revision": 0,
        },
    )
    assert response["error"]["code"] == "conflict"

    response = await send(second, {"type": "mealie_atelier/studio_state"})
    assert response["result"]["journal"] == {}
    response = await send(first, {"type": "mealie_atelier/studio_state"})
    assert response["result"]["journal"]["gnocchi"]["rating"] == 5
    assert hass_storage[f"mealie_atelier_studio_{ENTRY_ID}"]["data"]["revision"] == 1


async def test_nonadmin_cannot_assign_child_task(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    hass_storage: dict[str, Any],
):
    ordinary = await hass_ws_client(hass, hass_read_only_access_token)

    response = await send(
        ordinary,
        {
            "type": "mealie_atelier/studio_update",
            "action": "family_member",
            "payload": {"name": "Kid", "child": True},
        },
    )

    assert response["error"]["code"] == "unauthorized"
    assert f"mealie_atelier_studio_{ENTRY_ID}" not in hass_storage


async def test_entry_id_selects_between_multiple_entries(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    other_url = "http://other.test:9000"
    mock_mealie.get(
        f"{other_url}/api/users/self", json={"id": "xyz", "username": "other"}, headers=JSON
    )
    mock_mealie.get(
        f"{MEALIE_URL}/api/recipes/soup", json={"id": "a1", "slug": "soup"}, headers=JSON
    )
    mock_mealie.get(
        f"{other_url}/api/recipes/soup", json={"id": "b2", "slug": "soup"}, headers=JSON
    )
    second = MockConfigEntry(
        domain=setup_integration.domain,
        title="Second Kitchen",
        unique_id="other.test:xyz",
        entry_id="second-entry",
        data={**setup_integration.data, "url": other_url},
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client()

    status = await send(client, {"type": "mealie_atelier/status"})
    assert {row["entry_id"] for row in status["result"]["entries"]} == {ENTRY_ID, "second-entry"}

    default = await send(client, {"type": "mealie_atelier/recipe", "slug": "soup"})
    assert default["result"]["id"] == "a1"
    chosen = await send(
        client, {"type": "mealie_atelier/recipe", "slug": "soup", "entry_id": "second-entry"}
    )
    assert chosen["result"]["id"] == "b2"
    unknown = await send(
        client, {"type": "mealie_atelier/recipe", "slug": "soup", "entry_id": "missing"}
    )
    assert unknown["error"]["code"] == "not_configured"

    await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    status = await send(client, {"type": "mealie_atelier/status"})
    assert [row["entry_id"] for row in status["result"]["entries"]] == [ENTRY_ID]


async def test_translated_errors_reach_the_websocket_client(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    response = await send(
        client, {"type": "mealie_atelier/nutrition_prepare", "slug": "soup", "entry_id": "missing"}
    )
    assert response["error"]["code"] == "not_configured"
    assert response["error"]["message"].startswith("Configure Mealie Atelier Bridge first")

    mock_mealie.get(
        f"{MEALIE_URL}/api/recipes/soup",
        json={"id": "a1", "slug": "soup", "nutrition": {"calories": "120"}},
        headers=JSON,
    )
    response = await send(
        client,
        {
            "type": "mealie_atelier/nutrition_save",
            "slug": "soup",
            "expected_hash": "x",
            "entries": [],
        },
    )
    assert response["error"]["code"] == "existing_values_protected"
    assert "protected" in response["error"]["message"]


async def test_write_grants_gate_mutate_and_show_in_status(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
):
    entry = setup_integration
    hass.config_entries.async_update_entry(entry, options={CONF_WRITE_USERS: ["somebody-else"]})
    mutate = AsyncMock()
    entry.runtime_data.client.mutate = mutate
    ordinary = await hass_ws_client(hass, hass_read_only_access_token)

    status = (await send(ordinary, {"type": "mealie_atelier/status"}))["result"]["entries"][0]
    assert status["permissions"] == {"can_read": True, "can_write": False, "can_admin": False}
    assert status["can_write"] is False
    response = await send(
        ordinary,
        {
            "type": "mealie_atelier/mutate",
            "action": "shopping_add",
            "payload": {"list_id": "list-1", "note": "Bread"},
        },
    )
    assert response["error"]["code"] == "write_not_permitted"
    mutate.assert_not_called()


async def test_invalid_recipe_update_takes_no_backup(
    hass: HomeAssistant, setup_integration, hass_ws_client: WebSocketGenerator
) -> None:
    """Rejected payloads must not rotate real restore points out of the backup slots."""
    snapshot = AsyncMock()
    setup_integration.runtime_data.backups.async_snapshot = snapshot
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": "gnocchi", "servings": 0},
        }
    )
    response = await client.receive_json()
    assert response["error"]["code"] == "invalid_request"
    snapshot.assert_not_awaited()


async def test_removing_the_entry_deletes_sync_and_backup_stores(
    hass: HomeAssistant, setup_integration, hass_storage: dict
) -> None:
    """The sync outbox holds shopping item titles; nothing of a removed entry may stay."""
    entry_id = setup_integration.entry_id
    keys = (f"mealie_atelier_sync_{entry_id}", f"mealie_atelier_recipe_backups_{entry_id}")
    for key in keys:
        hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {}}
    assert await hass.config_entries.async_remove(entry_id)
    await hass.async_block_till_done()
    for key in keys:
        assert key not in hass_storage


async def test_status_accepts_the_entry_id_a_configured_card_sends(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()
    response = await send(client, {"type": "mealie_atelier/status", "entry_id": ENTRY_ID})
    assert response["success"], response
    assert response["result"]["entries"][0]["entry_id"] == ENTRY_ID


def test_ai_failures_use_their_own_error_code():
    from unittest.mock import MagicMock

    from custom_components.mealie_atelier.ai_client import AiError
    from custom_components.mealie_atelier.api import MealieError
    from custom_components.mealie_atelier.websocket import _fail

    for exc, code in (
        (AiError("AI provider unavailable"), "ai_error"),
        (MealieError("Mealie down"), "mealie_error"),
        (ValueError("Bad value"), "invalid_request"),
    ):
        connection = MagicMock()
        _fail(connection, {"id": 7}, exc)
        connection.send_error.assert_called_once_with(7, code, str(exc))


def test_translated_validation_errors_keep_their_own_code():
    from unittest.mock import MagicMock

    from custom_components.mealie_atelier.websocket import _fail, _translated

    connection = MagicMock()
    _fail(connection, {"id": 9}, _translated("too_many_timers"))
    args, kwargs = connection.send_error.call_args
    assert args[:2] == (9, "too_many_timers")
    assert kwargs["translation_key"] == "too_many_timers"


async def test_status_reports_ai_only_with_a_key(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    async def ai_flag() -> bool:
        reply = await send(client, {"type": "mealie_atelier/status"})
        return reply["result"]["entries"][0]["features"]["ai"]

    assert await ai_flag() is False
    hass.config_entries.async_update_entry(setup_integration, options={CONF_AI_KEY: "k"})
    assert await ai_flag() is True


async def test_ai_commands_use_the_card_language_else_the_home_assistant_language(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_mealie.get(f"{MEALIE_URL}/api/recipes/gnocchi", json=RECIPE, headers=JSON)
    hass.config_entries.async_update_entry(setup_integration, options={CONF_AI_KEY: "k"})
    hass.config.language = "en"
    client = await hass_ws_client()
    substitute = {"type": "mealie_atelier/studio_substitute", "ingredient": "Butter", "recipe": "R"}
    audit = {"type": "mealie_atelier/studio_ai_audit", "slug": "gnocchi"}
    with (
        patch.object(websocket, "ai_substitutes", AsyncMock(return_value={})) as subs,
        patch.object(websocket, "ai_audit", AsyncMock(return_value={})) as review,
    ):
        for message in (substitute, audit):
            assert (await send(client, message))["success"]
        assert subs.call_args.args[-1] == "en"
        assert review.call_args.args[-1] == "en"
        hass.config.language = "de-CH"
        for message in (substitute, audit):
            assert (await send(client, message))["success"]
        assert subs.call_args.args[-1] == "de-CH"
        assert review.call_args.args[-1] == "de-CH"
        for message in (substitute, audit):
            assert (await send(client, dict(message, language="fr")))["success"]
        assert subs.call_args.args[-1] == "fr"
        assert review.call_args.args[-1] == "fr"

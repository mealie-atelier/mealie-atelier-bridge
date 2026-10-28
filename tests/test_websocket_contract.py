"""WebSocket commands against real Home Assistant with a mocked Mealie server."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.websocket_api.const import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier.const import CONF_EXPERIMENTAL_INGREDIENTS, CONF_READ_ONLY

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
    commands = [name for name in hass.data[WEBSOCKET_DOMAIN] if name.startswith("mealie_atelier/")]
    assert len(commands) == 20
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


async def test_only_admin_can_manage_recipe_and_experimental_ingredients(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
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
    assert response["error"]["code"] == "disabled"
    mutate.assert_not_called()

    hass.config_entries.async_update_entry(entry, options={CONF_EXPERIMENTAL_INGREDIENTS: True})
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
    assert response["error"]["code"] == "invalid_request"
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
    assert response["error"]["code"] == "invalid_request"
    assert "protected" in response["error"]["message"]

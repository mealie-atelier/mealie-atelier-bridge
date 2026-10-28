"""Signed image proxy views: only valid signatures reach the Mealie server."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import ENTRY_ID, MEALIE_URL

IMAGE_PATH = f"/api/mealie_atelier/image/{ENTRY_ID}/recipe-42"
ASSET_PATH = f"/api/mealie_atelier/recipe_asset/{ENTRY_ID}/recipe-42/step-1.jpg"
UPSTREAM_IMAGE = f"{MEALIE_URL}/api/media/recipes/recipe-42/images/min-original.webp"


def signed(hass: HomeAssistant, path: str) -> str:
    return async_sign_path(hass, path, timedelta(minutes=5))


async def test_valid_signature_returns_the_image(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
):
    # The aiohttp mock responses lack content_length, so stub the fetch instead of the wire.
    setup_integration.runtime_data.client.image = AsyncMock(return_value=b"webp-bytes")
    client = await hass_client_no_auth()

    response = await client.get(signed(hass, IMAGE_PATH))

    assert response.status == 200
    assert response.content_type == "image/webp"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "private, max-age=300"
    assert await response.read() == b"webp-bytes"
    # The Mealie token is only ever sent upstream, never back to the browser.
    assert "MOCK_TOKEN" not in str(response.headers)


async def test_missing_or_invalid_signature_is_rejected(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
):
    image = AsyncMock(return_value=b"webp-bytes")
    setup_integration.runtime_data.client.image = image
    client = await hass_client_no_auth()
    url = signed(hass, IMAGE_PATH)
    tampered = url[:-1] + ("A" if url[-1] != "A" else "B")
    other_recipe = signed(hass, f"/api/mealie_atelier/image/{ENTRY_ID}/recipe-99")
    reused_on_other_path = IMAGE_PATH + "?" + other_recipe.split("?", 1)[1]

    assert (await client.get(IMAGE_PATH)).status == 401
    assert (await client.get(tampered)).status == 401
    assert (await client.get(reused_on_other_path)).status == 401
    assert (await client.get(IMAGE_PATH + "?authSig=garbage")).status == 401
    # No rejected request may have reached the Mealie server.
    image.assert_not_called()


async def test_signed_request_for_unknown_entry_or_missing_image(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
):
    mock_mealie.get(UPSTREAM_IMAGE, status=404)
    client = await hass_client_no_auth()

    unknown = await client.get(signed(hass, "/api/mealie_atelier/image/missing/recipe-42"))
    absent = await client.get(signed(hass, IMAGE_PATH))
    unsafe = await client.get(signed(hass, f"/api/mealie_atelier/image/{ENTRY_ID}/bad%20id"))

    assert unknown.status == 404
    assert absent.status == 404
    assert unsafe.status == 404


async def test_upstream_failure_becomes_bad_gateway(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
):
    mock_mealie.get(UPSTREAM_IMAGE, status=500)
    client = await hass_client_no_auth()

    response = await client.get(signed(hass, IMAGE_PATH))

    assert response.status == 502


async def test_recipe_asset_view_requires_signature(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
):
    setup_integration.runtime_data.client.recipe_asset = AsyncMock(
        return_value=(b"\xff\xd8\xffjpeg", "image/jpeg")
    )
    client = await hass_client_no_auth()

    assert (await client.get(ASSET_PATH)).status == 401
    response = await client.get(signed(hass, ASSET_PATH))

    assert response.status == 200
    assert response.content_type == "image/jpeg"
    assert await response.read() == b"\xff\xd8\xffjpeg"


async def test_unloaded_entry_serves_nothing(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
):
    client = await hass_client_no_auth()
    url = signed(hass, IMAGE_PATH)
    await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()

    assert (await client.get(url)).status == 404

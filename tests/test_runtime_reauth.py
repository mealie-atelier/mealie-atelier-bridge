"""A token that Mealie rejects while Home Assistant runs starts re-authentication."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier.api import MealieAuthError

from .conftest import ENTRY_ID


async def _call(client, message):
    await client.send_json_auto_id(message)
    return await client.receive_json()


def _reauth_flows(hass: HomeAssistant):
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_rejected_token_in_a_request_starts_reauth_and_status_reports_it(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    setup_integration.runtime_data.client.shopping = AsyncMock(
        side_effect=MealieAuthError("no", status=401)
    )
    client = await hass_ws_client()
    status = await _call(client, {"type": "mealie_atelier/status"})
    assert status["result"]["entries"][0]["connected"] is True
    assert not _reauth_flows(hass)

    response = await _call(client, {"type": "mealie_atelier/shopping", "entry_id": ENTRY_ID})
    await hass.async_block_till_done()

    assert response["error"]["code"] == "mealie_auth"
    assert len(_reauth_flows(hass)) == 1
    status = await _call(client, {"type": "mealie_atelier/status"})
    assert status["result"]["entries"][0]["connected"] is False

    # A second rejection does not open a second flow.
    await _call(client, {"type": "mealie_atelier/shopping", "entry_id": ENTRY_ID})
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


async def test_editing_path_starts_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    setup_integration.runtime_data.client.foods = AsyncMock(
        side_effect=MealieAuthError("no", status=401)
    )
    client = await hass_ws_client()

    response = await _call(client, {"type": "mealie_atelier/foods"})
    await hass.async_block_till_done()

    assert response["error"]["code"] == "mealie_auth"
    assert len(_reauth_flows(hass)) == 1


async def test_sync_path_starts_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    sync = MagicMock()
    sync.state = MagicMock(side_effect=MealieAuthError("no", status=401))
    setup_integration.runtime_data.sync = sync
    client = await hass_ws_client()

    response = await _call(client, {"type": "mealie_atelier/sync_state"})
    await hass.async_block_till_done()

    assert response["error"]["code"] == "mealie_auth"
    assert len(_reauth_flows(hass)) == 1


async def test_forbidden_endpoint_does_not_start_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    setup_integration.runtime_data.client.shopping = AsyncMock(
        side_effect=MealieAuthError("no", status=403)
    )
    client = await hass_ws_client()

    response = await _call(client, {"type": "mealie_atelier/shopping", "entry_id": ENTRY_ID})
    await hass.async_block_till_done()

    assert response["error"]["code"] == "mealie_auth"
    assert not _reauth_flows(hass)
    status = await _call(client, {"type": "mealie_atelier/status"})
    assert status["result"]["entries"][0]["connected"] is True

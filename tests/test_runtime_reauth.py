"""A token that Mealie rejects while Home Assistant runs starts re-authentication."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier import AUTH_CHECK_INTERVAL
from custom_components.mealie_atelier.api import MealieAuthError

from .conftest import ENTRY_ID, MEALIE_URL


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
    sync.async_stop = AsyncMock()
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


async def test_rejected_request_from_any_client_caller_starts_reauth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock,
):
    """The sync engine and other background callers use the client, not a websocket command."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{MEALIE_URL}/api/households/shopping/items/abc", status=401)
    assert not _reauth_flows(hass)

    with pytest.raises(MealieAuthError):
        await setup_integration.runtime_data.sync.client.shopping_item("abc")
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


async def test_idle_bridge_notices_a_revoked_token_within_one_check_interval(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock,
    freezer: FrozenDateTimeFactory,
):
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=401)
    assert not _reauth_flows(hass)

    freezer.tick(AUTH_CHECK_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


async def test_failed_store_load_leaves_no_token_check_armed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mealie,
    monkeypatch: pytest.MonkeyPatch,
):
    armed = []
    real = async_track_time_interval

    def _track(*args, **kwargs):
        unsub = real(*args, **kwargs)
        armed.append(unsub)
        return unsub

    monkeypatch.setattr("custom_components.mealie_atelier.async_track_time_interval", _track)
    monkeypatch.setattr(
        "custom_components.mealie_atelier.OfflineLedger.async_load",
        AsyncMock(side_effect=OSError("corrupt store")),
    )
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert armed == []

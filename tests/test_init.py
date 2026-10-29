"""Config entry lifecycle: setup, unload and the failure modes of the account probe."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.mealie_atelier.alarms import TimerManager
from custom_components.mealie_atelier.api import MealieClient
from custom_components.mealie_atelier.const import DOMAIN

from .conftest import MEALIE_URL


async def test_setup_and_unload_entry(hass: HomeAssistant, setup_integration: MockConfigEntry):
    entry = setup_integration
    data = entry.runtime_data

    assert isinstance(data.client, MealieClient)
    assert isinstance(data.alarms, TimerManager)
    assert data.alarms.task is not None
    assert data.studio.state["revision"] == 0
    assert hass.config_entries.async_loaded_entries(DOMAIN) == [entry]

    task = data.alarms.task
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert task.done()
    assert data.alarms.task is None


async def test_setup_registers_nothing_in_hass_data(
    hass: HomeAssistant, setup_integration: MockConfigEntry
):
    assert DOMAIN not in hass.data
    assert DOMAIN + "_studio" not in hass.data
    assert DOMAIN + "_alarms" not in hass.data


async def test_setup_with_rejected_token_requires_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=401)
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert "refused access" in (mock_config_entry.reason or "")
    assert not hasattr(mock_config_entry, "runtime_data")
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_setup_retries_when_mealie_is_unreachable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=500)
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    # The reason carries only a language-neutral detail, never an English sentence.
    assert mock_config_entry.reason == "Unable to connect to Mealie: HTTP 500"


async def test_setup_retry_reason_names_the_exception_for_network_errors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", exc=aiohttp.ClientConnectionError())
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.reason == "Unable to connect to Mealie: ClientConnectionError"


async def test_migration_rekeys_unique_id_on_the_account(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_mealie: AiohttpClientMocker
):
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.version == 2
    assert mock_config_entry.unique_id == "abc"

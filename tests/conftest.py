"""Shared fixtures: load the integration from custom_components with real Home Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.mealie_atelier.const import CONF_API_TOKEN, CONF_VERIFY_SSL, DOMAIN

MEALIE_URL = "http://mealie.test:9000"
MEALIE_TOKEN = "MOCK_TOKEN"
ENTRY_ID = "test-entry"
JSON = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant discover custom_components/mealie_atelier in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Studio Kitchen",
        unique_id="mealie.test:abc",
        entry_id=ENTRY_ID,
        data={CONF_URL: MEALIE_URL, CONF_API_TOKEN: MEALIE_TOKEN, CONF_VERIFY_SSL: True},
    )


@pytest.fixture
def mock_mealie(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer the account probe that setup performs."""
    aioclient_mock.get(
        f"{MEALIE_URL}/api/users/self", json={"id": "abc", "username": "demo"}, headers=JSON
    )
    return aioclient_mock


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_mealie: AiohttpClientMocker
) -> AsyncGenerator[MockConfigEntry]:
    """Set up one entry against the mocked Mealie and unload it again afterwards."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    yield mock_config_entry
    if mock_config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

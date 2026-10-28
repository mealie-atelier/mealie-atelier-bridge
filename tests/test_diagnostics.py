"""Diagnostics expose configuration shape and counts, never credentials or content."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mealie_atelier.const import (
    CONF_AI_KEY,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
)
from custom_components.mealie_atelier.diagnostics import async_get_config_entry_diagnostics

from .conftest import MEALIE_TOKEN, MEALIE_URL


async def test_diagnostics_redact_secrets_and_report_counts(
    hass: HomeAssistant, setup_integration: MockConfigEntry
):
    entry = setup_integration
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_READ_ONLY: True,
            CONF_AI_KEY: "sk-secret-ai",
            CONF_FDC_KEY: "secret-fdc",
            CONF_TIMER_NOTIFY_SERVICE: "notify.mobile_app_phone",
        },
    )
    entry.runtime_data.studio.state["pantry"] = [{"name": "Rice"}, {"name": "Beans"}]
    entry.runtime_data.alarms.alarms = [{"id": "t1", "label": "Private pasta timer"}]

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["integration_version"]
    assert result["entry"]["read_only"] is True
    assert result["entry"]["data"]["api_token"] == "**REDACTED**"
    assert result["entry"]["data"]["url"] == "**REDACTED**"
    assert result["entry"]["options"][CONF_AI_KEY] == "**REDACTED**"
    assert result["entry"]["options"][CONF_FDC_KEY] == "**REDACTED**"
    assert result["entry"]["options"][CONF_TIMER_NOTIFY_SERVICE] == "notify.mobile_app_phone"
    assert result["counts"]["alarms"] == 1
    assert result["counts"]["pantry_items"] == 2
    assert result["shopping_sync"]["rules"] == 0
    assert result["shopping_sync"]["records"]["pending"] == 0
    dumped = json.dumps(result)
    for secret in (MEALIE_TOKEN, MEALIE_URL, "sk-secret-ai", "secret-fdc"):
        assert secret not in dumped
    # Counts only: neither pantry nor timer content may leak.
    assert "Rice" not in dumped
    assert "Private pasta timer" not in dumped


async def test_diagnostics_for_entry_that_is_not_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
):
    mock_config_entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "counts" not in result
    assert result["entry"]["data"]["api_token"] == "**REDACTED**"

"""Config flow tests on the real Home Assistant fixtures."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mealie_atelier.api import MealieAuthError, MealieError
from custom_components.mealie_atelier.const import (
    CONF_API_TOKEN,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    DOMAIN,
)

URL = "http://mealie.test:9000"
ACCOUNT = {"id": "user-1", "username": "chef"}
CHECK = "custom_components.mealie_atelier.config_flow.MealieClient.check_connection"
SETUP = "custom_components.mealie_atelier.async_setup_entry"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components during these tests."""


@pytest.fixture(autouse=True)
def _no_setup():
    with (
        patch(SETUP, return_value=True),
        patch("custom_components.mealie_atelier.async_unload_entry", return_value=True),
    ):
        yield


def _entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{URL}:user-1",
        data={CONF_API_TOKEN: "old", CONF_VERIFY_SSL: True, "url": URL, **data},
    )


async def _start_user(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


def _input(url=URL, token="secret"):
    return {"url": url, CONF_API_TOKEN: token, CONF_VERIFY_SSL: True}


async def test_user_step_success_strips_trailing_slash(hass):
    result = await _start_user(hass)
    assert result["type"] is FlowResultType.FORM
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _input(url=f" {URL}/ ", token=" secret ")
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["url"] == URL
    assert result["data"][CONF_API_TOKEN] == "secret"
    assert result["result"].unique_id == f"{URL}:user-1"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (MealieError("down"), "cannot_connect"),
        (MealieAuthError("no"), "invalid_auth"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_step_errors(hass, error, key):
    result = await _start_user(hass)
    with patch(CHECK, side_effect=error):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}


async def test_user_step_invalid_url(hass):
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _input(url="ftp://mealie.test")
    )
    assert result["errors"] == {"base": "invalid_url"}


async def test_user_step_already_configured(hass):
    _entry().add_to_hass(hass)
    result = await _start_user(hass)
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "fresh"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "fresh"
    assert entry.data["url"] == URL


async def test_reauth_invalid_token(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    with patch(CHECK, side_effect=MealieAuthError("no")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_API_TOKEN] == "old"


async def test_reconfigure_success(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    new_url = "https://mealie.example.test"
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _input(url=f"{new_url}/", token="newer")
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["url"] == new_url
    assert entry.data[CONF_API_TOKEN] == "newer"
    assert entry.unique_id == f"{new_url}:user-1"


async def test_reauth_rejects_other_account(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    with patch(CHECK, return_value={"id": "user-2", "username": "guest"}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "foreign"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_API_TOKEN] == "old"


async def test_reconfigure_rejects_other_account(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(CHECK, return_value={"id": "user-2", "username": "guest"}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _input(token="foreign")
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_API_TOKEN] == "old"
    assert entry.unique_id == f"{URL}:user-1"


async def test_options_flow_save(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_READ_ONLY: True, CONF_TIMER_NOTIFY_SERVICE: "notify.mobile_app_phone"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_READ_ONLY] is True
    assert entry.options[CONF_TIMER_NOTIFY_SERVICE] == "notify.mobile_app_phone"


async def test_options_flow_rejects_bad_notify_service(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIMER_NOTIFY_SERVICE: "telegram"}
    )
    assert result["errors"] == {CONF_TIMER_NOTIFY_SERVICE: "invalid_notify_service"}

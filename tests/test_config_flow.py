"""Config flow tests on the real Home Assistant fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelector, TextSelectorType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mealie_atelier.api import MealieAuthError, MealieError
from custom_components.mealie_atelier.const import (
    CONF_AI_KEY,
    CONF_API_TOKEN,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    CONF_WRITE_USERS,
    DOMAIN,
)

URL = "http://mealie.test:9000"
ACCOUNT = {"id": "user-1", "username": "chef"}
CHECK = "custom_components.mealie_atelier.config_flow.MealieClient.check_connection"
SETUP = "custom_components.mealie_atelier.async_setup_entry"
INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "mealie_atelier"


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
        unique_id="user-1",
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
    assert result["result"].unique_id == "user-1"
    assert result["title"] == "Mealie Atelier Bridge · chef@mealie.test:9000"


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
    assert entry.unique_id == "user-1"


async def test_reconfigure_retitles_a_generated_title_only(hass):
    generated = _entry()
    generated.add_to_hass(hass)
    renamed = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user-2",
        title="My kitchen",
        data={CONF_API_TOKEN: "old", CONF_VERIFY_SSL: True, "url": URL},
    )
    renamed.add_to_hass(hass)
    generated_title = f"Mealie Atelier Bridge · {ACCOUNT['username']}@{'mealie.test:9000'}"
    hass.config_entries.async_update_entry(generated, title=generated_title)
    for entry, account in ((generated, ACCOUNT), (renamed, {**ACCOUNT, "id": "user-2"})):
        result = await entry.start_reconfigure_flow(hass)
        with patch(CHECK, return_value=account):
            await hass.config_entries.flow.async_configure(
                result["flow_id"], _input(url="https://moved.example.test", token="t")
            )
    assert generated.title == f"Mealie Atelier Bridge · {ACCOUNT['username']}@moved.example.test"
    assert renamed.title == "My kitchen"


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
    assert entry.unique_id == "user-1"


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


async def test_options_form_fills_every_link_placeholder(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    placeholders = result["description_placeholders"]
    for locale in ("en", "de"):
        strings = json.loads((INTEGRATION / "translations" / f"{locale}.json").read_text())
        help_texts = strings["options"]["step"]["init"]["data_description"].values()
        used = {name for text in help_texts for name in re.findall(r"\{(\w+)\}", text)}
        assert used == {"openai_keys_url", "anthropic_keys_url", "usda_signup_url"}
        assert used <= set(placeholders)
    assert all(url.startswith("https://") for url in placeholders.values())


async def test_options_flow_rejects_bad_notify_service(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIMER_NOTIFY_SERVICE: "telegram"}
    )
    assert result["errors"] == {CONF_TIMER_NOTIFY_SERVICE: "invalid_notify_service"}


async def _options_form(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    field = next(key for key in schema if key == CONF_WRITE_USERS)
    return result, schema[field], field


async def test_options_flow_lists_only_active_non_system_users(hass):
    # The first user is the owner and therefore an admin: never offered, always allowed.
    await hass.auth.async_create_user("Owner")
    anna = await hass.auth.async_create_user("Anna")
    ben = await hass.auth.async_create_user("Ben")
    gone = await hass.auth.async_create_user("Inactive")
    await hass.auth.async_update_user(gone, is_active=False)
    await hass.auth.async_create_system_user("Integration")
    entry = _entry()
    entry.add_to_hass(hass)

    _, selector, _ = await _options_form(hass, entry)

    options = selector.config["options"]
    assert [(o["value"], o["label"]) for o in options] == [(anna.id, "Anna"), (ben.id, "Ben")]
    assert selector.config["multiple"] is True


async def test_options_flow_saves_write_users_and_suggests_only_known_ids(hass):
    await hass.auth.async_create_user("Owner")
    anna = await hass.auth.async_create_user("Anna")
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_WRITE_USERS: [anna.id, "deleted"]})

    result, _, field = await _options_form(hass, entry)
    # The form never suggests a stale id, so the frontend cannot resubmit it.
    assert field.description["suggested_value"] == [anna.id]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WRITE_USERS: [anna.id]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_WRITE_USERS] == [anna.id]


async def test_options_flow_rejects_unknown_write_user_id(hass):
    anna = await hass.auth.async_create_user("Anna")
    entry = _entry()
    entry.add_to_hass(hass)
    result, _, _ = await _options_form(hass, entry)

    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_WRITE_USERS: [anna.id, "deleted"]}
        )
    assert CONF_WRITE_USERS not in entry.options


async def test_options_flow_defaults_to_no_grants(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_WRITE_USERS] == []


def _assert_password(schema, key):
    selector = next(v for k, v in schema.schema.items() if k == key)
    assert isinstance(selector, TextSelector)
    assert selector.config["type"] == TextSelectorType.PASSWORD


async def test_secret_fields_use_password_selector(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    user = await _start_user(hass)
    _assert_password(user["data_schema"], CONF_API_TOKEN)
    reauth = await entry.start_reauth_flow(hass)
    _assert_password(reauth["data_schema"], CONF_API_TOKEN)
    reconfigure = await entry.start_reconfigure_flow(hass)
    _assert_password(reconfigure["data_schema"], CONF_API_TOKEN)
    options = await hass.config_entries.options.async_init(entry.entry_id)
    _assert_password(options["data_schema"], CONF_FDC_KEY)
    _assert_password(options["data_schema"], CONF_AI_KEY)


@pytest.mark.parametrize("service", ["notify.é", "notify.Mobile_App", "notify.", "notify.a b"])
async def test_options_flow_rejects_non_ascii_or_uppercase_notify(hass, service):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIMER_NOTIFY_SERVICE: service}
    )
    assert result["errors"] == {CONF_TIMER_NOTIFY_SERVICE: "invalid_notify_service"}


async def test_options_flow_accepts_lowercase_notify(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIMER_NOTIFY_SERVICE: "notify.mobile_app_pixel"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_same_account_under_another_url_is_a_duplicate(hass):
    _entry().add_to_hass(hass)
    result = await _start_user(hass)
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _input(url="http://other-host.test:19926")
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_entry_title_names_the_server(hass):
    titles = []
    for host in ("http://one.test:9925", "http://two.test:9925"):
        result = await _start_user(hass)
        account = {"id": f"id-{host}", "username": "admin"}
        with patch(CHECK, return_value=account):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], _input(url=host)
            )
        titles.append(result["title"])
    assert len(set(titles)) == 2
    assert titles[0] == "Mealie Atelier Bridge · admin@one.test:9925"


@pytest.mark.parametrize(
    "pasted",
    [
        f"{URL}/login",
        f"{URL}/api",
        f"{URL}/api/docs",
        f"{URL}/g/home/",
        f"{URL}/g/home/recipes",
        f"{URL}/LOGIN/",
        f"{URL}/login?redirect=%2Fg%2Fhome",
        f"{URL}/g/home#x",
    ],
)
async def test_user_step_strips_browser_paths(hass, pasted):
    result = await _start_user(hass)
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _input(url=pasted)
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["url"] == URL


async def test_user_step_keeps_a_proxy_subpath(hass):
    result = await _start_user(hass)
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _input(url=f"{URL}/mealie/login")
        )
    assert result["data"]["url"] == f"{URL}/mealie"


@pytest.mark.parametrize("url", ["http://mealie:99999", "http://mealie:99999/login"])
async def test_user_step_port_out_of_range_is_invalid_url(hass, url):
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _input(url=url))
    assert result["errors"] == {"base": "invalid_url"}


async def test_legacy_url_scoped_unique_id_still_matches_account(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id=f"{URL}:user-1")
    result = await entry.start_reauth_flow(hass)
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "fresh"}
        )
    assert result["reason"] == "reauth_successful"


def _serialized(schema) -> str:
    """Everything the frontend receives per field: its name, suggested value and default."""
    return json.dumps(
        [
            [str(key), key.description, None if callable(key.default) is False else key.default()]
            for key in schema.schema
        ],
        default=str,
    )


async def test_reconfigure_form_does_not_send_the_saved_token(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_API_TOKEN: "super-secret-token"}
    )
    result = await entry.start_reconfigure_flow(hass)
    assert "super-secret-token" not in _serialized(result["data_schema"])


async def test_reconfigure_with_empty_token_keeps_the_saved_one(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(CHECK, return_value=ACCOUNT):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": URL, CONF_VERIFY_SSL: True}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_TOKEN] == "old"


async def test_options_form_does_not_send_saved_keys(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_FDC_KEY: "fdc-secret", CONF_AI_KEY: "ai-secret"}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    text = _serialized(result["data_schema"])
    assert "fdc-secret" not in text
    assert "ai-secret" not in text


async def test_options_error_redisplay_keeps_typed_key(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AI_KEY: "typed-key", CONF_TIMER_NOTIFY_SERVICE: "notify.Bad"}
    )
    assert result["errors"] == {CONF_TIMER_NOTIFY_SERVICE: "invalid_notify_service"}
    assert "typed-key" in _serialized(result["data_schema"])


async def test_options_empty_key_keeps_saved_and_clear_removes(hass):
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_FDC_KEY: "fdc-secret", CONF_AI_KEY: "ai-secret"}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert entry.options[CONF_FDC_KEY] == "fdc-secret"
    assert entry.options[CONF_AI_KEY] == "ai-secret"
    assert "clear_ai_key" not in entry.options

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"clear_ai_key": True, CONF_FDC_KEY: " new-fdc "}
    )
    assert entry.options[CONF_AI_KEY] == ""
    assert entry.options[CONF_FDC_KEY] == "new-fdc"


async def test_options_flow_does_not_offer_admins(hass):
    owner = await hass.auth.async_create_user("Owner")
    anna = await hass.auth.async_create_user("Anna")
    entry = _entry()
    entry.add_to_hass(hass)
    _, selector, _ = await _options_form(hass, entry)
    values = [o["value"] for o in selector.config["options"]]
    assert owner.id not in values
    assert values == [anna.id]

"""Config entry lifecycle: setup, unload and the failure modes of the account probe."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.mealie_atelier.alarms import TimerManager
from custom_components.mealie_atelier.api import MealieClient
from custom_components.mealie_atelier import async_migrate_entry
from custom_components.mealie_atelier.const import CONF_WRITE_USERS, DOMAIN
from custom_components.mealie_atelier.permissions import can_write

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


def _legacy_entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={"url": MEALIE_URL, "api_token": "t", "verify_ssl": True},
        **kwargs,
    )


async def test_migration_keeps_the_legacy_id_when_the_account_is_taken(hass: HomeAssistant):
    first = _legacy_entry(unique_id="a.test:abc", title="first")
    second = _legacy_entry(unique_id="b.test:abc", title="second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    assert await async_migrate_entry(hass, first)
    assert await async_migrate_entry(hass, second)

    assert first.unique_id == "abc"
    assert second.unique_id == "b.test:abc"
    assert first.version == second.version == 2


async def test_setup_retitles_only_the_exact_generated_legacy_title(
    hass: HomeAssistant, mock_mealie: AiohttpClientMocker
):
    host = MEALIE_URL.split("://", 1)[1]
    cases = {
        "Mealie Atelier · demo": f"demo@{host}",
        "Mealie Atelier · Kitchen": "Mealie Atelier · Kitchen",
        "Mealie Atelier · Family": "Mealie Atelier · Family",
        "Family kitchen": "Family kitchen",
    }
    entries = {}
    for index, title in enumerate(cases):
        entry = _legacy_entry(unique_id=f"x:{index}", title=title)
        entry.add_to_hass(hass)
        entries[title] = entry
        await async_migrate_entry(hass, entry)
    # Setting up one entry sets up every entry of the domain.
    assert await hass.config_entries.async_setup(entries["Family kitchen"].entry_id)
    await hass.async_block_till_done()
    for title, expected in cases.items():
        assert entries[title].title == expected
        assert "retitle_legacy_title" not in entries[title].data


async def test_setup_shortens_the_old_prefixed_title_only(
    hass: HomeAssistant, mock_mealie: AiohttpClientMocker
):
    host = MEALIE_URL.split("://", 1)[1]
    prefixed = _legacy_entry(unique_id="x:1", title=f"Mealie Atelier Bridge · demo@{host}")
    custom = _legacy_entry(unique_id="x:2", title="Mealie Atelier Bridge · my own name")
    for entry in (prefixed, custom):
        entry.add_to_hass(hass)
        await async_migrate_entry(hass, entry)
    assert await hass.config_entries.async_setup(prefixed.entry_id)
    await hass.async_block_till_done()
    assert prefixed.title == f"demo@{host}"
    assert custom.title == "Mealie Atelier Bridge · my own name"


async def test_later_renames_are_never_reverted(
    hass: HomeAssistant, mock_mealie: AiohttpClientMocker
):
    entry = _legacy_entry(unique_id="x:1", title="Family kitchen")
    entry.add_to_hass(hass)
    await async_migrate_entry(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hass.config_entries.async_update_entry(entry, title="Mealie Atelier · demo")

    assert await hass.config_entries.async_reload(entry.entry_id)

    assert entry.title == "Mealie Atelier · demo"


async def test_entries_created_after_the_upgrade_keep_their_title(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_mealie: AiohttpClientMocker
):
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, title="Mealie Atelier · demo", version=2
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.title == "Mealie Atelier · demo"


async def test_migration_keeps_the_write_list_untouched(hass: HomeAssistant):
    admin = await hass.auth.async_create_user("Boss", group_ids=[GROUP_ID_ADMIN])
    member = await hass.auth.async_create_user("Guest", group_ids=[GROUP_ID_USER])
    mixed = [admin.id, member.id, "deleted-user"]
    stale = [admin.id, "deleted-user"]
    first = _legacy_entry(unique_id="x:1", options={CONF_WRITE_USERS: mixed})
    second = _legacy_entry(unique_id="x:2", options={CONF_WRITE_USERS: stale})
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    await async_migrate_entry(hass, first)
    await async_migrate_entry(hass, second)

    assert first.options[CONF_WRITE_USERS] == mixed
    assert second.options[CONF_WRITE_USERS] == stale
    assert not can_write(second, member)
    assert can_write(second, admin)


async def test_options_save_keeps_a_list_with_an_admin_and_a_stale_id(hass: HomeAssistant):
    admin = await hass.auth.async_create_user("Boss", group_ids=[GROUP_ID_ADMIN])
    member = await hass.auth.async_create_user("Guest", group_ids=[GROUP_ID_USER])
    entry = _legacy_entry(unique_id="x:1", options={CONF_WRITE_USERS: [admin.id, "deleted-user"]})
    entry.add_to_hass(hass)
    await async_migrate_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    field = next(key for key in result["data_schema"].schema if key == CONF_WRITE_USERS)
    assert field.description["suggested_value"] == [admin.id]
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WRITE_USERS: [admin.id]}
    )

    assert entry.options[CONF_WRITE_USERS] == [admin.id]
    assert not can_write(entry, member)


async def test_options_save_keeps_an_administrators_only_list(hass: HomeAssistant):
    admin = await hass.auth.async_create_user("Boss", group_ids=[GROUP_ID_ADMIN])
    member = await hass.auth.async_create_user("Guest", group_ids=[GROUP_ID_USER])
    entry = _legacy_entry(unique_id="x:1", options={CONF_WRITE_USERS: [admin.id]})
    entry.add_to_hass(hass)
    await async_migrate_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    field = next(key for key in result["data_schema"].schema if key == CONF_WRITE_USERS)
    assert field.description["suggested_value"] == [admin.id]
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WRITE_USERS: [admin.id]}
    )

    assert entry.options[CONF_WRITE_USERS] == [admin.id]
    assert not can_write(entry, member)


def test_user_facing_texts_use_the_full_integration_name():
    import json
    import pathlib
    import re

    root = pathlib.Path(__file__).parents[1] / "custom_components" / "mealie_atelier"
    for name in ("strings.json", "translations/en.json", "translations/de.json", "alarms.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert not re.search(r"Mealie[ -]Atelier(?! Bridge)(?!-Bridge)", text), name
        if name.endswith(".json"):
            json.loads(text)


async def _recipes_error(hass, hass_ws_client, entry_id: str) -> str:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "mealie_atelier/recipes", "entry_id": entry_id})
    response = await client.receive_json()
    return response["error"]["code"]


async def test_card_is_told_when_the_entry_needs_reauthentication(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=401)
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    code = await _recipes_error(hass, hass_ws_client, mock_config_entry.entry_id)

    assert code == "entry_reauth"


async def test_card_is_told_when_mealie_is_unreachable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=500)
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)

    code = await _recipes_error(hass, hass_ws_client, mock_config_entry.entry_id)

    assert code == "entry_unavailable"


async def test_unbound_card_is_told_about_the_only_entry_in_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=401)
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "mealie_atelier/recipes"})

    assert (await client.receive_json())["error"]["code"] == "entry_reauth"


async def test_unbound_card_is_told_about_the_only_entry_being_unavailable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
):
    aioclient_mock.get(f"{MEALIE_URL}/api/users/self", status=500)
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "mealie_atelier/recipes"})

    assert (await client.receive_json())["error"]["code"] == "entry_unavailable"


async def test_unknown_entry_is_still_not_configured(
    hass: HomeAssistant, setup_integration: MockConfigEntry, hass_ws_client
):
    assert await _recipes_error(hass, hass_ws_client, "missing") == "not_configured"

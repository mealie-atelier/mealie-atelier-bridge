"""`mealie_atelier/sync_*` WebSocket commands: shapes, limits, permissions and error codes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.websocket_api.const import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier.const import CONF_READ_ONLY

from .conftest import JSON, MEALIE_TOKEN, MEALIE_URL
from .sync_support import PARTY, WEEKLY, FakeMealie, FakeTodo, draft, mealie_item, setup_todo

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "mealie_atelier"
SYNC_COMMANDS = {
    "sync_state",
    "sync_destinations",
    "sync_preview",
    "sync_rule_save",
    "sync_rule_delete",
    "sync_run",
    "sync_pause",
    "sync_resume",
    "sync_retry",
    "sync_resolve",
    "sync_records",
}


async def send(client, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


@pytest.fixture
async def bring(hass: HomeAssistant) -> FakeTodo:
    entity = FakeTodo("Einkauf", "bring-1")
    await setup_todo(hass, entity)
    return entity


@pytest.fixture
def mealie(mock_mealie: AiohttpClientMocker) -> FakeMealie:
    fake = FakeMealie(mock_mealie)
    fake.add(
        mealie_item("a", note="Milch"),
        mealie_item("b", note="Brot"),
        mealie_item("c", quantity=2),
    )
    return fake


@pytest.fixture
async def admin(
    hass: HomeAssistant, bring, mealie, setup_integration: MockConfigEntry, hass_ws_client
):
    return await hass_ws_client()


async def create_rule(client, **overrides: Any) -> dict[str, Any]:
    response = await send(
        client, {"type": "mealie_atelier/sync_rule_save", "rule": draft(**overrides)}
    )
    assert response["success"], response
    return response["result"]["rule"]


async def test_commands_are_registered(hass: HomeAssistant, setup_integration: MockConfigEntry):
    registered = {
        name.removeprefix("mealie_atelier/")
        for name in hass.data[WEBSOCKET_DOMAIN]
        if name.startswith("mealie_atelier/sync_")
    }
    assert registered == SYNC_COMMANDS


async def test_setup_preview_confirm_and_status_flow(
    hass: HomeAssistant, admin, mealie: FakeMealie, bring: FakeTodo
):
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create("todo", "mealie", "list-1", suggested_object_id="mealie")
    hass.states.async_set("todo.mealie", "0", {"supported_features": 1, "friendly_name": "M"})
    hass.states.async_set("todo.read_only", "0", {"supported_features": 0})

    response = await send(admin, {"type": "mealie_atelier/sync_destinations"})
    assert response["result"]["entities"] == [
        {
            "entity_id": "todo.einkauf",
            "name": "Einkauf",
            "platform": "test",
            "available": True,
            "supports": {"create": True, "description": True, "update": True},
        }
    ]

    response = await send(admin, {"type": "mealie_atelier/sync_preview", "rule": draft()})
    preview = response["result"]
    assert preview["capabilities"]["description"] is True
    assert preview["sample"] == {"title": "1 Milch", "description": None}
    assert preview["total_candidates"] == 3
    assert [line["action"] for line in preview["lines"]] == ["send", "send", "skip_empty"]
    assert bring.calls == []

    rule = await create_rule(admin)
    assert rule["name"] == "Weekly groceries → Einkauf"
    assert rule["activated"] is False
    assert rule["revision"] == 1
    assert rule["destination"]["platform"] == "test"

    response = await send(admin, {"type": "mealie_atelier/sync_run", "rule_id": rule["id"]})
    assert response["error"]["code"] == "sync_not_activated"
    assert response["error"]["translation_key"] == "sync_not_activated"
    assert bring.calls == []

    response = await send(
        admin,
        {"type": "mealie_atelier/sync_run", "rule_id": rule["id"], "confirm_first_batch": True},
    )
    result = response["result"]
    assert result["outcome"] == "sent"
    assert result["sent"] == 2
    assert set(result) == {
        "rule_id",
        "batch_id",
        "outcome",
        "sent",
        "skipped_duplicates",
        "merged",
        "failed",
        "needs_reconciliation",
        "completed_in_source",
        "pending_left",
    }

    response = await send(admin, {"type": "mealie_atelier/sync_state"})
    state = response["result"]
    assert state["supported"] is True
    status = state["rules"][0]
    assert status["activated"] is True
    assert status["counts"]["sent"] == 2
    assert status["running"] is False
    assert state["audit"][-1]["outcome"] == "sent"

    response = await send(
        admin,
        {"type": "mealie_atelier/sync_records", "rule_id": rule["id"], "states": ["sent"]},
    )
    records = response["result"]["records"]
    assert len(records) == 2
    assert all("fingerprint" not in record for record in records)

    response = await send(admin, {"type": "mealie_atelier/sync_pause", "rule_id": rule["id"]})
    assert response["result"]["rule"]["paused"] is True
    response = await send(admin, {"type": "mealie_atelier/sync_resume", "rule_id": rule["id"]})
    assert response["result"]["rule"]["paused"] is False

    bring.reject = {"1 Eier"}
    mealie.add(mealie_item("d", note="Eier"))
    response = await send(admin, {"type": "mealie_atelier/sync_run", "rule_id": rule["id"]})
    assert response["result"]["outcome"] == "failed"
    bring.reject = set()
    response = await send(admin, {"type": "mealie_atelier/sync_retry", "rule_id": rule["id"]})
    assert response["result"]["outcome"] == "sent"
    assert bring.calls[-2:] == ["1 Eier", "1 Eier"]

    mealie.items["a"]["quantity"] = 2
    await send(admin, {"type": "mealie_atelier/sync_run", "rule_id": rule["id"]})
    response = await send(
        admin,
        {
            "type": "mealie_atelier/sync_resolve",
            "rule_id": rule["id"],
            "record_key": f"{rule['id']}:a",
            "action": "dismiss",
        },
    )
    assert response["result"]["record"]["state"] == "sent"
    response = await send(
        admin,
        {
            "type": "mealie_atelier/sync_resolve",
            "rule_id": rule["id"],
            "record_key": f"{rule['id']}:b",
            "action": "dismiss",
        },
    )
    assert response["error"]["code"] == "invalid_request"

    response = await send(admin, {"type": "mealie_atelier/sync_rule_delete", "rule_id": rule["id"]})
    assert response["result"] == {"deleted": True}
    response = await send(admin, {"type": "mealie_atelier/sync_run", "rule_id": rule["id"]})
    assert response["error"]["code"] == "sync_rule_not_found"
    assert bring.summaries == ["1 Milch", "1 Brot", "1 Eier"]


async def test_rule_limits_and_validation(hass: HomeAssistant, admin, mealie: FakeMealie):
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create("todo", "mealie", "list-1", suggested_object_id="mealie")
    hass.states.async_set("todo.mealie", "0", {"supported_features": 1})

    async def save(**kwargs: Any) -> dict[str, Any]:
        message = {"type": "mealie_atelier/sync_rule_save", **kwargs}
        return await send(admin, message)

    response = await save(rule=draft("todo.mealie"))
    assert response["error"]["code"] == "sync_destination_unsupported"
    response = await save(rule=draft("todo.missing"))
    assert response["error"]["code"] == "sync_destination_unsupported"
    for bad in (
        {"debounce_seconds": 4},
        {"debounce_seconds": 601},
        {"poll_interval_seconds": 59},
        {"mode": "mirror"},
        {"duplicate_policy": "merge_everything"},
        {"name": "x" * 61},
        {"triggers": {"after_atelier_add": "yes"}},
        {"source_list_id": "../lists"},
        {"source_list_id": "unknown-list"},
    ):
        response = await save(rule=draft(**bad))
        assert response["error"]["code"] == "invalid_request", bad

    rule = await create_rule(admin)
    response = await save(rule=draft())
    assert response["error"]["code"] == "sync_source_taken"

    response = await save(rule=draft(mode="move"), rule_id=rule["id"], expected_revision=7)
    assert response["error"]["code"] == "conflict"
    response = await save(rule=draft(mode="move"), rule_id=rule["id"], expected_revision=1)
    assert response["result"]["rule"]["mode"] == "move"
    assert response["result"]["rule"]["revision"] == 2

    response = await save(rule=draft(source_list_id=PARTY), rule_id=rule["id"])
    assert response["error"]["code"] == "invalid_request"
    response = await save(rule=draft(source_list_id=PARTY), rule_id=rule["id"], reset_records=True)
    assert response["result"]["rule"]["source_list_id"] == PARTY

    entry_sync = hass.config_entries.async_entries("mealie_atelier")[0].runtime_data.sync
    for n in range(4):
        list_id = f"list-extra-{n}"
        mealie.lists.append({"id": list_id, "name": f"Extra {n}"})
        await create_rule(admin, source_list_id=list_id)
    assert len(entry_sync.rules) == 5
    mealie.lists.append({"id": "list-sixth", "name": "Sixth"})
    response = await save(rule=draft(source_list_id="list-sixth"))
    assert response["error"]["code"] == "sync_too_many_rules"
    assert WEEKLY not in {rule["source_list_id"] for rule in entry_sync.rules.values()}


WRITE_COMMANDS = [
    ("sync_run", {"rule_id": "r"}),
    ("sync_pause", {"rule_id": "r"}),
    ("sync_resume", {"rule_id": "r"}),
    ("sync_retry", {"rule_id": "r"}),
    ("sync_resolve", {"rule_id": "r", "record_key": "r:a", "action": "dismiss"}),
]
ADMIN_COMMANDS = [
    ("sync_rule_save", {"rule": draft()}),
    ("sync_rule_delete", {"rule_id": "r"}),
    ("sync_run", {"rule_id": "r", "confirm_first_batch": True}),
]


@pytest.mark.parametrize(("command", "payload"), ADMIN_COMMANDS)
async def test_admin_commands_reject_non_admins_before_any_request(
    hass: HomeAssistant,
    bring,
    mealie,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    aioclient_mock: AiohttpClientMocker,
    command: str,
    payload: dict[str, Any],
):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    calls = aioclient_mock.call_count

    response = await send(client, {"type": f"mealie_atelier/{command}", **payload})

    assert response["error"]["code"] == "unauthorized"
    assert aioclient_mock.call_count == calls
    assert bring.calls == []


@pytest.mark.parametrize(("command", "payload"), WRITE_COMMANDS + ADMIN_COMMANDS)
async def test_read_only_blocks_every_changing_command(
    hass: HomeAssistant,
    admin,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    command: str,
    payload: dict[str, Any],
):
    hass.config_entries.async_update_entry(setup_integration, options={CONF_READ_ONLY: True})
    calls = aioclient_mock.call_count

    response = await send(admin, {"type": f"mealie_atelier/{command}", **payload})

    assert response["error"]["code"] == "read_only"
    assert aioclient_mock.call_count == calls


async def test_non_admin_may_run_an_activated_rule(
    hass: HomeAssistant,
    admin,
    bring: FakeTodo,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
):
    rule = await create_rule(admin)
    await send(
        admin,
        {"type": "mealie_atelier/sync_run", "rule_id": rule["id"], "confirm_first_batch": True},
    )
    user = await hass_ws_client(hass, hass_read_only_access_token)

    response = await send(user, {"type": "mealie_atelier/sync_run", "rule_id": rule["id"]})

    assert response["result"]["outcome"] == "nothing_to_send"
    response = await send(user, {"type": "mealie_atelier/sync_state"})
    assert response["result"]["rules"][0]["id"] == rule["id"]


async def test_errors_never_leak_token_or_mealie_url(admin, mealie: FakeMealie):
    rule = await create_rule(admin)
    mealie.fail_reads = True

    response = await send(admin, {"type": "mealie_atelier/sync_preview", "rule_id": rule["id"]})

    assert response["error"]["code"] == "mealie_error"
    dumped = json.dumps(response)
    assert MEALIE_TOKEN not in dumped
    assert MEALIE_URL not in dumped
    assert "mealie.test" not in dumped


async def test_missing_entry_is_not_configured(
    hass: HomeAssistant, setup_integration: MockConfigEntry, hass_ws_client
):
    await hass.config_entries.async_unload(setup_integration.entry_id)
    client = await hass_ws_client()
    response = await send(client, {"type": "mealie_atelier/sync_state"})
    assert response["error"]["code"] == "not_configured"


async def test_shopping_add_succeeds_when_sync_notification_fails(
    hass: HomeAssistant,
    admin,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
):
    mock_mealie.post(
        f"{MEALIE_URL}/api/households/shopping/items", json={"id": "new"}, headers=JSON
    )

    def broken(*args, **kwargs):
        raise RuntimeError("sync is broken")

    setup_integration.runtime_data.sync.notify_source_changed = broken

    response = await send(
        admin,
        {
            "type": "mealie_atelier/mutate",
            "action": "shopping_add",
            "payload": {"list_id": WEEKLY, "note": "Milch"},
        },
    )

    assert response["success"]
    assert response["result"] == {"id": "new"}


async def test_shopping_add_schedules_the_debounced_run(
    hass: HomeAssistant,
    admin,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
):
    rule = await create_rule(admin)
    await send(
        admin,
        {"type": "mealie_atelier/sync_run", "rule_id": rule["id"], "confirm_first_batch": True},
    )
    mock_mealie.post(
        f"{MEALIE_URL}/api/households/shopping/items", json={"id": "new"}, headers=JSON
    )

    await send(
        admin,
        {
            "type": "mealie_atelier/mutate",
            "action": "shopping_add",
            "payload": {"list_id": WEEKLY, "note": "Milch"},
        },
    )

    sync = setup_integration.runtime_data.sync
    assert sync.rule_status(sync.rules[rule["id"]])["next_run_at"] is not None


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_sync_error_code_is_translated(name: str):
    codes: set[str] = set()
    for source in (INTEGRATION / "shopping_sync").glob("*.py"):
        codes |= set(re.findall(r'SyncError\(\s*"(sync_\w+)"', source.read_text()))
    codes.add("sync_not_supported")
    exceptions = json.loads((INTEGRATION / name).read_text())["exceptions"]

    assert {"sync_rule_not_found", "sync_not_activated", "sync_source_taken"} <= codes
    for code in codes:
        assert exceptions[code]["message"], code

"""Moving a meal plan entry: validation, the read then write round trip and the feature flag."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import JSON, MEALIE_URL

PREFIX = "mealie_atelier/"
URL = f"{MEALIE_URL}/api/households/mealplans/42"
STORED = {
    "id": 42,
    "date": "2026-11-10",
    "groupId": "g1",
    "userId": "u1",
    "householdId": "h1",
    "entryType": "lunch",
    "title": "Soup",
    "text": "note",
    "recipeId": "r1",
    "recipeScale": 2.0,
    "recipe": {"id": "r1", "name": "Big nested recipe"},
}


async def send(client, payload: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(
        {"type": f"{PREFIX}mutate", "action": "mealplan_move", "payload": payload}
    )
    return await client.receive_json()


def calls(mock, method: str):
    return [c for c in mock.mock_calls if c[0] == method and str(c[1]).endswith("/mealplans/42")]


async def test_status_advertises_mealplan_move(setup_integration, hass_ws_client):
    client = await hass_ws_client()
    await client.send_json_auto_id({"type": f"{PREFIX}status"})
    response = await client.receive_json()
    assert response["result"]["entries"][0]["features"]["mealplan_move"] is True


async def test_move_reads_then_puts_the_entry_with_the_new_date(
    setup_integration, mock_mealie, hass_ws_client
):
    mock_mealie.get(URL, json=STORED, headers=JSON)
    mock_mealie.put(URL, json={**STORED, "date": "2026-11-12"}, headers=JSON)
    response = await send(await hass_ws_client(), {"entry_id": "42", "date": "2026-11-12"})
    assert response["success"]
    assert response["result"]["date"] == "2026-11-12"
    assert [c[2] for c in calls(mock_mealie, "PUT")] == [
        {
            "id": 42,
            "groupId": "g1",
            "userId": "u1",
            "entryType": "lunch",
            "title": "Soup",
            "text": "note",
            "recipeId": "r1",
            "recipeScale": 2.0,
            "date": "2026-11-12",
        }
    ]


async def test_move_sends_only_the_fields_the_entry_has(
    setup_integration, mock_mealie, hass_ws_client
):
    mock_mealie.get(URL, json={"id": 42, "groupId": "g1", "userId": "u1"}, headers=JSON)
    mock_mealie.put(URL, json={}, headers=JSON)
    response = await send(await hass_ws_client(), {"entry_id": "42", "date": "2026-11-12"})
    assert response["success"]
    assert [c[2] for c in calls(mock_mealie, "PUT")] == [
        {"id": 42, "groupId": "g1", "userId": "u1", "date": "2026-11-12"}
    ]


async def test_move_does_not_put_when_the_read_fails(
    setup_integration, mock_mealie, hass_ws_client
):
    mock_mealie.get(URL, status=404, json={})
    mock_mealie.put(URL, json={}, headers=JSON)
    response = await send(await hass_ws_client(), {"entry_id": "42", "date": "2026-11-12"})
    assert not response["success"]
    assert calls(mock_mealie, "PUT") == []


@pytest.mark.parametrize(
    "payload",
    [
        {"entry_id": "../x", "date": "2026-11-12"},
        {"date": "2026-11-12"},
        {"entry_id": "42", "date": "2026-02-30"},
        {"entry_id": "42", "date": "12.11.2026"},
        {"entry_id": "42"},
    ],
)
async def test_move_rejects_invalid_payloads_without_a_mealie_call(
    setup_integration, mock_mealie, hass_ws_client, payload
):
    mock_mealie.get(URL, json=STORED, headers=JSON)
    mock_mealie.put(URL, json=STORED, headers=JSON)
    response = await send(await hass_ws_client(), payload)
    assert response["error"]["code"] == "invalid_request"
    assert calls(mock_mealie, "GET") == []
    assert calls(mock_mealie, "PUT") == []


async def add(client, payload: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(
        {"type": f"{PREFIX}mutate", "action": "mealplan_add", "payload": payload}
    )
    return await client.receive_json()


async def test_add_restores_a_note_without_a_recipe(setup_integration, mock_mealie, hass_ws_client):
    plans = f"{MEALIE_URL}/api/households/mealplans"
    mock_mealie.post(plans, json={"id": 9}, headers=JSON)
    client = await hass_ws_client()
    response = await add(
        client,
        {"date": "2026-11-12", "meal_type": "lunch", "title": "Reste", "text": "vom Vortag"},
    )
    assert response["success"]
    posted = [c[2] for c in mock_mealie.mock_calls if c[0] == "POST" and str(c[1]) == plans]
    assert posted[-1] == {
        "date": "2026-11-12",
        "entryType": "lunch",
        "title": "Reste",
        "text": "vom Vortag",
    }


async def test_add_without_recipe_or_title_is_refused(
    setup_integration, mock_mealie, hass_ws_client
):
    response = await add(await hass_ws_client(), {"date": "2026-11-12", "meal_type": "lunch"})
    assert not response["success"]
    assert not [c for c in mock_mealie.mock_calls if c[0] == "POST"]

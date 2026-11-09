"""Favorites and ratings: the user's own Mealie feedback through the WebSocket API."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import JSON, MEALIE_URL

PREFIX = "mealie_atelier/"
RID = "6f1c1c1e-2f6a-4c0e-8d7b-3a1f5b9d2c10"


async def send(client, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


def calls(mock, method: str, suffix: str):
    return [c for c in mock.mock_calls if c[0] == method and str(c[1]).endswith(suffix)]


async def test_status_advertises_recipe_feedback(setup_integration, hass_ws_client):
    response = await send(await hass_ws_client(), {"type": f"{PREFIX}status"})
    assert response["result"]["entries"][0]["features"]["recipe_feedback"] is True


async def test_feedback_reads_own_rating_and_favorite(
    setup_integration, mock_mealie, hass_ws_client
):
    mock_mealie.get(
        f"{MEALIE_URL}/api/users/self/ratings/{RID}",
        json={"recipeId": RID, "rating": 4.5, "isFavorite": True},
        headers=JSON,
    )
    response = await send(
        await hass_ws_client(), {"type": f"{PREFIX}recipe_feedback", "recipe_id": RID}
    )
    assert response["result"] == {"rating": 4.5, "favorite": True}


async def test_feedback_404_means_nothing_given(setup_integration, mock_mealie, hass_ws_client):
    mock_mealie.get(f"{MEALIE_URL}/api/users/self/ratings/{RID}", status=404, json={})
    response = await send(
        await hass_ws_client(), {"type": f"{PREFIX}recipe_feedback", "recipe_id": RID}
    )
    assert response["result"] == {"rating": None, "favorite": False}


async def test_feedback_other_errors_are_reported(setup_integration, mock_mealie, hass_ws_client):
    mock_mealie.get(f"{MEALIE_URL}/api/users/self/ratings/{RID}", status=500, json={})
    response = await send(
        await hass_ws_client(), {"type": f"{PREFIX}recipe_feedback", "recipe_id": RID}
    )
    assert response["error"]["code"] == "mealie_error"


async def test_feedback_rejects_unsafe_recipe_id(setup_integration, mock_mealie, hass_ws_client):
    response = await send(
        await hass_ws_client(), {"type": f"{PREFIX}recipe_feedback", "recipe_id": "../x"}
    )
    assert response["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("rating", [4, 2.5, 0, None])
async def test_rate_posts_for_the_self_user(setup_integration, mock_mealie, hass_ws_client, rating):
    mock_mealie.post(f"{MEALIE_URL}/api/users/abc/ratings/gnocchi", json={}, headers=JSON)
    response = await send(
        await hass_ws_client(),
        {"type": f"{PREFIX}recipe_rate", "slug": "gnocchi", "rating": rating},
    )
    assert response["success"]
    posted = calls(mock_mealie, "POST", "/api/users/abc/ratings/gnocchi")
    assert [c[2] for c in posted] == [{"rating": rating}]


@pytest.mark.parametrize("rating", [5.5, -1, 6])
async def test_rate_rejects_out_of_range(setup_integration, mock_mealie, hass_ws_client, rating):
    mock_mealie.post(f"{MEALIE_URL}/api/users/abc/ratings/gnocchi", json={}, headers=JSON)
    response = await send(
        await hass_ws_client(),
        {"type": f"{PREFIX}recipe_rate", "slug": "gnocchi", "rating": rating},
    )
    assert response["error"]["code"] == "invalid_request"
    assert not calls(mock_mealie, "POST", "/api/users/abc/ratings/gnocchi")


async def test_rate_rejects_boolean(setup_integration, mock_mealie, hass_ws_client):
    response = await send(
        await hass_ws_client(),
        {"type": f"{PREFIX}recipe_rate", "slug": "gnocchi", "rating": True},
    )
    assert not response["success"]
    assert not calls(mock_mealie, "POST", "/api/users/abc/ratings/gnocchi")


@pytest.mark.parametrize("favorite", [True, False])
async def test_favorite_uses_post_or_delete(
    setup_integration, mock_mealie, hass_ws_client, favorite
):
    url = f"{MEALIE_URL}/api/users/abc/favorites/gnocchi"
    mock_mealie.post(url, json={}, headers=JSON)
    mock_mealie.delete(url, json={}, headers=JSON)
    response = await send(
        await hass_ws_client(),
        {"type": f"{PREFIX}recipe_favorite", "slug": "gnocchi", "favorite": favorite},
    )
    assert response["result"] == {"favorite": favorite}
    suffix = "/api/users/abc/favorites/gnocchi"
    assert len(calls(mock_mealie, "POST", suffix)) == (1 if favorite else 0)
    assert len(calls(mock_mealie, "DELETE", suffix)) == (0 if favorite else 1)


async def test_self_id_is_fetched_once(setup_integration, mock_mealie, hass_ws_client):
    mock_mealie.post(f"{MEALIE_URL}/api/users/abc/favorites/gnocchi", json={}, headers=JSON)
    client = await hass_ws_client()
    before = len(calls(mock_mealie, "GET", "/api/users/self"))
    for _ in range(2):
        await send(
            client, {"type": f"{PREFIX}recipe_favorite", "slug": "gnocchi", "favorite": True}
        )
    assert len(calls(mock_mealie, "GET", "/api/users/self")) - before == 1

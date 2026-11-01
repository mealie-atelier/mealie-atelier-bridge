"""The yield text follows the servings when it starts with the old servings number."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier.api import recipe_update_changes

from .mealie_fake import SLUG, FakeRecipeServer
from .test_recipe_editing import server  # noqa: F401


def _changes(servings, old, text):
    recipe = {"recipeServings": old, "recipeYield": text}
    return recipe_update_changes({"slug": "a", "servings": servings}, recipe)[1]


def test_decimal_comma_stays_a_comma():
    changes = _changes(2.5, 1.5, "1,5 Portionen")
    assert changes["recipeYield"] == "2,5 Portionen"
    assert changes["recipeYieldQuantity"] == 2.5


def test_whole_numbers_have_no_trailing_zero():
    changes = _changes(6, 4, "4 servings")
    assert changes["recipeYield"] == "6 servings"
    assert changes["recipeYieldQuantity"] == 6


def test_fraction_and_point_are_replaced():
    assert _changes(2, 0.5, "1/2 Blech")["recipeYield"] == "2 Blech"
    assert _changes(3, 1.5, "1.5 cups")["recipeYield"] == "3 cups"


@pytest.mark.parametrize(
    ("old", "text"),
    [
        (4, "1 Blech"),
        (4, ""),
        (4, "viele"),
        (4, "40 Stück"),
        (2, "2-3 Portionen"),
        (2, "2 – 3 Portionen"),
        (4, "4 to 6 servings"),
        (2, "2 bis 3 Portionen"),
    ],
)
def test_other_yield_text_is_untouched(old, text):
    changes = _changes(12, old, text)
    assert "recipeYield" not in changes
    assert "recipeYieldQuantity" not in changes


def test_without_recipe_only_servings_change():
    _slug, changes = recipe_update_changes({"slug": "a", "servings": 3})
    assert changes == {"recipeServings": 3}


async def _save_servings(client, servings):
    await client.send_json_auto_id(
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": SLUG, "servings": servings},
        }
    )
    return await client.receive_json()


async def test_websocket_servings_save_updates_yield_text(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,  # noqa: F811
    hass_ws_client: WebSocketGenerator,
):
    server.recipe.update(recipeServings=2.5, recipeYield="2,5 Portionen", recipeYieldQuantity=2.5)
    client = await hass_ws_client()

    response = await _save_servings(client, 3.5)

    assert response["success"], response
    assert server.recipe["recipeServings"] == 3.5
    assert server.recipe["recipeYield"] == "3,5 Portionen"
    assert server.recipe["recipeYieldQuantity"] == 3.5


async def test_websocket_servings_save_keeps_a_range_yield(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,  # noqa: F811
    hass_ws_client: WebSocketGenerator,
):
    server.recipe.update(recipeServings=2, recipeYield="2-3 Portionen", recipeYieldQuantity=2)
    client = await hass_ws_client()

    response = await _save_servings(client, 4)

    assert response["success"], response
    assert server.recipe["recipeServings"] == 4
    assert server.recipe["recipeYield"] == "2-3 Portionen"
    assert server.recipe["recipeYieldQuantity"] == 2

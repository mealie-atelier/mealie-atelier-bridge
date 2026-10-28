"""A tiny stateful Mealie recipe endpoint for aioclient_mock (fictional data only)."""

from __future__ import annotations

import copy
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from .conftest import JSON, MEALIE_URL

RECIPE_ID = "recipe-42"
SLUG = "gnocchi"
FLOUR = "5b6f3c0e-8a52-4b67-9c14-0d7f4a1e2b01"
STORE_KEY = "mealie_atelier_recipe_backups_test-entry"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PNG = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"\x00" * 200


def make_recipe() -> dict[str, Any]:
    return {
        "id": RECIPE_ID,
        "slug": SLUG,
        "name": "Gnocchi",
        "description": "Soft potato dumplings",
        "image": "abcd",
        "recipeServings": 4,
        "recipeYieldQuantity": 4,
        "recipeYield": "4 servings",
        "assets": [],
        "recipeIngredient": [
            {
                "referenceId": FLOUR,
                "quantity": 200.0,
                "unit": {"id": "unit-g", "name": "gram", "abbreviation": "g"},
                "food": {"id": "food-flour", "name": "flour"},
                "note": "",
                "originalText": "200 g flour",
                "display": "200 g flour",
                "title": None,
            }
        ],
        "recipeInstructions": [
            {"id": "step-a", "title": "", "text": "Boil the potatoes."},
            {"id": "step-b", "title": "", "text": "Knead the dough."},
        ],
    }


class FakeRecipeServer:
    """Serves one recipe, applies PATCH bodies and records the order of events."""

    def __init__(self, mock: AiohttpClientMocker, hass_storage: dict, recipe: dict | None = None):
        self.recipe = recipe or make_recipe()
        self.events: list[tuple] = []
        self.ignore_patch_keys: set[str] = set()
        self._storage = hass_storage
        base = f"{MEALIE_URL}/api/recipes/{SLUG}"
        mock.get(base, side_effect=self._get)
        mock.patch(base, side_effect=self._patch)
        mock.put(f"{base}/image", side_effect=self._put_image)
        mock.delete(f"{base}/image", side_effect=self._delete_image)
        mock.post(f"{base}/assets", side_effect=self._post_asset)
        mock.get(
            f"{MEALIE_URL}/api/media/recipes/{RECIPE_ID}/images/original.webp",
            content=WEBP,
            headers={"Content-Type": "image/webp"},
        )

    def snapshots(self) -> list[dict]:
        data = self._storage.get(STORE_KEY, {}).get("data", {})
        return data.get("recipes", {}).get(RECIPE_ID, [])

    def _reply(self, method: str, url, body: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=copy.deepcopy(body), headers=JSON)

    async def _get(self, method, url, data):
        self.events.append(("get",))
        return self._reply(method, url, self.recipe)

    async def _patch(self, method, url, data):
        self.events.append(("patch", len(self.snapshots()), copy.deepcopy(data)))
        for key, value in data.items():
            if key not in self.ignore_patch_keys:
                self.recipe[key] = copy.deepcopy(value)
        return self._reply(method, url, self.recipe)

    async def _put_image(self, method, url, data):
        self.events.append(("put_image", len(self.snapshots())))
        self.recipe["image"] = "efgh"
        return self._reply(method, url, {"image": "efgh"})

    async def _delete_image(self, method, url, data):
        self.events.append(("delete_image", len(self.snapshots())))
        self.recipe["image"] = ""
        return AiohttpClientMockResponse(method, url, status=200, json={}, headers=JSON)

    async def _post_asset(self, method, url, data):
        fields = {opts["name"]: value for opts, _headers, value in data._fields}
        self.events.append(("post_asset", len(self.snapshots()), fields["name"]))
        asset = {
            "name": fields["name"],
            "icon": "mdi-image",
            "fileName": f"{fields['name']}.{fields['extension']}",
        }
        return AiohttpClientMockResponse(method, url, json=asset, headers=JSON)

"""Real HTTP transport tests for the Mealie bridge client against a local fixture server.

These tests exercise the aiohttp client with a strict local Mealie fixture. A real
Mealie/HA compatibility test is still required before public stable release.
"""

from __future__ import annotations

import unittest
from uuid import UUID

import pytest
from aiohttp import ClientSession, web

from custom_components.mealie_atelier.api import (
    MealieClient,
    MealieError,
    safe_date,
    safe_id,
    safe_slug,
)


@pytest.mark.usefixtures("socket_enabled")
class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.batch_recipe_endpoint = True

        async def handler(request):
            path = request.path
            if request.headers.get("Authorization") != "Bearer MOCK_TOKEN":
                return web.json_response({"error": "Unauthorized"}, status=401)
            data = (
                await request.json()
                if request.can_read_body and request.method in {"POST", "PATCH", "PUT"}
                else None
            )
            self.calls.append(
                {"path": path, "query": dict(request.query), "method": request.method, "body": data}
            )
            if path == "/api/users/self":
                return web.json_response({"id": "abc", "username": "demo"})
            if path == "/api/households/mealplans" and request.method == "GET":
                if not request.query.get("start_date") or not request.query.get("end_date"):
                    return web.json_response({"error": "Date missing"}, status=422)
                return web.json_response(
                    [{"date": request.query["start_date"], "entryType": "dinner"}]
                )
            if path == "/api/households/shopping/lists" and request.method == "POST":
                return web.json_response({"id": "list-1", **data}, status=201)
            if path == "/api/households/shopping/lists/list-1/recipe" and request.method == "POST":
                if not self.batch_recipe_endpoint:
                    return web.json_response({"error": "Not found"}, status=404)
                self.assertEqual(data, [{"recipeId": "recipe-1"}])
                return web.json_response({"added": 2})
            if (
                path == "/api/households/shopping/lists/list-1/recipe/recipe-1"
                and request.method == "POST"
            ):
                return web.json_response({"added": 2})
            if path == "/api/recipes/lasagna" and request.method == "GET":
                return web.json_response(
                    {"id": "recipe-1", "name": "Lasagna", "recipeIngredient": []}
                )
            if path == "/api/media/recipes/recipe-1/assets/step-1.jpg":
                return web.Response(body=b"\xff\xd8\xffjpeg", content_type="image/jpeg")
            if path == "/api/recipes/lasagna" and request.method == "PATCH":
                return web.json_response({"id": "recipe-1", **data})
            if path in {"/api/organizers/categories", "/api/organizers/tags"}:
                return web.json_response({"items": [{"id": "tag-1", "name": "Vegetarisch"}]})
            if path == "/api/households/cookbooks":
                return web.json_response(
                    {
                        "items": [
                            {
                                "id": "book-1",
                                "slug": "schnell",
                                "name": "Schnell",
                                "queryFilter": {"parts": []},
                            }
                        ]
                    }
                )
            if path == "/api/households/shopping/items/item-1" and request.method == "GET":
                return web.json_response(
                    {"id": "item-1", "shoppingListId": "list-1", "checked": False, "note": "Milk"}
                )
            if path == "/api/households/shopping/items/item-1" and request.method == "PUT":
                return web.json_response(data)
            return web.json_response({"error": "Not found"}, status=404)

        self.app = web.Application()
        self.app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.session = ClientSession()
        self.client = MealieClient(self.session, f"http://127.0.0.1:{port}", "MOCK_TOKEN")

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    def test_id_and_date_validation(self):
        with self.assertRaises(ValueError):
            safe_slug("../../env")
        with self.assertRaises(ValueError):
            safe_id("id/with/path")
        with self.assertRaises(ValueError):
            safe_date("2026-02-30")
        self.assertEqual(safe_date("2028-02-29"), "2028-02-29")

    async def test_auth_and_dates(self):
        user = await self.client.check_connection()
        self.assertEqual(user["username"], "demo")
        entries = await self.client.mealplan("2026-10-27", "2026-11-02")
        self.assertEqual(entries[0]["entryType"], "dinner")
        self.assertEqual(
            self.calls[-1]["query"], {"start_date": "2026-10-27", "end_date": "2026-11-02"}
        )
        with self.assertRaises(ValueError):
            await self.client.mealplan("2026-12-30", "2026-12-01")

    async def test_household_actions(self):
        result = await self.client.mutate("shopping_list_create", {"name": "Weekend"})
        self.assertEqual(result["name"], "Weekend")
        result = await self.client.mutate(
            "shopping_from_recipe", {"list_id": "list-1", "recipe_id": "recipe-1"}
        )
        self.assertEqual(result, {"added": 2})
        self.assertEqual(self.calls[-1]["path"], "/api/households/shopping/lists/list-1/recipe")
        self.assertEqual(self.calls[-1]["body"], [{"recipeId": "recipe-1"}])
        self.batch_recipe_endpoint = False
        result = await self.client.mutate(
            "shopping_from_recipe", {"list_id": "list-1", "recipe_id": "recipe-1"}
        )
        self.assertEqual(result, {"added": 2})
        self.assertEqual(
            [call["path"] for call in self.calls[-2:]],
            [
                "/api/households/shopping/lists/list-1/recipe",
                "/api/households/shopping/lists/list-1/recipe/recipe-1",
            ],
        )
        item = await self.client.mutate("shopping_toggle", {"item_id": "item-1", "checked": True})
        self.assertTrue(item["checked"])
        self.assertEqual(item["note"], "Milk")
        with self.assertRaises(ValueError):
            await self.client.mutate(
                "shopping_from_recipe", {"list_id": "../secret", "recipe_id": "recipe-1"}
            )

    async def test_metadata_includes_household_cookbooks(self):
        result = await self.client.metadata()
        self.assertEqual(result["cookbooks"][0]["slug"], "schnell")
        self.assertTrue(
            any(
                call["path"] == "/api/households/cookbooks" and call["query"] == {"perPage": "-1"}
                for call in self.calls
            )
        )

    async def test_recipe_step_asset_is_constrained_and_sniffed(self):
        asset = await self.client.recipe_asset("recipe-1", "step-1.jpg")
        self.assertEqual(asset, (b"\xff\xd8\xffjpeg", "image/jpeg"))
        self.assertEqual(self.calls[-1]["path"], "/api/media/recipes/recipe-1/assets/step-1.jpg")
        with self.assertRaises(ValueError):
            await self.client.recipe_asset("recipe-1", "../secret")
        with self.assertRaises(ValueError):
            await self.client.recipe_asset("../recipe", "step-1.jpg")

    async def test_recipe_editor_sends_allowlisted_fields(self):
        result = await self.client.mutate(
            "recipe_update",
            {
                "slug": "lasagna",
                "name": "Sunday Lasagna",
                "description": "Very tasty",
                "servings": 4,
                "instructions": ["Preheat oven", "Bake"],
                "ingredients": ["200 g flour", "1 lemon"],
            },
        )
        self.assertEqual(result["recipeServings"], 4)
        self.assertEqual(result["recipeInstructions"], [{"text": "Preheat oven"}, {"text": "Bake"}])
        self.assertEqual(result["recipeIngredient"][0]["originalText"], "200 g flour")
        self.assertTrue(UUID(result["recipeIngredient"][0]["referenceId"]))
        self.assertNotIn("password", self.calls[-1]["body"])
        with self.assertRaises(ValueError):
            await self.client.mutate("recipe_update", {"slug": "lasagna", "servings": 0})
        with self.assertRaises(ValueError):
            await self.client.mutate("recipe_update", {"slug": "lasagna", "instructions": [""]})
        with self.assertRaises(ValueError):
            await self.client.mutate("recipe_update", {"slug": "lasagna", "ingredients": [" "]})
        with self.assertRaises(ValueError):
            await self.client.mutate("dangerous_operation", {})

    async def test_errors_do_not_echo_remote_body(self):
        with self.assertRaises(MealieError) as caught:
            await self.client.recipe("missing")
        self.assertIn("404", str(caught.exception))
        self.assertNotIn("Not found", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

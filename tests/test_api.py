"""Real HTTP transport tests for the Mealie bridge client against a local fixture server.

These tests exercise the aiohttp client with a strict local Mealie fixture. A real
Mealie/HA compatibility test is still required before public stable release.
"""

from __future__ import annotations

import asyncio
import unittest

import pytest
from aiohttp import ClientSession, web

from custom_components.mealie_atelier.const import MAX_PARALLEL_REQUESTS
from custom_components.mealie_atelier.errors import AtelierError
from custom_components.mealie_atelier.api import (
    MealieClient,
    MealieError,
    apply_shopping_changes,
    safe_date,
    safe_id,
    safe_slug,
    shopping_item_name,
)


# A 300 kB photo, well above one network chunk (Mealie photos are commonly 100 to 500 kB).
_BIG_CHUNK = b"WEBPVP8 " + bytes(range(256)) * 1172
BIG_WEBP = b"RIFF" + len(_BIG_CHUNK).to_bytes(4, "little") + _BIG_CHUNK
BIG_MEDIA = {
    "/api/media/recipes/recipe-big/images/min-original.webp": "image/webp",
    "/api/media/recipes/recipe-big/images/original.webp": "image/webp",
    "/api/media/recipes/recipe-big/assets/step-big.webp": "image/webp",
}


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
            if path in BIG_MEDIA:
                # Stream in small writes, like a real network, so one read() cannot see it all.
                response = web.StreamResponse(headers={"Content-Type": BIG_MEDIA[path]})
                await response.prepare(request)
                for start in range(0, len(BIG_WEBP), 16384):
                    await response.write(BIG_WEBP[start : start + 16384])
                    await asyncio.sleep(0)
                await response.write_eof()
                return response
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
            if path == "/api/households/shopping/items" and request.method == "POST":
                return web.json_response(data)
            if path == "/api/households/shopping/items" and request.method == "GET":
                return web.json_response(
                    {
                        "items": [
                            {"id": "item-1", "shoppingListId": "list-1", "checked": True},
                            {"id": "item-2", "shoppingListId": "list-1", "checked": False},
                            {"id": "item-3", "shoppingListId": "list-1", "checked": True},
                            {"id": "item-4", "shoppingListId": "other", "checked": True},
                        ]
                    }
                )
            if (
                path
                in (
                    "/api/households/shopping/items/item-1",
                    "/api/households/shopping/items/item-3",
                )
                and request.method == "DELETE"
            ):
                if path.endswith("item-3"):
                    return web.json_response({"error": "Not found"}, status=404)
                return web.json_response({"message": "deleted"})
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

    async def test_free_text_shopping_item_has_no_forced_quantity_of_one(self):
        await self.client.mutate("shopping_add", {"list_id": "list-1", "note": "Oat milk"})
        body = self.calls[-1]["body"]
        self.assertEqual(self.calls[-1]["method"], "POST")
        self.assertEqual(body["note"], "Oat milk")
        self.assertNotEqual(body["quantity"], 1)
        self.assertEqual(body["quantity"], 0)

    async def test_shopping_update_keeps_the_other_fields(self):
        item = await self.client.mutate(
            "shopping_update", {"item_id": "item-1", "note": " Oat milk ", "quantity": 2.5}
        )
        self.assertEqual(self.calls[-1]["method"], "PUT")
        self.assertEqual(item["note"], "Oat milk")
        self.assertEqual(item["quantity"], 2.5)
        self.assertEqual(item["shoppingListId"], "list-1")
        for bad in (
            {"item_id": "item-1"},
            {"item_id": "item-1", "quantity": True},
            {"item_id": "item-1", "quantity": -1},
            {"item_id": "item-1", "quantity": float("nan")},
            {"item_id": "item-1", "note": " "},
            {"item_id": "item-1", "name": " "},
            {"item_id": "item-1", "name": "A", "note": "b"},
            {"item_id": "item-1", "note": "x" * 251},
            {"item_id": "../x", "note": "a"},
        ):
            with self.assertRaises(ValueError):
                await self.client.mutate("shopping_update", bad)

    def test_rename_unlinks_the_food_and_an_empty_note_is_allowed_with_a_food(self):
        food_item = {
            "id": "i",
            "note": "bio",
            "display": "2 g Zucker",
            "isFood": True,
            "food": {"name": "Zucker"},
            "foodId": "f1",
            "quantity": 2,
        }
        renamed = apply_shopping_changes(food_item, {"name": "Rohrzucker"})
        self.assertEqual(shopping_item_name(renamed), "Rohrzucker")
        self.assertIsNone(renamed["food"])
        self.assertFalse(renamed["isFood"])
        self.assertEqual(renamed["quantity"], 2)
        cleared = apply_shopping_changes(food_item, {"note": ""})
        self.assertEqual(cleared["note"], "")
        self.assertEqual(shopping_item_name(cleared), "2 g Zucker")
        with self.assertRaises(ValueError):
            apply_shopping_changes({"id": "i", "note": "Milk"}, {"note": ""})

    async def test_online_update_and_delete_of_a_vanished_item(self):
        async def gone(*args, **kwargs):
            raise MealieError("gone", status=404)

        self.client.request = gone
        with self.assertRaises(AtelierError) as ctx:
            await self.client.mutate("shopping_update", {"item_id": "item-1", "note": "x"})
        self.assertEqual(ctx.exception.code, "shopping_item_not_found")
        result = await self.client.mutate("shopping_delete", {"item_id": "item-1"})
        self.assertEqual(result, {"ok": True, "already_applied": True})

    async def test_shopping_delete_and_clear_checked(self):
        await self.client.mutate("shopping_delete", {"item_id": "item-1"})
        self.assertEqual(self.calls[-1]["method"], "DELETE")
        with self.assertRaises(ValueError):
            await self.client.mutate("shopping_delete", {"item_id": "a/b"})
        self.calls.clear()
        with self.assertRaises(ValueError):
            await self.client.mutate(
                "shopping_clear_checked", {"list_id": "list-1", "expected_ids": []}
            )
        self.calls.clear()
        # Only items the user saw ticked go: item-3 was ticked elsewhere and survives.
        result = await self.client.mutate(
            "shopping_clear_checked", {"list_id": "list-1", "expected_ids": ["item-1"]}
        )
        self.assertEqual(result, {"deleted": 1})
        self.assertEqual(
            [c["path"] for c in self.calls if c["method"] == "DELETE"],
            ["/api/households/shopping/items/item-1"],
        )
        self.calls.clear()
        result = await self.client.mutate("shopping_clear_checked", {"list_id": "list-1"})
        # item-3 vanished meanwhile (404) and still counts; the other list and item-2 are untouched.
        self.assertEqual(result, {"deleted": 2})
        deletes = [c["path"] for c in self.calls if c["method"] == "DELETE"]
        self.assertEqual(
            deletes,
            [
                "/api/households/shopping/items/item-1",
                "/api/households/shopping/items/item-3",
            ],
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

    async def test_photos_arrive_whole_when_mealie_streams_them_in_chunks(self):
        self.assertEqual(await self.client.image("recipe-big"), BIG_WEBP)
        self.assertEqual(await self.client.original_image("recipe-big"), BIG_WEBP)
        asset = await self.client.recipe_asset("recipe-big", "step-big.webp")
        self.assertEqual(asset, (BIG_WEBP, "image/webp"))

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
            },
        )
        self.assertEqual(result["recipeServings"], 4)
        self.assertEqual(result["recipeInstructions"], [{"text": "Preheat oven"}, {"text": "Bake"}])
        self.assertNotIn("password", self.calls[-1]["body"])
        with self.assertRaises(ValueError):
            await self.client.mutate("recipe_update", {"slug": "lasagna", "servings": 0})
        with self.assertRaises(ValueError):
            await self.client.mutate("recipe_update", {"slug": "lasagna", "instructions": [""]})
        with self.assertRaises(AtelierError) as caught:
            await self.client.mutate("recipe_update", {"slug": "lasagna", "ingredients": ["x"]})
        self.assertEqual(caught.exception.code, "use_structured_editor")
        with self.assertRaises(ValueError):
            await self.client.mutate("dangerous_operation", {})

    async def test_errors_do_not_echo_remote_body(self):
        with self.assertRaises(MealieError) as caught:
            await self.client.recipe("missing")
        self.assertIn("404", str(caught.exception))
        self.assertNotIn("Not found", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


@pytest.mark.usefixtures("socket_enabled")
class ParallelLimitTests(unittest.IsolatedAsyncioTestCase):
    """Mealie hangs for good once too many requests arrive at once, so the client queues them."""

    async def asyncSetUp(self):
        self.in_flight = 0
        self.peak = 0

        async def handler(request):
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            await asyncio.sleep(0.02)
            self.in_flight -= 1
            if request.path.startswith("/api/media/"):
                return web.Response(body=b"RIFF0000WEBP", content_type="image/webp")
            return web.json_response({"items": []})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.session = ClientSession()
        self.client = MealieClient(self.session, f"http://127.0.0.1:{port}", "MOCK_TOKEN")

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def test_parallel_requests_are_capped(self):
        calls = [self.client.request("GET", "/api/recipes") for _ in range(30)]
        calls += [self.client.image("recipe-1") for _ in range(30)]
        results = await asyncio.gather(*calls)
        self.assertEqual(len(results), 60)
        self.assertEqual(self.peak, MAX_PARALLEL_REQUESTS)

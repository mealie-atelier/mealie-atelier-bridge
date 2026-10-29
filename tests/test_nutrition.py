"""Nutrition data-contract tests with fake USDA and AI HTTP endpoints.

No live external service and no live Mealie/Home Assistant installation is called.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest
from aiohttp import ClientSession, web

from custom_components.mealie_atelier import ai_client
from custom_components.mealie_atelier import nutrition as n
from custom_components.mealie_atelier.errors import AtelierError

RECIPE = {
    "slug": "haferflocken-mit-beeren",
    "name": "Haferflocken mit Beeren",
    "recipeServings": 2,
    "recipeIngredient": [
        {"display": "100 g Haferflocken", "quantity": 100, "unit": {"name": "g"}},
        {"display": "50 g Beeren", "quantity": 50, "unit": {"name": "g"}},
    ],
}

FOODS = {
    101: {
        "fdcId": 101,
        "description": "Oats - fake",
        "foodNutrients": [
            {"nutrient": {"id": 1008}, "amount": 300},
            {"nutrient": {"id": 1003}, "amount": 10},
            {"nutrient": {"id": 1004}, "amount": 5},
            {"nutrient": {"id": 1005}, "amount": 50},
            {"nutrient": {"id": 1093}, "amount": 4},
        ],
    },
    102: {
        "fdcId": 102,
        "description": "Berries - fake",
        "foodNutrients": [
            {"nutrient": {"id": 1008}, "amount": 80},
            {"nutrient": {"id": 1003}, "amount": 2},
            {"nutrient": {"id": 1004}, "amount": 1},
            {"nutrient": {"id": 1005}, "amount": 10},
        ],
    },
}


@pytest.mark.usefixtures("socket_enabled")
class NutritionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

        async def respond(request):
            self.calls.append(
                {"method": request.method, "path": request.path, "query": dict(request.query)}
            )
            if request.path.startswith("/fdc/"):
                if request.query.get("api_key") == "LIMITED_KEY":
                    return web.json_response({"error": "Slow down"}, status=429)
                if request.query.get("api_key") != "TEST_USDA_KEY":
                    return web.json_response({"error": "Bad key"}, status=401)
                if request.path.endswith("/foods/search"):
                    return web.json_response(
                        {
                            "foods": [
                                {
                                    "fdcId": data["fdcId"],
                                    "description": data["description"],
                                    "foodNutrients": [
                                        {"nutrientId": x["nutrient"]["id"], "value": x["amount"]}
                                        for x in data["foodNutrients"]
                                    ],
                                }
                                for data in FOODS.values()
                            ]
                        }
                    )
                try:
                    item = FOODS[int(request.path.rsplit("/", 1)[-1])]
                    return web.json_response(item)
                except (KeyError, ValueError):
                    return web.json_response({"error": "Not found"}, status=404)
            if request.path == "/v1/responses":
                data = await request.json()
                self.assertFalse(data.get("store"))
                self.assertEqual(request.headers.get("Authorization"), "Bearer TEST_AI_KEY")
                return web.json_response(
                    {
                        "output": [
                            {
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": '{"suggestions":[{"index":0,"query":"oats","note":""},'
                                        '{"index":1,"query":"berries","note":"fresh"}]}',
                                    }
                                ]
                            }
                        ]
                    }
                )
            return web.json_response({"error": "Not found"}, status=404)

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", respond)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.session = ClientSession()
        self.fdc_patch = patch.object(n, "_FDC", f"http://127.0.0.1:{port}/fdc")
        self.ai_patch = patch.object(
            ai_client, "OPENAI_URL", f"http://127.0.0.1:{port}/v1/responses"
        )
        self.fdc_patch.start()
        self.ai_patch.start()

    async def asyncTearDown(self):
        self.ai_patch.stop()
        self.fdc_patch.stop()
        await self.session.close()
        await self.runner.cleanup()

    def test_prepare_only_converts_known_mass_units(self):
        recipe = dict(
            RECIPE,
            recipeIngredient=[
                {"display": "100 g Haferflocken"},
                {"display": "0,5 kg Kartoffeln"},
                {"display": "2 EL Olivenoel"},
                {"quantity": 2, "unit": {"name": "kg"}, "food": {"name": "Flour"}},
            ],
        )
        prep = n.prepare(recipe, fdc_enabled=True, ai_enabled=False)
        self.assertEqual([row["grams"] for row in prep["ingredients"]], [100, 500, None, 2000])
        self.assertTrue(prep["ingredients"][2]["needs_review"])
        self.assertTrue(prep["fdc_enabled"])

    async def test_usda_search_and_correct_per_serving_calculation(self):
        found = await n.search(self.session, "TEST_USDA_KEY", "oats")
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["id"], 101)
        self.assertIn("food-details/101", found[0]["url"])
        info = n.prepare(RECIPE, fdc_enabled=True, ai_enabled=False)
        lines = [
            {"index": 0, "grams": 100, "fdc_id": 101},
            {"index": 1, "grams": 50, "fdc_id": 102},
        ]
        report = await n.calculate(
            self.session, "TEST_USDA_KEY", RECIPE, lines, expected_hash=info["hash"]
        )
        self.assertEqual(report["per_portion"]["calories"], 170.0)
        self.assertEqual(report["per_portion"]["protein"], 5.5)
        self.assertEqual(report["per_portion"]["carbohydrate"], 27.5)
        self.assertIsNone(report["per_portion"]["sodium"])
        self.assertEqual(report["coverage"]["sodium"], "1/2")
        payload = n.mealie_nutrition(report)
        self.assertEqual(payload["calories"], "170.0 kcal")
        self.assertEqual(payload["proteinContent"], "5.5 g")
        self.assertNotIn("sodiumContent", payload)
        self.assertEqual(self.calls[0]["query"].get("api_key"), "TEST_USDA_KEY")

    async def test_recipe_edits_incomplete_matches_and_bad_grains_rejected(self):
        info = n.prepare(RECIPE, fdc_enabled=True, ai_enabled=False)
        lines = [
            {"index": 0, "grams": 100, "fdc_id": 101},
            {"index": 1, "grams": 50, "fdc_id": 102},
        ]
        with self.assertRaisesRegex(ValueError, "changed"):
            await n.calculate(
                self.session,
                "TEST_USDA_KEY",
                dict(RECIPE, recipeServings=3),
                lines,
                expected_hash=info["hash"],
            )
        with self.assertRaisesRegex(ValueError, "grams"):
            await n.calculate(
                self.session,
                "TEST_USDA_KEY",
                RECIPE,
                [lines[0], {"index": 1, "grams": 0, "fdc_id": 102}],
                expected_hash=info["hash"],
            )
        with self.assertRaisesRegex(ValueError, "[Ee]ach recipe ingredient"):
            await n.calculate(
                self.session, "TEST_USDA_KEY", RECIPE, lines[:1], expected_hash=info["hash"]
            )
        with self.assertRaisesRegex(ValueError, "grams"):
            await n.calculate(
                self.session,
                "TEST_USDA_KEY",
                RECIPE,
                [lines[0], {"index": 1, "grams": True, "fdc_id": 102}],
                expected_hash=info["hash"],
            )

    async def test_rejected_usda_key_has_its_own_error_code(self):
        with self.assertRaises(AtelierError) as caught:
            await n.search(self.session, "WRONG_KEY", "oats")
        self.assertEqual(caught.exception.code, "usda_key_invalid")

    async def test_exhausted_usda_limit_has_its_own_error_code(self):
        with self.assertRaises(AtelierError) as caught:
            await n.search(self.session, "LIMITED_KEY", "oats")
        self.assertEqual(caught.exception.code, "usda_rate_limited")

    async def test_optional_ai_only_proposes_search_text(self):
        suggestions = await n.ai_suggest(
            self.session, "TEST_AI_KEY", ["100 g oats", "50 g berries"]
        )
        self.assertEqual([x["query"] for x in suggestions], ["oats", "berries"])
        with self.assertRaises(ValueError):
            await n.ai_suggest(self.session, "", ["something"])
        self.assertEqual(self.calls[-1]["path"], "/v1/responses")


if __name__ == "__main__":
    unittest.main()

"""The live rig seed puts every demo shopping item on the seeded list."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SEED = Path(__file__).parent / "live" / "seed_mealie.py"


def load_seed(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["seed_mealie.py", "http://mealie.invalid", str(token), "unused.json", "de"]
    )
    spec = importlib.util.spec_from_file_location("seed_mealie_under_test", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_item_on_another_list_is_still_seeded_on_the_demo_list(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    items = [
        {"id": "a", "shoppingListId": "demo", "note": "Milch"},
        {"id": "b", "shoppingListId": "other", "note": "Parmesan"},
    ]
    posted = []

    def call(method, path, body=None, **_):
        if method == "GET" and path.startswith("/api/households/shopping/lists"):
            return {
                "items": [{"id": "demo", "name": "Wocheneinkauf"}, {"id": "other", "name": "E2E"}]
            }
        if method == "GET" and path.startswith("/api/households/shopping/items"):
            return {"items": items}
        if method == "POST":
            posted.append(body)
        return None

    monkeypatch.setattr(seed, "call", call)
    seed.seed_shopping()
    notes = {body["note"] for body in posted}
    assert "Parmesan" in notes
    assert "Milch" not in notes
    assert all(body["shoppingListId"] == "demo" for body in posted)


def test_demo_list_is_picked_by_name_not_position(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    lists = [{"id": "other", "name": "E2E"}, {"id": "demo", "name": "Wocheneinkauf"}]

    def call(method, path, body=None, **_):
        assert method == "GET"
        return {"items": lists}

    monkeypatch.setattr(seed, "call", call)
    assert seed.demo_list_id() == "demo"


def test_demo_list_is_created_when_missing(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    created = []

    def call(method, path, body=None, **_):
        if method == "POST":
            created.append(body)
            return {"id": "new"}
        return {"items": [{"id": "other", "name": "E2E"}]}

    monkeypatch.setattr(seed, "call", call)
    assert seed.demo_list_id() == "new"
    assert created == [{"name": "Wocheneinkauf"}]


def recipe_fixture(**extra):
    recipe = {
        "id": "skillet-eggs",
        "ingredients": {
            "de": ["2 EL Olivenöl", "1 Zwiebel, in Streifen", "Salz und Pfeffer"],
            "en": ["2 tbsp olive oil", "1 onion, sliced", "Salt and pepper"],
        },
        "steps": {
            "de": [{"title": "A", "text": "a"}, {"title": "B", "text": "b"}],
            "en": [{"title": "A", "text": "a"}, {"title": "B", "text": "b"}],
        },
    }
    recipe.update(extra)
    return recipe


PARTS = {
    "de": [
        {"quantity": 2, "unit": "EL", "food": "Olivenöl", "note": ""},
        {"quantity": 1, "unit": None, "food": "Zwiebel", "note": "in Streifen"},
        None,
    ]
}


def fake_mealie(calls):
    units, foods = {}, {}

    def call(method, path, body=None, **_):
        calls.append((method, path, body))
        store = units if "/units" in path else foods
        if method == "GET":
            return {"items": [{"id": i, "name": n} for n, i in store.items()]}
        store[body["name"]] = f"id-{body['name']}"
        return {"id": store[body["name"]], "name": body["name"]}

    return call


def test_structured_rows_carry_quantity_unit_food_and_reference(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(seed, "call", fake_mealie(calls))
    rows = seed.ingredient_rows(recipe_fixture(ingredientParts=PARTS))
    assert rows[0]["quantity"] == 2
    assert rows[0]["unit"] == {"id": "id-EL", "name": "EL"}
    assert rows[0]["food"] == {"id": "id-Olivenöl", "name": "Olivenöl"}
    assert rows[0]["display"] == rows[0]["originalText"] == "2 EL Olivenöl"
    assert rows[1]["unit"] is None
    assert rows[1]["note"] == "in Streifen"
    assert rows[2]["quantity"] == 0
    assert rows[2]["note"] == rows[2]["display"] == "Salz und Pfeffer"
    assert "food" not in rows[2]
    assert all(row["referenceId"] for row in rows)


def test_units_and_foods_are_created_once_and_reused(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(seed, "call", fake_mealie(calls))
    recipe = recipe_fixture(ingredientParts=PARTS)
    seed.ingredient_rows(recipe)
    seed.ingredient_rows(recipe)
    posts = [body["name"] for method, _, body in calls if method == "POST"]
    assert sorted(posts) == ["EL", "Olivenöl", "Zwiebel"]
    assert sum(1 for method, *_ in calls if method == "GET") == 2


def test_reference_ids_are_stable_across_runs(tmp_path, monkeypatch):
    recipe = recipe_fixture(ingredientParts=PARTS)
    runs = []
    for _ in range(2):
        seed = load_seed(tmp_path, monkeypatch)
        monkeypatch.setattr(seed, "call", fake_mealie([]))
        runs.append([row["referenceId"] for row in seed.ingredient_rows(recipe)])
    assert runs[0] == runs[1]
    assert len(set(runs[0])) == 3


def test_step_references_map_to_the_linked_rows(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    monkeypatch.setattr(seed, "call", fake_mealie([]))
    recipe = recipe_fixture(ingredientParts=PARTS, stepIngredients=[[0, 1], [2]])
    ids = [row["referenceId"] for row in seed.ingredient_rows(recipe)]
    steps = seed.instruction_rows(recipe)
    assert steps[0]["ingredientReferences"] == [{"referenceId": ids[0]}, {"referenceId": ids[1]}]
    assert steps[1]["ingredientReferences"] == [{"referenceId": ids[2]}]


def test_catalog_without_the_new_fields_seeds_note_only_rows(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(seed, "call", fake_mealie(calls))
    recipe = recipe_fixture()
    rows = seed.ingredient_rows(recipe)
    assert [set(row) for row in rows] == [{"note", "display", "referenceId"}] * 3
    assert [row["note"] for row in rows] == recipe["ingredients"]["de"]
    assert all("ingredientReferences" not in step for step in seed.instruction_rows(recipe))
    assert calls == []


def test_seed_recipe_with_an_old_catalog_puts_note_only_rows(tmp_path, monkeypatch):
    seed = load_seed(tmp_path, monkeypatch)
    puts = []

    def call(method, path, body=None, **_):
        if method == "POST":
            return "skillet-eggs"
        if method == "PUT":
            puts.append(body)
        return {"slug": "skillet-eggs"}

    monkeypatch.setattr(seed, "call", call)
    recipe = recipe_fixture(
        title={"de": "Pfannen-Eier"},
        description={"de": "d"},
        servings=2,
        prepMinutes=5,
        cookMinutes=5,
    )
    seed.seed_recipe(recipe, {})
    assert [row["note"] for row in puts[0]["recipeIngredient"]] == recipe["ingredients"]["de"]
    assert len(puts[0]["recipeInstructions"]) == 2

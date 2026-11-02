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

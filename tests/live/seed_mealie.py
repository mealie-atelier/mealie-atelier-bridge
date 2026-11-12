"""Seed a disposable Mealie instance with the fictional Mealie Atelier demo recipes.

Usage: python3 -I seed_mealie.py <mealie-url> <api-token-file> <recipes.json> [de|en]

Never point this at a real household instance. Cover photos are uploaded only from local files
(`image.file`, relative to the catalog); the seed never downloads anything.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

BASE, TOKEN_FILE, CATALOG = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
LANG = sys.argv[4] if len(sys.argv) > 4 else "de"
TOKEN = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
# Fixed namespace: reference ids stay the same over reseeds, so step links survive.
REFERENCE_NAMESPACE = uuid.UUID("6f1b2c1e-5a53-4e0b-9d35-3c0f6f3a7b21")
# Units and foods by exact name, loaded once per run: {"units": {name: {id, name}}, "foods": {...}}.
_NAMED: dict[str, dict[str, dict]] = {}


def call(method: str, path: str, body=None, raw=None, ctype: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else None
    except urllib.error.HTTPError as err:
        # Mealie explains a 422 in the body; without it a failed seed says nothing useful.
        detail = err.read().decode(errors="replace")[:2000]
        raise RuntimeError(f"{method} {path} failed with HTTP {err.code}: {detail}") from err


def upload_image(slug: str, image: Path) -> None:
    boundary = uuid.uuid4().hex
    extension = image.suffix.lstrip(".") or "jpg"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="extension"\r\n\r\n{extension}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
        f'filename="cover.{extension}"\r\nContent-Type: image/{extension}\r\n\r\n'
    ).encode()
    parts = head + image.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    call("PUT", f"/api/recipes/{slug}/image", raw=parts, ctype=ctype)


def named(kind: str, name: str) -> dict:
    """The unit or food called `name`; created when Mealie does not know it yet."""
    if kind not in _NAMED:
        listing = call("GET", f"/api/{kind}?perPage=-1")
        items = listing["items"] if isinstance(listing, dict) else listing
        _NAMED[kind] = {item["name"]: {"id": item["id"], "name": item["name"]} for item in items}
    if name not in _NAMED[kind]:
        created = call("POST", f"/api/{kind}", {"name": name})
        _NAMED[kind][name] = {"id": created["id"], "name": created["name"]}
    return _NAMED[kind][name]


def reference_id(recipe_data: dict, index: int) -> str:
    """A stable id for one ingredient row. Mealie accepts only version 4 UUIDs as step references
    (HTTP 422 "UUID version 4 expected"), so the name-based hash is stamped as version 4."""
    stable = uuid.uuid5(REFERENCE_NAMESPACE, f"{recipe_data['id']}:{LANG}:{index}")
    return str(uuid.UUID(bytes=stable.bytes, version=4))


def ingredient_rows(recipe_data: dict) -> list[dict]:
    """Structured rows when the catalog has `ingredientParts`, else note-only rows as before."""
    lines = recipe_data["ingredients"][LANG]
    parts = (recipe_data.get("ingredientParts") or {}).get(LANG)
    if not parts:
        return [{"note": line, "display": line, "referenceId": str(uuid.uuid4())} for line in lines]
    rows = []
    for index, line in enumerate(lines):
        part = parts[index]
        if part is None:
            # Note-only row: Mealie adds it to a shopping list as written.
            row = {"note": line, "display": line, "quantity": 0}
        else:
            row = {
                "quantity": part["quantity"],
                "unit": named("units", part["unit"]) if part.get("unit") else None,
                "food": named("foods", part["food"]),
                "note": part.get("note") or "",
                "display": line,
                "originalText": line,
            }
        row["referenceId"] = reference_id(recipe_data, index)
        rows.append(row)
    return rows


def instruction_rows(recipe_data: dict) -> list[dict]:
    links = recipe_data.get("stepIngredients") or []
    rows = []
    for position, step in enumerate(recipe_data["steps"][LANG]):
        row = {
            "id": str(uuid.uuid4()),
            "title": step["title"],
            "summary": step.get("summary", ""),
            "text": step["text"],
        }
        if position < len(links):
            row["ingredientReferences"] = [
                {"referenceId": reference_id(recipe_data, i)} for i in links[position]
            ]
        rows.append(row)
    return rows


def seed_recipe(recipe_data: dict, existing: dict[str, str]) -> str:
    # Mealie derives its own slug (umlauts transliterate differently), so match by name.
    name = recipe_data["title"][LANG]
    slug = existing.get(name) or call("POST", "/api/recipes", {"name": name})
    recipe = call("GET", f"/api/recipes/{slug}")
    servings = recipe_data["servings"]
    prep, cook = recipe_data["prepMinutes"], recipe_data["cookMinutes"]
    recipe.update(
        {
            "description": recipe_data["description"][LANG],
            "recipeServings": servings,
            "recipeYield": f"{servings} Portionen" if LANG == "de" else f"{servings} servings",
            "prepTime": f"{prep} min",
            "performTime": f"{cook} min",
            "totalTime": f"{prep + cook} min",
            "recipeIngredient": ingredient_rows(recipe_data),
            "recipeInstructions": instruction_rows(recipe_data),
        }
    )
    call("PUT", f"/api/recipes/{slug}", recipe)
    local = (recipe_data.get("image") or {}).get("file")
    if local and (CATALOG.parent / local).is_file():
        upload_image(slug, CATALOG.parent / local)
    return slug


SHOPPING = {
    "de": ["Milch", "Eier", "Basilikum", "Zitronen", "Parmesan", "Haferflocken"],
    "en": ["Milk", "Eggs", "Basil", "Lemons", "Parmesan", "Rolled oats"],
}


LIST_NAMES = {"de": "Wocheneinkauf", "en": "Weekly shop"}


def demo_list_id() -> str:
    """Id of the demo shopping list by name; created when missing."""
    name = LIST_NAMES.get(LANG, LIST_NAMES["en"])
    for shopping_list in call("GET", "/api/households/shopping/lists?perPage=200")["items"]:
        if shopping_list.get("name") == name:
            return shopping_list["id"]
    return call("POST", "/api/households/shopping/lists", {"name": name})["id"]


def seed_shopping() -> None:
    """Fictional items on the demo list; leftovers of the live smoke are removed."""
    list_id = demo_list_id()
    items = call("GET", "/api/households/shopping/items?perPage=500")["items"]
    for item in items:
        if str(item.get("note") or "").startswith("Live smoke"):
            call("DELETE", f"/api/households/shopping/items/{item['id']}")
    # Only the seeded list counts: the same note on another list (left there by a test run) must not
    # keep the demo item off the list the screenshots show.
    notes = {item.get("note") for item in items if item.get("shoppingListId") == list_id}
    for index, note in enumerate(SHOPPING.get(LANG, SHOPPING["en"])):
        if note not in notes:
            item = {"shoppingListId": list_id, "note": note, "quantity": 0, "checked": index == 5}
            call("POST", "/api/households/shopping/items", item)


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {r["name"]: r["slug"] for r in call("GET", "/api/recipes?perPage=200")["items"]}
    slugs = []
    for recipe_data in catalog["recipes"]:
        slugs.append(seed_recipe(recipe_data, existing))
        print("seeded", slugs[-1])
    seed_shopping()
    recipes = {r["slug"]: r for r in call("GET", "/api/recipes?perPage=200")["items"]}
    today = date.today()
    planned = {
        (row["date"], row.get("recipeId"))
        for row in call(
            "GET",
            f"/api/households/mealplans?start_date={today}"
            f"&end_date={today + timedelta(days=4)}&perPage=200",
        )["items"]
    }
    for offset, slug in enumerate(slugs[:5]):
        day = (today + timedelta(days=offset)).isoformat()
        if (day, recipes[slug]["id"]) in planned:
            continue
        entry = {
            "date": day,
            "entryType": "dinner",
            "recipeId": recipes[slug]["id"],
        }
        call("POST", "/api/households/mealplans", entry)
    print("done", len(slugs), "recipes")


if __name__ == "__main__":
    main()

"""Seed a disposable Mealie instance with the fictional Mealie Atelier demo recipes.

Usage: python3 -I seed_mealie.py <mealie-url> <api-token-file> <recipes.json> [de|en]

Never point this at a real household instance. Cover photos are uploaded only from local files
(`image.file`, relative to the catalog); the seed never downloads anything.
"""

from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

BASE, TOKEN_FILE, CATALOG = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
LANG = sys.argv[4] if len(sys.argv) > 4 else "de"
TOKEN = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()


def call(method: str, path: str, body=None, raw=None, ctype: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode()
        return json.loads(text) if text else None


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
            "recipeIngredient": [
                {"note": line, "display": line, "referenceId": str(uuid.uuid4())}
                for line in recipe_data["ingredients"][LANG]
            ],
            "recipeInstructions": [
                {
                    "id": str(uuid.uuid4()),
                    "title": step["title"],
                    "summary": step.get("summary", ""),
                    "text": step["text"],
                }
                for step in recipe_data["steps"][LANG]
            ],
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


def seed_shopping() -> None:
    """Fictional items on the first list; leftovers of the live smoke are removed."""
    list_id = call("GET", "/api/households/shopping/lists")["items"][0]["id"]
    items = call("GET", "/api/households/shopping/items?perPage=500")["items"]
    for item in items:
        if str(item.get("note") or "").startswith("Live smoke"):
            call("DELETE", f"/api/households/shopping/items/{item['id']}")
    notes = {item.get("note") for item in items}
    for index, note in enumerate(SHOPPING.get(LANG, SHOPPING["en"])):
        if note not in notes:
            item = {"shoppingListId": list_id, "note": note, "quantity": 1, "checked": index == 5}
            call("POST", "/api/households/shopping/items", item)


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {r["name"]: r["slug"] for r in call("GET", "/api/recipes?perPage=200")["items"]}
    slugs = []
    for recipe_data in catalog["recipes"]:
        slugs.append(seed_recipe(recipe_data, existing))
        print("seeded", slugs[-1])
    if not call("GET", "/api/households/shopping/lists")["items"]:
        name = "Wocheneinkauf" if LANG == "de" else "Weekly shop"
        call("POST", "/api/households/shopping/lists", {"name": name})
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

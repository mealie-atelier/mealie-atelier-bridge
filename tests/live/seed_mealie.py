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


def seed_recipe(recipe_data: dict, existing: set[str]) -> str:
    slug = recipe_data["slug"]
    if slug not in existing:
        slug = call("POST", "/api/recipes", {"name": recipe_data["title"][LANG]})
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


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = {r["slug"] for r in call("GET", "/api/recipes?perPage=200")["items"]}
    slugs = []
    for recipe_data in catalog["recipes"]:
        slugs.append(seed_recipe(recipe_data, existing))
        print("seeded", slugs[-1])
    if not call("GET", "/api/households/shopping/lists")["items"]:
        name = "Wocheneinkauf" if LANG == "de" else "Weekly shop"
        call("POST", "/api/households/shopping/lists", {"name": name})
    recipes = {r["slug"]: r for r in call("GET", "/api/recipes?perPage=200")["items"]}
    today = date.today()
    for offset, slug in enumerate(slugs[:5]):
        entry = {
            "date": (today + timedelta(days=offset)).isoformat(),
            "entryType": "dinner",
            "recipeId": recipes[slug]["id"],
        }
        call("POST", "/api/households/mealplans", entry)
    print("done", len(slugs), "recipes")


if __name__ == "__main__":
    main()

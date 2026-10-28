"""Live acceptance smoke test of the Bridge against the disposable rig (fictional data only).

Usage: python3 tests/live/smoke.py [ha-url] [token-file]
Runs read commands, a recipe edit with backup and restore, the structured ingredient read and the
shopping sync read path. Exits non-zero on the first failed check and leaves the recipe as it was.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

from ha_ws import HaSocket, read_token

HERE = Path(__file__).parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:18123"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else str(HERE / "state" / "ha-token")


class Smoke:
    def __init__(self, socket: HaSocket) -> None:
        self.socket, self.passed = socket, 0

    async def ok(self, label: str, command: dict) -> object:
        success, value = await self.socket.call(command)
        if not success:
            raise SystemExit(f"FAIL {label}: {value}")
        self.passed += 1
        print(f"ok   {label}")
        return value

    async def refused(self, label: str, command: dict, code: str) -> None:
        success, value = await self.socket.call(command)
        if success or (value or {}).get("code") != code:
            raise SystemExit(f"FAIL {label}: expected {code}, got {value}")
        self.passed += 1
        print(f"ok   {label} ({code})")

    def check(self, label: str, condition: bool) -> None:
        if not condition:
            raise SystemExit(f"FAIL {label}")
        self.passed += 1
        print(f"ok   {label}")


async def main() -> None:
    async with HaSocket(BASE, read_token(TOKEN)) as socket:
        smoke = Smoke(socket)
        status = await smoke.ok("status", {"type": "mealie_atelier/status"})
        entry = status["entries"][0]
        smoke.check(
            "status: connected with admin rights",
            entry["connected"] and entry["permissions"]["can_admin"],
        )
        smoke.check("status: upload limits", entry["limits"]["upload_chunk_bytes"] > 0)

        recipes = await smoke.ok("recipes", {"type": "mealie_atelier/recipes", "per_page": 5})
        smoke.check("recipes: seeded catalogue", recipes["total"] >= 10)
        found = await smoke.ok(
            "recipes: search", {"type": "mealie_atelier/recipes", "search": "Gnocchi"}
        )
        smoke.check("recipes: search finds Gnocchi", found["total"] >= 1)
        slug = found["items"][0]["slug"]
        recipe = await smoke.ok("recipe", {"type": "mealie_atelier/recipe", "slug": slug})
        today = date.today()
        plan = {
            "type": "mealie_atelier/mealplan",
            "start": today.isoformat(),
            "end": (today + timedelta(days=6)).isoformat(),
        }
        await smoke.ok("mealplan", plan)
        await smoke.ok("shopping", {"type": "mealie_atelier/shopping"})
        await smoke.ok("metadata", {"type": "mealie_atelier/metadata"})
        await smoke.ok("foods", {"type": "mealie_atelier/foods", "search": ""})
        await smoke.ok("units", {"type": "mealie_atelier/units", "search": ""})
        await smoke.ok("ingredients_get", {"type": "mealie_atelier/ingredients_get", "slug": slug})

        original = recipe.get("description") or ""
        marker = "Live smoke edit"
        update = {"slug": slug, "description": marker}
        await smoke.ok(
            "recipe_update",
            {"type": "mealie_atelier/mutate", "action": "recipe_update", "payload": update},
        )
        edited = await smoke.ok(
            "recipe after edit", {"type": "mealie_atelier/recipe", "slug": slug}
        )
        smoke.check("recipe_update reached Mealie", edited.get("description") == marker)
        backups = await smoke.ok(
            "recipe_backups", {"type": "mealie_atelier/recipe_backups", "slug": slug}
        )
        newest = (backups.get("backups") or backups)[0]
        smoke.check("backup taken before the write", newest["kind"] == "basics")
        await smoke.ok(
            "recipe_restore",
            {
                "type": "mealie_atelier/recipe_restore",
                "slug": slug,
                "backup_id": newest["backup_id"],
            },
        )
        restored = await smoke.ok(
            "recipe restored", {"type": "mealie_atelier/recipe", "slug": slug}
        )
        smoke.check("restore brings the description back", restored.get("description") == original)
        await smoke.refused(
            "plain-text ingredients are refused",
            {
                "type": "mealie_atelier/mutate",
                "action": "recipe_update",
                "payload": {"slug": slug, "ingredients": ["1 egg"]},
            },
            "use_structured_editor",
        )

        await smoke.ok("sync_state", {"type": "mealie_atelier/sync_state"})
        await smoke.ok("sync_destinations", {"type": "mealie_atelier/sync_destinations"})
        print(f"{smoke.passed} checks passed")


if __name__ == "__main__":
    asyncio.run(main())

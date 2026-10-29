"""Live acceptance test of Smart Shopping Sync against the disposable rig (fictional data only).

Usage: python3 tests/live/sync_live.py [ha-url] [mealie-url]
Syncs a throwaway Mealie list into Home Assistant's built-in shopping list (`todo.shopping_list`,
or the To-do entity named in `SYNC_TODO`, for example a dedicated Bring! test list) and checks the preview, the first-batch gate, exact-once delivery, duplicate skipping, the
after-add trigger and move mode. Every list, rule and To-do item it creates is removed again.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from ha_ws import HaSocket, read_token
from smoke import Smoke

HERE = Path(__file__).parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:18123"
MEALIE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:19925"
TODO = os.environ.get("SYNC_TODO", "todo.shopping_list")
PREFIX = "Live sync"


def mealie(method: str, path: str, body: object = None) -> object:
    request = urllib.request.Request(
        MEALIE + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {read_token(str(HERE / 'state' / 'mealie-token'))}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def mealie_items(list_id: str) -> list[dict]:
    return mealie("GET", f"/api/households/shopping/lists/{list_id}")["listItems"]


class SyncCheck(Smoke):
    async def todo_items(self) -> list[dict]:
        success, value = await self.socket.call({"type": "todo/item/list", "entity_id": TODO})
        if not success:
            raise SystemExit(f"FAIL todo/item/list: {value}")
        return value["items"]

    async def todo_titles(self) -> list[str]:
        return [item["summary"] for item in await self.todo_items()]

    async def todo(self, service: str, data: dict) -> None:
        await self.ok(
            f"todo.{service}",
            {
                "type": "call_service",
                "domain": "todo",
                "service": service,
                "target": {"entity_id": TODO},
                "service_data": data,
            },
        )

    async def wait_for(self, label: str, predicate, seconds: float = 30) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate(await self.todo_titles()):
                return self.check(label, True)
            await asyncio.sleep(1)
        self.check(label, False)


def count(titles: list[str], text: str) -> int:
    return sum(1 for title in titles if text.lower() in title.lower())


async def copy_rule(check: SyncCheck, tag: str) -> None:
    listed = mealie("POST", "/api/households/shopping/lists", {"name": f"{PREFIX} copy {tag}"})
    list_id = listed["id"]
    rule_id = None
    try:
        notes = [f"{PREFIX} apples {tag}", f"{PREFIX} oats {tag}", f"{PREFIX} lemons {tag}"]
        for note in notes:
            mealie(
                "POST",
                "/api/households/shopping/items",
                {"shoppingListId": list_id, "note": note, "quantity": 0, "checked": False},
            )
        # Already on the To-do list, so the first batch must skip it.
        await check.todo("add_item", {"item": notes[1]})
        draft = {
            "name": f"{PREFIX} copy",
            "source_list_id": list_id,
            "destination_entity_id": TODO,
            "mode": "copy",
            "triggers": {"after_atelier_add": True, "watch": False},
            "debounce_seconds": 5,
            "duplicate_policy": "skip_exact",
        }
        preview = await check.ok(
            "sync_preview (draft)", {"type": "mealie_atelier/sync_preview", "rule": draft}
        )
        actions = {line["title"]: line["action"] for line in preview["lines"]}
        check.check("preview lists all three items", len(actions) == 3)
        check.check(
            "preview marks the existing item as duplicate",
            "send" not in actions.get(notes[1], "send"),
        )
        check.check("preview wrote nothing", count(await check.todo_titles(), notes[0]) == 0)

        saved = await check.ok(
            "sync_rule_save", {"type": "mealie_atelier/sync_rule_save", "rule": draft}
        )
        rule_id = saved["rule"]["id"]
        await check.refused(
            "sync_run before the first batch is confirmed",
            {"type": "mealie_atelier/sync_run", "rule_id": rule_id},
            "sync_not_activated",
        )
        result = await check.ok(
            "sync_run (confirm first batch)",
            {"type": "mealie_atelier/sync_run", "rule_id": rule_id, "confirm_first_batch": True},
        )
        check.check(
            f"first batch sent 2, skipped 1 ({result})",
            result["sent"] == 2 and result["skipped_duplicates"] == 1,
        )
        titles = await check.todo_titles()
        check.check(
            "each item arrived exactly once",
            all(count(titles, note) == 1 for note in notes),
        )
        again = await check.ok(
            "sync_run again", {"type": "mealie_atelier/sync_run", "rule_id": rule_id}
        )
        check.check(f"a second run sends nothing ({again['outcome']})", again["sent"] == 0)
        titles = await check.todo_titles()
        check.check(
            "no duplicates after the second run", all(count(titles, note) == 1 for note in notes)
        )

        added = f"{PREFIX} basil {tag}"
        await check.ok(
            "shopping_add through the Bridge",
            {
                "type": "mealie_atelier/mutate",
                "action": "shopping_add",
                "payload": {"list_id": list_id, "note": added},
            },
        )
        await check.wait_for(
            "after-add trigger delivers the new item after the debounce",
            lambda titles: count(titles, added) == 1,
        )
        state = await check.ok("sync_state", {"type": "mealie_atelier/sync_state"})
        rule = next(r for r in state["rules"] if r["id"] == rule_id)
        check.check(
            "rule is active and reports its last run", rule["activated"] and rule["last_run"]
        )
        source = mealie_items(list_id)
        check.check(
            "copy mode leaves Mealie unchecked",
            len(source) == 4 and not any(item["checked"] for item in source),
        )
    finally:
        # Each cleanup runs even when the other one fails.
        try:
            mealie("DELETE", f"/api/households/shopping/lists/{list_id}")
        finally:
            if rule_id:
                await check.ok(
                    "sync_rule_delete (copy)",
                    {"type": "mealie_atelier/sync_rule_delete", "rule_id": rule_id},
                )


async def move_rule(check: SyncCheck, tag: str) -> None:
    listed = mealie("POST", "/api/households/shopping/lists", {"name": f"{PREFIX} move {tag}"})
    list_id = listed["id"]
    rule_id = None
    try:
        note = f"{PREFIX} flour {tag}"
        mealie(
            "POST",
            "/api/households/shopping/items",
            {"shoppingListId": list_id, "note": note, "quantity": 0, "checked": False},
        )
        draft = {
            "source_list_id": list_id,
            "destination_entity_id": TODO,
            "mode": "move",
            "triggers": {"after_atelier_add": False, "watch": False},
        }
        saved = await check.ok(
            "sync_rule_save (move)", {"type": "mealie_atelier/sync_rule_save", "rule": draft}
        )
        rule_id = saved["rule"]["id"]
        result = await check.ok(
            "sync_run move",
            {"type": "mealie_atelier/sync_run", "rule_id": rule_id, "confirm_first_batch": True},
        )
        check.check(f"move sent the item ({result['outcome']})", result["sent"] == 1)
        check.check("item arrived once", count(await check.todo_titles(), note) == 1)
        source = mealie_items(list_id)
        check.check(
            "move mode checked the item off in Mealie",
            len(source) == 1 and all(item["checked"] for item in source),
        )
        await check.ok("sync_records", {"type": "mealie_atelier/sync_records", "rule_id": rule_id})
    finally:
        # Each cleanup runs even when the other one fails.
        try:
            mealie("DELETE", f"/api/households/shopping/lists/{list_id}")
        finally:
            if rule_id:
                await check.ok(
                    "sync_rule_delete (move)",
                    {"type": "mealie_atelier/sync_rule_delete", "rule_id": rule_id},
                )


async def cleanup(check: SyncCheck) -> None:
    leftovers = [
        # Bring! puts the quantity in front ("1 Live sync basil"), so match anywhere in the title.
        item["uid"]
        for item in await check.todo_items()
        if PREFIX in item["summary"]
    ]
    if leftovers:
        await check.todo("remove_item", {"item": leftovers})
    check.check(
        "To-do list is clean again",
        not any(PREFIX in t for t in await check.todo_titles()),
    )


async def main() -> None:
    async with HaSocket(BASE, read_token(str(HERE / "state" / "ha-token"))) as socket:
        check = SyncCheck(socket)
        destinations = await check.ok(
            "sync_destinations", {"type": "mealie_atelier/sync_destinations"}
        )
        check.check(
            f"{TODO} is a usable destination",
            any(
                d["entity_id"] == TODO and d["supports"]["create"] for d in destinations["entities"]
            ),
        )
        tag = uuid.uuid4().hex[:6]
        try:
            await copy_rule(check, tag)
            await move_rule(check, tag)
        finally:
            await cleanup(check)
        print(f"{check.passed} checks passed")


if __name__ == "__main__":
    asyncio.run(main())

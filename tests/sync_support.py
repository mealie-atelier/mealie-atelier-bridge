"""Test doubles for shopping sync: a stateful fake Mealie list and a controllable To-do entity.

Both run inside real Home Assistant: the fake Mealie answers through `aioclient_mock`, and the
To-do entity is registered on a real `todo` component, so every add and read goes through the
core `todo.add_item` and `todo.get_items` services.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from .conftest import JSON, MEALIE_URL

WEEKLY = "list-weekly"
PARTY = "list-party"
ITEMS_PATH = "/api/households/shopping/items"


def unit(name: str, *, abbreviation: str = "", plural: str = "", fraction: bool = False) -> dict:
    return {
        "id": f"unit-{name}",
        "name": name,
        "pluralName": plural or None,
        "abbreviation": abbreviation,
        "useAbbreviation": bool(abbreviation),
        "fraction": fraction,
    }


def food(name: str, *, plural: str = "") -> dict:
    return {"id": f"food-{name.lower()}", "name": name, "pluralName": plural or None}


def mealie_item(
    item_id: str,
    *,
    list_id: str = WEEKLY,
    food: dict | None = None,
    unit: dict | None = None,
    quantity: float | None = 1,
    note: str = "",
    display: str = "",
    checked: bool = False,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "shoppingListId": list_id,
        "checked": checked,
        "position": 0,
        "quantity": quantity,
        "note": note,
        "display": display,
        "food": food,
        "foodId": food["id"] if food else None,
        "unit": unit,
        "unitId": unit["id"] if unit else None,
    }


class FakeMealie:
    """Shopping lists and items that answer GET and PUT like Mealie v3."""

    def __init__(self, mock: AiohttpClientMocker) -> None:
        self.lists = [
            {"id": WEEKLY, "name": "Weekly groceries"},
            {"id": PARTY, "name": "Party"},
        ]
        self.items: dict[str, dict[str, Any]] = {}
        self.puts: list[str] = []
        self.list_reads = 0
        self.fail_reads = False
        self.fail_puts = False
        base = re.escape(MEALIE_URL + ITEMS_PATH)
        mock.get(f"{MEALIE_URL}/api/households/shopping/lists", side_effect=self._lists)
        mock.get(re.compile(rf"^{base}/[\w-]+$"), side_effect=self._item)
        mock.put(re.compile(rf"^{base}/[\w-]+$"), side_effect=self._put)
        mock.get(re.compile(rf"^{base}(\?.*)?$"), side_effect=self._items)

    def add(self, *items: dict[str, Any]) -> None:
        for item in items:
            self.items[item["id"]] = item

    def respond(self, method, url, status=200, payload=None) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, status=status, json=payload, headers=JSON)

    async def _lists(self, method, url, data):
        return self.respond(method, url, payload={"items": self.lists})

    async def _items(self, method, url, data):
        self.list_reads += 1
        if self.fail_reads:
            return self.respond(method, url, status=500, payload={})
        wanted = parse_qs(url.query_string).get("queryFilter", [""])[0]
        # Deliberately ignore the filter for one foreign item: the client must filter again.
        rows = [
            item
            for item in self.items.values()
            if f'"{item["shoppingListId"]}"' in wanted or item["id"] == "foreign-1"
        ]
        return self.respond(method, url, payload={"items": rows})

    async def _item(self, method, url, data):
        item = self.items.get(url.path.rsplit("/", 1)[-1])
        if item is None:
            return self.respond(method, url, status=404, payload={})
        return self.respond(method, url, payload=item)

    async def _put(self, method, url, data):
        item_id = url.path.rsplit("/", 1)[-1]
        self.puts.append(item_id)
        if self.fail_puts or item_id not in self.items:
            return self.respond(method, url, status=500, payload={})
        self.items[item_id] = {**self.items[item_id], "checked": data["checked"]}
        return self.respond(method, url, payload={"updatedItems": [self.items[item_id]]})


class FakeTodo(TodoListEntity):
    """A To-do list whose add can succeed, reject, hang, or hang after storing the item."""

    _attr_should_poll = False

    def __init__(self, name: str, unique_id: str, *, description: bool = True) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        features = (
            TodoListEntityFeature.CREATE_TODO_ITEM
            | TodoListEntityFeature.UPDATE_TODO_ITEM
            | TodoListEntityFeature.DELETE_TODO_ITEM
        )
        if description:
            features |= TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
        self._attr_supported_features = features
        self._attr_todo_items = []
        self.calls: list[str] = []
        self.descriptions: list[str | None] = []
        self.mode = "ok"
        self.reject: set[str] = set()
        self.on_create = None
        self.gate: asyncio.Event | None = None
        self.active = 0
        self.max_active = 0

    def seed(self, summary: str, *, completed: bool = False) -> None:
        self._attr_todo_items.append(
            TodoItem(
                summary=summary,
                uid=uuid4().hex,
                status=TodoItemStatus.COMPLETED if completed else TodoItemStatus.NEEDS_ACTION,
            )
        )

    @property
    def summaries(self) -> list[str]:
        return [item.summary for item in self._attr_todo_items]

    def set_available(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        self.calls.append(item.summary)
        self.descriptions.append(item.description)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.mode == "reject" or item.summary in self.reject:
                raise HomeAssistantError("Destination rejected the item")
            if self.mode == "hang":
                await asyncio.Event().wait()
            self._attr_todo_items.append(
                TodoItem(
                    summary=item.summary,
                    uid=uuid4().hex,
                    status=TodoItemStatus.NEEDS_ACTION,
                    description=item.description,
                )
            )
            self.async_write_ha_state()
            if self.on_create is not None:
                self.on_create(item.summary)
            if self.mode == "hang_after_store":
                await asyncio.Event().wait()
        finally:
            self.active -= 1


async def setup_todo(hass: HomeAssistant, *entities: FakeTodo) -> None:
    setup_test_component_platform(hass, "todo", list(entities))
    assert await async_setup_component(hass, "todo", {"todo": {"platform": "test"}})
    await hass.async_block_till_done()


def draft(entity_id: str = "todo.einkauf", **overrides: Any) -> dict[str, Any]:
    return {
        "source_list_id": WEEKLY,
        "destination_entity_id": entity_id,
        "mode": "copy",
        "triggers": {"after_atelier_add": True, "watch": False},
        "debounce_seconds": 30,
        "poll_interval_seconds": 120,
        "duplicate_policy": "skip_exact",
        **overrides,
    }

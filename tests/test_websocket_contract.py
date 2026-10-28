"""WebSocket handler integration under a minimal HA API contract adapter.

NOT an installed Home Assistant runtime. Verifies actual handler logic and access
checks with mocked HA transports, using the integration's unmodified source.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "mealie_atelier"


def package(name):
    if name not in sys.modules:
        m = types.ModuleType(name)
        m.__path__ = []
        sys.modules[name] = m
    return sys.modules[name]


def module(name, **values):
    m = types.ModuleType(name)
    m.__dict__.update(values)
    sys.modules[name] = m
    return m


package("homeassistant")
package("homeassistant.components")
package("homeassistant.components.http")
package("homeassistant.helpers")
module("probatio", Required=lambda key, **kw: key, Optional=lambda key, **kw: key)
registered = []
ws = module(
    "homeassistant.components.websocket_api",
    websocket_command=lambda schema: lambda func: func,
    async_response=lambda func: func,
    async_register_command=lambda hass, handler: registered.append(handler),
)
sys.modules["homeassistant.components"].websocket_api = ws
module(
    "homeassistant.components.http.auth",
    async_sign_path=lambda hass, path, ttl: path + "?signature=TEST_ONLY",
)
module("homeassistant.core", HomeAssistant=object)
module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
package("mealie_atelier").__path__ = [str(ROOT)]
for name in ("const", "api", "nutrition", "studio", "studio_ai", "websocket"):
    fullname = f"mealie_atelier.{name}"
    if fullname in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(fullname, ROOT / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = m
    spec.loader.exec_module(m)
from mealie_atelier import websocket as handlers
from mealie_atelier.const import CONF_EXPERIMENTAL_INGREDIENTS, CONF_READ_ONLY, DOMAIN
from mealie_atelier.studio import initial as studio_initial


class Client:
    def __init__(self):
        self.operations = []

    async def recipe(self, slug):
        return {
            "id": "recipe-42",
            "slug": slug,
            "image": "image.webp",
            "recipeIngredient": [],
            "recipeInstructions": [
                {
                    "title": "Prepare",
                    "text": 'Cut vegetables. <img src="/api/media/recipes/recipe-42/assets/step-1.jpg" width="100%"><img src="https://unsafe.example/step.jpg">',
                }
            ],
        }

    async def mutate(self, action, payload):
        self.operations.append((action, payload))
        return {"saved": True}

    async def recipes(self, page, per_page, search):
        return {"items": [{"id": "recipe-42", "slug": "gnocchi", "image": "image.webp"}]}


class Conn:
    def __init__(self, admin=False, userid="user-1"):
        self.user = SimpleNamespace(is_admin=admin, id=userid)
        self.sent = []

    def send_result(self, id, body):
        self.sent.append(("ok", id, body))

    def send_error(self, id, code, message):
        self.sent.append(("error", id, code, message))

    def result(self):
        return self.sent[-1]


class Store:
    def __init__(self):
        self.saves = []

    async def async_save(self, value):
        self.saves.append(value)


class Harness:
    def __init__(self, options=None):
        self.client = Client()
        self.entry = SimpleNamespace(
            entry_id="test-entry", title="Studio Kitchen", options=options or {}
        )
        self.store = Store()
        self.state = studio_initial()
        self.data = {
            DOMAIN: {"test-entry": self.client},
            DOMAIN + "_studio": {
                "test-entry": {"state": self.state, "store": self.store, "lock": asyncio.Lock()}
            },
        }
        self.config_entries = SimpleNamespace(
            async_get_entry=lambda id: self.entry if id == "test-entry" else None,
            async_entries=lambda domain: [self.entry] if domain == DOMAIN else [],
        )


class WSContracts(unittest.IsolatedAsyncioTestCase):
    async def test_registration_and_signed_recipe_images(self):
        hass = Harness()
        registered.clear()
        handlers.async_register_commands(hass)
        self.assertEqual(len(registered), 20)
        self.assertIn(handlers.ws_alarm_create, registered)
        self.assertIn(handlers.ws_alarm_cancel, registered)
        self.assertIn(handlers.ws_alarm_capabilities, registered)
        conn = Conn()
        await handlers.ws_recipe(hass, conn, {"id": 1, "slug": "gnocchi"})
        self.assertEqual(conn.result()[0], "ok")
        self.assertEqual(
            conn.result()[2]["imageUrl"],
            "/api/mealie_atelier/image/test-entry/recipe-42?signature=TEST_ONLY",
        )
        step = conn.result()[2]["recipeInstructions"][0]
        self.assertEqual(step["text"], "Cut vegetables.")
        self.assertEqual(
            step["imageUrls"],
            [
                "/api/mealie_atelier/recipe_asset/test-entry/recipe-42/step-1.jpg?signature=TEST_ONLY"
            ],
        )
        self.assertNotIn("api_token", str(conn.result()).lower())

    async def test_only_admin_can_manage_recipe_and_experimental_ingredients(self):
        hass = Harness()
        ordinary = Conn()
        await handlers.ws_mutation(
            hass,
            ordinary,
            {
                "id": 2,
                "action": "recipe_update",
                "payload": {"slug": "gnocchi", "name": "New name"},
            },
        )
        self.assertEqual(ordinary.result()[2], "unauthorized")
        self.assertEqual(hass.client.operations, [])
        admin = Conn(admin=True)
        await handlers.ws_mutation(
            hass,
            admin,
            {
                "id": 3,
                "action": "recipe_update",
                "payload": {"slug": "gnocchi", "ingredients": ["200 g flour"]},
            },
        )
        self.assertEqual(admin.result()[2], "disabled")
        self.assertEqual(hass.client.operations, [])
        hass.entry.options[CONF_EXPERIMENTAL_INGREDIENTS] = True
        await handlers.ws_mutation(
            hass,
            admin,
            {
                "id": 4,
                "action": "recipe_update",
                "payload": {"slug": "gnocchi", "name": "New name"},
            },
        )
        self.assertEqual(admin.result()[0], "ok")
        self.assertEqual(hass.client.operations[0][0], "recipe_update")

    async def test_readonly_denies_all_writes_and_exposes_status(self):
        hass = Harness({CONF_READ_ONLY: True})
        conn = Conn(admin=True)
        await handlers.ws_status(hass, conn, {"id": 1})
        entry = conn.result()[2]["entries"][0]
        self.assertFalse(entry["can_write"])
        self.assertFalse(entry["can_manage"])
        await handlers.ws_mutation(
            hass,
            conn,
            {"id": 2, "action": "shopping_add", "payload": {"list_id": "list-1", "note": "Bread"}},
        )
        self.assertEqual(conn.result()[2], "read_only")
        await handlers.ws_studio_update(
            hass,
            conn,
            {
                "id": 3,
                "action": "pantry_put",
                "payload": {"name": "Rice", "amount": 100, "unit": "g"},
            },
        )
        self.assertEqual(conn.result()[2], "read_only")
        self.assertEqual(hass.store.saves, [])
        self.assertEqual(hass.client.operations, [])

    async def test_studio_revision_conflict_and_user_isolation(self):
        hass = Harness()
        first = Conn(admin=True, userid="user-one")
        second = Conn(admin=True, userid="user-two")
        await handlers.ws_studio_update(
            hass,
            first,
            {
                "id": 1,
                "action": "journal_set",
                "payload": {"slug": "gnocchi", "note": "More sage", "rating": 5, "favorite": True},
                "expected_revision": 0,
            },
        )
        self.assertEqual(first.result()[0], "ok")
        self.assertEqual(hass.store.saves[0]["revision"], 1)
        await handlers.ws_studio_update(
            hass,
            second,
            {
                "id": 2,
                "action": "journal_set",
                "payload": {"slug": "gnocchi", "note": "Less salt", "rating": 4},
                "expected_revision": 0,
            },
        )
        self.assertEqual(second.result()[2], "conflict")
        await handlers.ws_studio_state(hass, second, {"id": 3})
        self.assertEqual(second.result()[2]["journal"], {})
        await handlers.ws_studio_state(hass, first, {"id": 4})
        self.assertEqual(first.result()[2]["journal"]["gnocchi"]["rating"], 5)
        self.assertEqual(len(hass.store.saves), 1)

    async def test_nonadmin_cannot_assign_child_task(self):
        hass = Harness()
        ordinary = Conn(admin=False)
        await handlers.ws_studio_update(
            hass,
            ordinary,
            {"id": 1, "action": "family_member", "payload": {"name": "Kid", "child": True}},
        )
        self.assertEqual(ordinary.result()[2], "unauthorized")
        self.assertEqual(hass.store.saves, [])


if __name__ == "__main__":
    unittest.main()

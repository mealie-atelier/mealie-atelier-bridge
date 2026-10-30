"""Recapture the whole German screenshot set of the Mealie guides (fictional data only).

Usage: uv run --with playwright --with pillow python tests/live/capture_guides.py <out-dir>

Writes one webp per guide image (same names and pixel sizes as the site's
public/images/guides/mealie-atelier/) and the OG crops as 1:1 jpg files into <out-dir>/og/ (same names
as public/images/og/mealie/). Everything is German: the Home Assistant user profile, the card
language and the browser locale. The clock is pinned to 09:00 local on the day of the run.

The script sets up everything it shows over the Home Assistant websocket, like a household would:
the Lovelace resource, the dashboards, a throwaway Local To-do list as sync target and the German
demo data in Mealie (the catalog of the cards repository). It then starts the standalone web app
with Node against the rig Mealie (throwaway data folder and password) and stops it again. The
cards bundle is served through route interception, so the mounted rig files stay untouched.

Environment:
  LIVE_STATE   folder with ha-token and mealie-token (default ./state, needed in a worktree)
  CARDS_BUNDLE built mealie-atelier-cards.js (default: ../mealie-atelier-cards/dist next to this repo)
  GUIDE_ONLY   comma separated image names to capture, for example "dashboard-hell,web-app"

Not captured on purpose: bring-integration and bring-todo-liste (they come from a real Bring! account)
and mealie-api-token unless it shows a wrong UI. Run `uv run ... capture_guides.py` again to redo all.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from capture_bridge import mealie
from capture_screens import BASE, sign_in
from ha_ws import HaSocket, read_token, state_dir
from PIL import Image
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

HERE = Path(__file__).parent
STATE = Path(state_dir(str(HERE)))
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "state" / "guides"
ONLY = {name for name in os.environ.get("GUIDE_ONLY", "").split(",") if name}
WORKSPACE = STATE.resolve().parents[3]
BUNDLE = Path(
    os.environ.get("CARDS_BUNDLE")
    or WORKSPACE / "mealie-atelier-cards" / "dist" / "mealie-atelier-cards.js"
)
CARDS_REPO = BUNDLE.parent.parent
CATALOG = CARDS_REPO / "demo" / "data" / "recipes.json"
RESOURCE = "/local/community/mealie-atelier-cards/mealie-atelier-cards.js"
MEALIE = "http://localhost:19925"
WEB_PASSWORD = secrets.token_urlsafe(18)

# Same time of day in every shot: 09:00 local on the day of the run.
CLOCK = datetime.combine(date.today(), datetime.min.time()).replace(hour=9)

# File name (without .webp) to pixel size. The viewport follows from the size: 1440 css pixels wide
# on a desktop shot, the shot is taken at device scale 2 and resized.
SIZES: dict[str, tuple[int, int]] = {
    "bridge-integration": (1600, 1067),
    "bridge-optionen": (1600, 1333),
    "bridge-verbinden": (1600, 1067),
    "dashboard-dunkel": (1600, 1130),
    "dashboard-hell": (1600, 1130),
    "einkaufs-sync-einrichten": (1920, 1280),
    "einkaufs-sync-leer": (1920, 1280),
    "einkaufs-sync-status": (1920, 1280),
    "einkaufs-sync-vorschau": (1920, 1280),
    "einkaufsliste-handy": (780, 1688),
    "integration-suchen": (1600, 1067),
    "karte-auswaehlen": (1600, 1067),
    "karte-einkauf": (1600, 1200),
    "karte-gemeinsam-kochen": (1600, 1500),
    "karte-heute": (1600, 1067),
    "karte-kochbuch": (1600, 1300),
    "karte-kochinspo": (1600, 1067),
    "karte-kueche": (1600, 1120),
    "karte-rezepte": (1600, 1105),
    "karte-verwalten": (1600, 1455),
    "karte-vorraete": (1600, 978),
    "karte-wochenplan": (1600, 1067),
    "karten-editor": (1600, 1400),
    "mealie-api-token": (1600, 1067),
    "kochmodus-dunkel": (1600, 1067),
    "kochmodus-handy": (780, 1688),
    "rezept-hell": (1600, 1101),
    "web-app-design-studio": (1920, 1200),
    "web-app-einkaufs-sync": (1920, 1200),
    "web-app-einkaufsliste": (1920, 1350),
    "web-app-handy": (780, 1688),
    "web-app-installieren": (780, 1688),
    "web-app-kochmodus": (1920, 1200),
    "web-app-rezept": (1920, 1200),
    "web-app-statusleiste": (1920, 1200),
    "web-app-wochenplan": (1920, 1200),
    "web-app": (1920, 1200),
    "wochenplan-hell": (1600, 1067),
}
# OG crops: name -> (source image, crop box in css pixels of the 1440 wide capture, output size).
# Crop boxes are given as fractions (left, top, right, bottom) of the source image.
OG_CROPS: dict[str, tuple[str, tuple[float, float, float, float], tuple[int, int]]] = {
    "dashboard": ("dashboard-dunkel", (0.0, 0.0, 0.78, 0.62), (1000, 600)),
    "web-app": ("web-app", (0.0, 0.0, 0.9, 0.9), (1000, 625)),
    "web-app-phone": ("web-app-handy", (0.0, 0.0, 1.0, 1.0), (360, 779)),
    "mealie-token": ("mealie-api-token", (0.0, 0.0, 0.7, 0.35), (900, 394)),
    "recipe-hero": ("dashboard-dunkel", (0.344, 0.482, 0.976, 0.816), (560, 222)),
    "sync-status": ("einkaufs-sync-status", (0.24, 0.14, 0.76, 0.86), (760, 702)),
    "bridge-options": ("bridge-optionen", (0.29, 0.01, 0.71, 0.80), (520, 839)),
}

RAW: dict[str, Image.Image] = {}
FAILED: list[str] = []

# German Mealie data. The week of the run: dinners from Thursday to Sunday.
WEEK_PLAN = [  # (days after Monday, German recipe title)
    (3, "Hähnchensalat mit Parmesan und Croutons"),
    (4, "Pfannen-Eier mit Tomaten und Basilikum"),
    (5, "Gnocchi alla Sorrentina"),
    (6, "Kürbissuppe mit Feta und Kürbiskernen"),
]
SOURCE_LIST = "Wochenmarkt"
SYNC_ITEMS = ["Tomaten", "Basilikum", "Mozzarella"]
RULE_NAME = "Wochenmarkt nach Bring!"
TODO_NAME = "Mealie Atelier"
TODO_ENTITY = "todo.mealie_atelier"


# Decode a photo, but never wait longer than three seconds for one that will not load.
DECODE = (
    "img => Promise.race([img.decode().catch(() => null), new Promise(r => setTimeout(r, 3000))])"
)


def arun(coro):
    """Run a coroutine to the end; a thread of its own, because Playwright's sync API owns the loop."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def wanted(name: str) -> bool:
    return not ONLY or name in ONLY


# ---------------------------------------------------------------------------------------------
# Image output


def finish(name: str, png: bytes, save: bool = True) -> None:
    """Resize a device-scale-2 capture to the size of the guide image and save it as webp."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    RAW[name] = image
    size = SIZES[name]
    if not save:
        return
    out = image.resize(size, Image.Resampling.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.webp", "WEBP", quality=84, method=6)
    print(f"ok   {name} {size[0]}x{size[1]}")


def viewport(name: str) -> dict[str, int]:
    width, height = SIZES[name]
    css_width = 390 if width == 780 else 1440
    return {"width": css_width, "height": round(height * css_width / width)}


def write_og() -> None:
    og = OUT / "og"
    og.mkdir(parents=True, exist_ok=True)
    for name, (source, box, size) in OG_CROPS.items():
        image = RAW.get(source)
        if image is None:
            path = OUT / f"{source}.webp"
            if not path.exists():
                continue
            image = Image.open(path).convert("RGB")
        w, h = image.size
        crop = image.crop(
            (round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h))
        )
        crop.resize(size, Image.Resampling.LANCZOS).save(og / f"{name}.jpg", "JPEG", quality=86)
        print(f"ok   og/{name} {size[0]}x{size[1]}")


# ---------------------------------------------------------------------------------------------
# Rig preparation (Home Assistant over the websocket, Mealie over its API)


def mealie_raw(method: str, path: str, body: object = None) -> object:
    return mealie(method, path, body)


def prepare_mealie() -> None:
    """German demo data only: replace the English seed, plan one dinner a day Thursday to Sunday."""
    import json

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    english = {r["title"]["en"] for r in catalog["recipes"]}
    german = {r["title"]["de"] for r in catalog["recipes"]}
    for entry in mealie("GET", "/api/households/mealplans?perPage=500")["items"]:
        mealie("DELETE", f"/api/households/mealplans/{entry['id']}")
    for recipe in mealie("GET", "/api/recipes?perPage=500")["items"]:
        if recipe["name"] in english - german:
            mealie("DELETE", f"/api/recipes/{recipe['slug']}")
    for shopping in mealie("GET", "/api/households/shopping/lists?perPage=100")["items"]:
        if shopping["name"] in ("Weekly shop", SOURCE_LIST):
            mealie("DELETE", f"/api/households/shopping/lists/{shopping['id']}")
    subprocess.run(
        [
            sys.executable,
            "-I",
            str(HERE / "seed_mealie.py"),
            MEALIE,
            str(STATE / "mealie-token"),
            str(CATALOG),
            "de",
        ],
        check=True,
        capture_output=True,
    )
    recipes = {r["name"]: r for r in mealie("GET", "/api/recipes?perPage=500")["items"]}
    for entry in mealie("GET", "/api/households/mealplans?perPage=500")["items"]:
        mealie("DELETE", f"/api/households/mealplans/{entry['id']}")
    monday = date.today() - timedelta(days=date.today().weekday())
    for offset, title in WEEK_PLAN:
        mealie(
            "POST",
            "/api/households/mealplans",
            {
                "date": (monday + timedelta(days=offset)).isoformat(),
                "entryType": "dinner",
                "recipeId": recipes[title]["id"],
            },
        )
    print("ok   Mealie has German demo data")


def mealie_source_list() -> str:
    """A fresh Mealie list with the three fictional items the sync rule sends."""
    list_id = mealie("POST", "/api/households/shopping/lists", {"name": SOURCE_LIST})["id"]
    for note in SYNC_ITEMS:
        mealie(
            "POST",
            "/api/households/shopping/items",
            {"shoppingListId": list_id, "note": note, "quantity": 0, "checked": False},
        )
    return list_id


def ha_rest(method: str, path: str, body: object = None) -> object:
    import json

    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {read_token(str(STATE / 'ha-token'))}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


async def ha_all(commands: list[dict]) -> list[object]:
    out = []
    async with HaSocket(BASE, read_token(str(STATE / "ha-token"))) as socket:
        for command in commands:
            ok, value = await socket.call(command)
            out.append(value if ok else {"error": value})
    return out


def card(kind: str, theme: str, **extra: object) -> dict:
    return {"type": f"custom:mealie-atelier-{kind}", "theme": theme, "language": "de", **extra}


CARD_KINDS = [
    "card",
    "today",
    "recipes",
    "mealplan",
    "shopping",
    "manage",
    "discover",
    "pantry",
    "family",
    "cookbook",
]
CARD_TABS = [
    "Küche",
    "Heute",
    "Rezepte",
    "Wochenplan",
    "Einkauf",
    "Verwalten",
    "Ideen",
    "Vorräte",
    "Familie",
    "Kochbuch",
]
SHOT_MODES = ["dashboard", "recipes", "mealplan", "shopping", "discover"]
SHOT_TABS = ["Küche", "Rezepte", "Wochenplan", "Einkauf", "Kochinspo"]


async def prepare_ha() -> None:
    """Resource, user language, country (no repair badge), to-do list and every dashboard."""
    first = await ha_all(
        [
            {"type": "lovelace/resources"},
            {"type": "lovelace/dashboards/list"},
            {"type": "config_entries/get"},
        ]
    )
    resources, dashboards, entries = first
    await ha_all([{"type": "config/core/update", "country": "DE", "language": "de"}])
    await ha_all(
        [{"type": "frontend/set_user_data", "key": "language", "value": {"language": "de"}}]
    )
    if not any(str(r["url"]).split("?")[0] == RESOURCE for r in resources):
        await ha_all([{"type": "lovelace/resources/create", "res_type": "module", "url": RESOURCE}])
    if not any(e["domain"] == "local_todo" and e["title"] == TODO_NAME for e in entries):
        flow = ha_rest("POST", "/api/config/config_entries/flow", {"handler": "local_todo"})
        result = ha_rest(
            "POST",
            f"/api/config/config_entries/flow/{flow['flow_id']}",
            {"todo_list_name": TODO_NAME},
        )
        if result.get("type") == "form":  # older forms ask for the name and an icon-less step
            result = ha_rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {})
        print("ok   To-do list", TODO_NAME, result.get("type"))
    existing = {d["url_path"] for d in dashboards}
    wanted_dashboards = {
        "atelier-shots-light": ("Atelier", False),
        "atelier-shots-dark": ("Atelier", False),
        "atelier-cards": ("Atelier", True),
        "atelier-edit": ("Küche", True),
    }
    for url_path, (title, sidebar) in wanted_dashboards.items():
        if url_path not in existing:
            await ha_all(
                [
                    {
                        "type": "lovelace/dashboards/create",
                        "url_path": url_path,
                        "title": title,
                        "mode": "storage",
                        "show_in_sidebar": sidebar,
                        "icon": "mdi:silverware-fork-knife",
                    }
                ]
            )
    configs = []
    for theme in ("light", "dark"):
        views = [
            {"title": tab, "path": mode, "panel": True, "cards": [card("card", theme, mode=mode)]}
            for mode, tab in zip(SHOT_MODES, SHOT_TABS, strict=True)
        ]
        configs.append(
            {
                "type": "lovelace/config/save",
                "url_path": f"atelier-shots-{theme}",
                "config": {"views": views},
            }
        )
    views = [
        {"title": tab, "path": kind, "panel": True, "cards": [card(kind, "light")]}
        for kind, tab in zip(CARD_KINDS, CARD_TABS, strict=True)
    ]
    configs.append(
        {"type": "lovelace/config/save", "url_path": "atelier-cards", "config": {"views": views}}
    )
    edit = {
        "views": [
            {
                "title": "Küche",
                "path": "kitchen",
                "type": "sections",
                "sections": [
                    {
                        "type": "grid",
                        "cards": [{"type": "heading", "heading": "Mealie"}, card("today", "light")],
                    }
                ],
            }
        ]
    }
    configs.append({"type": "lovelace/config/save", "url_path": "atelier-edit", "config": edit})
    for result in await ha_all(configs):
        if isinstance(result, dict) and "error" in result:
            raise SystemExit(f"dashboard config: {result['error']}")
    issues = (await ha_all([{"type": "repairs/list_issues"}]))[0]
    left = [i["issue_id"] for i in issues.get("issues", []) if not i.get("ignored")]
    if left:
        raise SystemExit(f"Home Assistant still shows repair issues: {left}")
    print("ok   Home Assistant prepared")


async def reset_sync_state() -> None:
    """No rule, no sent items and no cooking session left from an earlier run."""
    state = (await ha_all([{"type": "mealie_atelier/sync_state"}]))[0]
    for rule in (state or {}).get("rules", []):
        await ha_all([{"type": "mealie_atelier/sync_rule_delete", "rule_id": rule["id"]}])
    listed = (await ha_all([{"type": "todo/item/list", "entity_id": TODO_ENTITY}]))[0]
    uids = [i["uid"] for i in (listed or {}).get("items", [])]
    if uids:
        await ha_all(
            [
                {
                    "type": "call_service",
                    "domain": "todo",
                    "service": "remove_item",
                    "target": {"entity_id": TODO_ENTITY},
                    "service_data": {"item": uids},
                }
            ]
        )
    studio = (await ha_all([{"type": "mealie_atelier/studio_state"}]))[0]
    session = ((studio or {}).get("family") or {}).get("session")
    if session:
        await ha_all(
            [
                {
                    "type": "mealie_atelier/studio_update",
                    "action": "session_end",
                    "payload": {"session_id": session["id"]},
                }
            ]
        )


async def start_session() -> None:
    """A shared cooking session for the pancakes, as the Gemeinsam-kochen guide shows it."""
    recipes = mealie("GET", "/api/recipes?perPage=500")["items"]
    pancakes = next(r for r in recipes if r["name"].startswith("Buttermilch-Pancakes"))
    await ha_all(
        [
            {
                "type": "mealie_atelier/studio_update",
                "action": "session_start",
                "payload": {
                    "slug": pancakes["slug"],
                    "name": pancakes["name"],
                    "replace_existing": True,
                },
            }
        ]
    )


# ---------------------------------------------------------------------------------------------
# Browser helpers


def new_context(
    browser: Browser, name: str, scheme: str, *, mobile: bool = False
) -> BrowserContext:
    context = browser.new_context(
        viewport=viewport(name),
        color_scheme=scheme,
        locale="de-DE",
        device_scale_factor=2,
        is_mobile=mobile,
        has_touch=mobile,
        service_workers="block",
    )
    bundle = BUNDLE.read_bytes()
    context.route(
        "**/mealie-atelier-cards.js*",
        lambda route: route.fulfill(body=bundle, content_type="text/javascript; charset=utf-8"),
    )
    context.add_init_script(
        "localStorage.setItem('selectedLanguage', JSON.stringify('de'));"
        "localStorage.setItem('mealie-atelier:design', JSON.stringify({language: 'de'}));"
        "localStorage.setItem('dockedSidebar', JSON.stringify('docked'));"
    )
    return context


def ha_page(
    browser: Browser, name: str, scheme: str, *, mobile: bool = False, pin: bool = True
) -> tuple[BrowserContext, Page]:
    """Signed-in Home Assistant page with the clock pinned. Sign-in happens on the real clock.

    The sync dialogs run unpinned: they show relative run times ("jetzt") that follow the server."""
    context = new_context(browser, name, scheme, mobile=mobile)
    page = context.new_page()
    sign_in(page)
    if pin:
        context.clock.set_fixed_time(CLOCK)
    return context, page


def blur_all(page: Page) -> None:
    """Drop focus (through shadow roots) and park the mouse on an empty edge: no ring, no hover."""
    page.evaluate(
        """() => {
            let node = document.activeElement;
            while (node) { const next = node.shadowRoot && node.shadowRoot.activeElement; if (node.blur) node.blur(); node = next; }
        }"""
    )
    size = page.viewport_size
    page.mouse.move(size["width"] - 2, size["height"] // 2)
    page.wait_for_timeout(250)


def settle_ha(page: Page, selector: str = "mealie-atelier-card, [class*='atelier']") -> None:
    """The dashboard is loaded when the launch screen is gone and every visible photo has decoded."""
    page.wait_for_function("!document.getElementById('ha-launch-screen')", timeout=60_000)
    page.wait_for_load_state("networkidle")
    page.locator(selector).first.wait_for(timeout=60_000)
    for img in page.locator("img >> visible=true").all():
        img.evaluate(DECODE)
    page.wait_for_timeout(1200)


def snap(page: Page, name: str) -> None:
    blur_all(page)
    finish(name, page.screenshot())


def open_dashboard(page: Page, path: str) -> None:
    page.goto(f"{BASE}/{path}")
    settle_ha(page)


def run(name: str | tuple[str, ...], step: Callable[[], None]) -> None:
    names = (name,) if isinstance(name, str) else name
    if not any(wanted(n) for n in names):
        return
    name = names[0]
    try:
        step()
    except Exception as exc:  # noqa: BLE001 - report and keep the other shots
        FAILED.append(f"{name}: {str(exc).splitlines()[0] if str(exc) else repr(exc)}")
        print(f"FAIL {name}: {FAILED[-1]}")


# ---------------------------------------------------------------------------------------------
# Home Assistant shots: cards on dashboards


def shot_dashboard(browser: Browser, name: str, theme: str) -> None:
    context, page = ha_page(browser, name, theme)
    open_dashboard(page, f"atelier-shots-{theme}/dashboard")
    snap(page, name)
    context.close()


def shot_card(browser: Browser, name: str, kind: str) -> None:
    context, page = ha_page(browser, name, "light")
    open_dashboard(page, f"atelier-cards/{kind}")
    snap(page, name)
    context.close()


def shot_wochenplan_hell(browser: Browser) -> None:
    context, page = ha_page(browser, "wochenplan-hell", "light")
    open_dashboard(page, "atelier-shots-light/mealplan")
    snap(page, "wochenplan-hell")
    context.close()


def shot_recipe(browser: Browser) -> None:
    context, page = ha_page(browser, "rezept-hell", "light")
    open_dashboard(page, "atelier-shots-light/recipes")
    page.locator(".recipe-tile >> visible=true").first.click()
    page.locator("[data-action='cook-start'] >> visible=true").wait_for()
    settle_ha(page)
    # End the dialog in the gap above the ingredient columns, after the recipe check, so no row is cut at its edge.
    page.locator(".modal[role='dialog'] >> visible=true").first.evaluate(
        """modal => {
            const cols = modal.querySelector('.detail-cols');
            const bottom = cols.getBoundingClientRect().top - modal.getBoundingClientRect().top - 6;
            const style = document.createElement('style');
            modal.getRootNode().appendChild(style);
            style.textContent = `.atelier .modal[role='dialog']{max-height:${Math.ceil(bottom)}px!important}`;
        }"""
    )
    page.wait_for_timeout(500)
    snap(page, "rezept-hell")
    context.close()


def shot_cook(browser: Browser) -> None:
    context, page = ha_page(browser, "kochmodus-dunkel", "dark")
    open_dashboard(page, "atelier-shots-dark/recipes")
    page.locator(".recipe-tile >> visible=true").first.click()
    page.locator("[data-action='cook-start'] >> visible=true").click()
    page.locator("[data-action='cook-next'] >> visible=true").wait_for()
    settle_ha(page)
    snap(page, "kochmodus-dunkel")
    context.close()


def shot_cook_phone(browser: Browser) -> None:
    context, page = ha_page(browser, "kochmodus-handy", "dark", mobile=True)
    open_dashboard(page, "atelier-shots-dark/recipes")
    page.locator(".recipe-tile >> visible=true").first.click()
    page.locator("[data-action='cook-start'] >> visible=true").click()
    page.locator("[data-action='cook-next'] >> visible=true").wait_for()
    settle_ha(page)
    snap(page, "kochmodus-handy")
    context.close()


def shot_shopping_phone(browser: Browser) -> None:
    context, page = ha_page(browser, "einkaufsliste-handy", "light", mobile=True)
    open_dashboard(page, "atelier-shots-light/shopping")
    # The checked item must show: scroll the list until the "Erledigt" row is whole.
    page.get_by_text("Haferflocken").first.scroll_into_view_if_needed()
    page.mouse.move(200, 400)
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(600)
    snap(page, "einkaufsliste-handy")
    context.close()


# ---------------------------------------------------------------------------------------------
# Home Assistant shots: its own pages


def shot_bridge_pages(browser: Browser) -> None:
    """Integration page, the connect form and the options dialog of the Bridge."""
    context, page = ha_page(browser, "bridge-integration", "light")
    page.goto(f"{BASE}/config/integrations/integration/mealie_atelier")
    page.wait_for_load_state("networkidle")
    page.get_by_text("Dienste", exact=True).first.wait_for(timeout=30_000)
    page.wait_for_timeout(800)
    if wanted("bridge-integration"):
        snap(page, "bridge-integration")
    if wanted("bridge-optionen"):
        page.set_viewport_size(viewport("bridge-optionen"))
        page.get_by_role("button", name=re.compile("^(Konfigurieren|Configure)")).first.click()
        page.locator("ha-form >> visible=true").first.wait_for(timeout=30_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        snap(page, "bridge-optionen")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    if wanted("bridge-verbinden"):
        page.set_viewport_size(viewport("bridge-verbinden"))
        page.goto(f"{BASE}/config/integrations/integration/mealie_atelier")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name=re.compile("Dienst hinzufügen|Add service")).click()
        first = page.locator("ha-form input >> visible=true").first
        first.wait_for(timeout=30_000)
        # An empty required field shows "Erforderlich" once it loses focus; a fictional address avoids it.
        first.fill("http://192.168.1.20:9000")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        snap(page, "bridge-verbinden")
        page.keyboard.press("Escape")
    context.close()


def abort_flows() -> None:
    progress = arun(ha_all([{"type": "config_entries/flow/progress"}]))[0]
    for flow in progress or []:
        if flow.get("handler") == "mealie_atelier":
            arun(ha_all([{"type": "config_entries/flow/abort", "flow_id": flow["flow_id"]}]))


def shot_integration_search(browser: Browser) -> None:
    context, page = ha_page(browser, "integration-suchen", "light")
    page.goto(f"{BASE}/config/integrations/dashboard")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name=re.compile("Integration hinzufügen|Add integration")).click()
    search = page.get_by_placeholder(re.compile("Markennamen|brand name")).first
    search.wait_for(timeout=30_000)
    search.fill("Mealie")
    page.wait_for_timeout(1500)
    snap(page, "integration-suchen")
    context.close()


def shot_card_picker_and_editor(browser: Browser) -> None:
    context, page = ha_page(browser, "karte-auswaehlen", "light")
    page.goto(f"{BASE}/atelier-edit/kitchen?edit=1")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    page.get_by_role("button", name=re.compile("Karte hinzufügen|Add card")).first.click()
    page.get_by_role("tab", name=re.compile("Nach Karte|By card")).click()
    search = page.locator("hui-card-picker input >> visible=true").first
    search.wait_for(timeout=30_000)
    search.fill("Mealie Atelier")
    page.wait_for_timeout(1500)
    if wanted("karte-auswaehlen"):
        snap(page, "karte-auswaehlen")
    if wanted("karten-editor"):
        page.set_viewport_size(viewport("karten-editor"))
        page.locator("hui-card-picker .card >> visible=true").first.click()
        page.locator("mealie-atelier-card-editor >> visible=true").first.wait_for(timeout=30_000)
        page.wait_for_timeout(1500)
        snap(page, "karten-editor")
    context.close()


# ---------------------------------------------------------------------------------------------
# Smart Shopping Sync dialogs (dark card in a dark Home Assistant)


def sync_modal(page: Page):
    return page.locator(".modal.sync-modal").first


def crop_modal(page: Page, first: int, last: int) -> None:
    """Show editor sections first..last (0 based) whole: the dialog height ends in the gap below."""
    sync_modal(page).evaluate(
        """(modal, [first, last]) => {
            const cards = [...modal.querySelectorAll('.editor-card')];
            const style = document.createElement('style');
            modal.getRootNode().appendChild(style);
            const gap = 24;
            const box = modal.getBoundingClientRect();
            const top = cards[first].getBoundingClientRect().top - box.top + modal.scrollTop;
            const bottom = cards[last].getBoundingClientRect().bottom - box.top + modal.scrollTop;
            const sticky = [...modal.children].find((el) => getComputedStyle(el).position === 'sticky');
            const head = first === 0 || !sticky ? 0 : sticky.getBoundingClientRect().height;
            const lead = first === 0 ? 0 : top - gap - head;
            style.textContent = `.atelier .modal.sync-modal{max-height:${Math.ceil(bottom - lead + gap)}px!important}`;
            modal.scrollTop = lead;
        }""",
        [first, last],
    )
    page.wait_for_timeout(500)


def fit_ratio(page: Page, name: str) -> None:
    """Viewport with the aspect ratio of the guide image around the whole dialog, nothing cut."""
    width, height = SIZES[name]
    box_height = sync_modal(page).evaluate("el => el.getBoundingClientRect().height")
    need = int(box_height) + 220
    target_h = max(viewport(name)["height"], need)
    page.set_viewport_size({"width": round(target_h * width / height), "height": target_h})
    page.wait_for_timeout(600)


def shots_sync(browser: Browser) -> None:
    list_id = mealie_source_list()
    context, page = ha_page(browser, "einkaufs-sync-leer", "dark", pin=False)
    try:
        arun(reset_sync_state())
        open_dashboard(page, "atelier-shots-dark/shopping")
        page.locator("[data-action='sync-open'] >> visible=true").click()
        page.locator("[data-action='sync-setup-open'] >> visible=true").first.wait_for()
        page.wait_for_timeout(900)
        if wanted("einkaufs-sync-leer"):
            fit_ratio(page, "einkaufs-sync-leer")
            snap(page, "einkaufs-sync-leer")
        page.locator("[data-action='sync-setup-open'] >> visible=true").first.click()
        source = page.locator("[data-input='sync-source']")
        source.wait_for(timeout=30_000)
        source.select_option(label=SOURCE_LIST)
        page.locator("[data-input='sync-name']").fill(RULE_NAME)
        destination = page.locator("[data-input='sync-destination']")
        destination.locator(f"option[value='{TODO_ENTITY}']").wait_for(
            state="attached", timeout=30_000
        )
        destination.select_option(value=TODO_ENTITY)
        page.wait_for_timeout(1200)
        if wanted("einkaufs-sync-einrichten"):
            page.set_viewport_size({"width": 1440, "height": 1080})
            crop_modal(page, 0, 2)
            fit_ratio(page, "einkaufs-sync-einrichten")
            snap(page, "einkaufs-sync-einrichten")
        if wanted("einkaufs-sync-vorschau") or wanted("einkaufs-sync-status"):
            page.locator("[data-action='sync-preview']").click()
            page.get_by_text(re.compile("Beispielzeile")).first.wait_for(timeout=30_000)
            page.wait_for_timeout(800)
            if wanted("einkaufs-sync-vorschau"):
                page.set_viewport_size({"width": 1440, "height": 1080})
                crop_modal(page, 4, 6)
                fit_ratio(page, "einkaufs-sync-vorschau")
                snap(page, "einkaufs-sync-vorschau")
            page.set_viewport_size({"width": 1440, "height": 960})
            page.locator("[data-action='sync-save'][data-value='send']").click()
            page.get_by_text(re.compile("Letzter Lauf")).first.wait_for(timeout=60_000)
            page.wait_for_timeout(1200)
            if wanted("einkaufs-sync-status"):
                fit_ratio(page, "einkaufs-sync-status")
                snap(page, "einkaufs-sync-status")
    finally:
        context.close()
        arun(reset_sync_state())
        mealie("DELETE", f"/api/households/shopping/lists/{list_id}")


# ---------------------------------------------------------------------------------------------
# Mealie's own token page (kept as is unless it shows a wrong UI)


def shot_mealie_token(browser: Browser) -> None:
    context = new_context(browser, "mealie-api-token", "light")
    page = context.new_page()
    page.goto(f"{MEALIE}/login")
    page.locator("input[type='text'], input[type='email']").first.fill("changeme@example.com")
    page.locator("input[type='password']").first.fill("MyPassword")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
    page.goto(f"{MEALIE}/user/profile/api-tokens")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    # The shipped image stays unless it is asked for by name; the capture still feeds the OG crop.
    finish("mealie-api-token", page.screenshot(), save="mealie-api-token" in ONLY)
    context.close()


# ---------------------------------------------------------------------------------------------
# Standalone web app (Node against the rig Mealie, throwaway data folder and password)


class WebApp:
    """The cards repository's web app on a free port; stopped again when the block ends."""

    def __enter__(self) -> WebApp:
        import socket
        import threading

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.data_dir = tempfile.mkdtemp(prefix="atelier-guides-")
        env = {
            "PATH": os.environ["PATH"],
            "MEALIE_URL": MEALIE,
            "MEALIE_API_TOKEN": read_token(str(STATE / "mealie-token")),
            "ATELIER_PASSWORD": WEB_PASSWORD,
            "HOST": "127.0.0.1",
            "PORT": str(self.port),
            "ATELIER_DATA_DIR": self.data_dir,
        }
        self.child = subprocess.Popen(
            ["node", "server.mjs"],
            cwd=CARDS_REPO / "web",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.log: list[str] = []
        threading.Thread(
            target=lambda: [self.log.append(line) for line in self.child.stdout], daemon=True
        ).start()
        deadline = time.time() + 20
        while time.time() < deadline and not any("available on http" in line for line in self.log):
            time.sleep(0.2)
        if not any("available on http" in line for line in self.log):
            raise SystemExit("web app did not start:\n" + "".join(self.log))
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    def __exit__(self, *exc: object) -> None:
        self.child.terminate()
        self.child.wait(timeout=10)
        subprocess.run(["trash", self.data_dir], check=False)


ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Mobile Safari/537.36"
)


def web_page(
    browser: Browser, app: WebApp, name: str, *, mobile: bool = False, ua: str | None = None
):
    context = browser.new_context(
        viewport=viewport(name),
        color_scheme="dark",
        locale="de-DE",
        device_scale_factor=2,
        is_mobile=mobile,
        has_touch=mobile,
        user_agent=ua,
        http_credentials={"username": "atelier", "password": WEB_PASSWORD},
        service_workers="block",
    )
    bundle = BUNDLE.read_bytes()
    context.route(
        "**/mealie-atelier-cards.js*",
        lambda route: route.fulfill(body=bundle, content_type="text/javascript; charset=utf-8"),
    )
    context.add_init_script(
        "localStorage.setItem('mealie-atelier:design', JSON.stringify({language: 'de', theme: 'dark'}));"
    )
    context.set_default_timeout(45_000)
    context.clock.set_fixed_time(CLOCK)
    page = context.new_page()
    page.goto(app.url + "/")
    page.locator("[data-action='nav'] >> visible=true").first.wait_for(timeout=60_000)
    web_settle(page)
    return context, page


def web_settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    for img in page.locator("img >> visible=true").all():
        img.evaluate(DECODE)
    page.wait_for_timeout(1200)


def web_nav(page: Page, label: str) -> None:
    page.locator("[data-action='nav'] >> visible=true", has_text=label).first.click()
    web_settle(page)


def cap_modal(page: Page, find_bottom: str) -> None:
    """Cap the dialog height just below an element (a JS expression over `modal`), cut nothing half."""
    page.locator(".modal[role='dialog'] >> visible=true").first.evaluate(
        """(modal, body) => {
            const edge = new Function('modal', body)(modal);
            const bottom = edge - modal.getBoundingClientRect().top;
            const style = document.createElement('style');
            modal.getRootNode().appendChild(style);
            style.textContent = `.atelier .modal[role='dialog']{max-height:${Math.ceil(bottom)}px!important}`;
        }""",
        find_bottom,
    )
    page.wait_for_timeout(500)


ABOVE_COLUMNS = "return modal.querySelector('.detail-cols').getBoundingClientRect().top - 6;"


def show_checked_item(page: Page) -> None:
    """Shopping list with the checked item and the add row whole (taller image, no scrolling)."""
    web_nav(page, "Einkaufsliste")


def shots_web(browser: Browser) -> None:
    with WebApp() as app:
        names = {
            "web-app": lambda page: None,
            "web-app-statusleiste": lambda page: page.locator(
                "details.atelier-connectivity summary"
            ).click(),
            "web-app-wochenplan": lambda page: web_nav(page, "Wochenplan"),
            "web-app-einkaufsliste": show_checked_item,
        }
        for name, prepare in names.items():
            if not wanted(name):
                continue
            context, page = web_page(browser, app, name)
            try:
                prepare(page)
                page.wait_for_timeout(700)
                snap(page, name)
            except Exception as exc:  # noqa: BLE001
                FAILED.append(f"{name}: {str(exc).splitlines()[0]}")
                print(f"FAIL {FAILED[-1]}")
            finally:
                context.close()

        def open_recipe(page: Page) -> None:
            web_nav(page, "Rezepte")
            page.locator(".recipe-tile >> visible=true", has_text="Gnocchi").first.click()
            page.locator("[data-action='cook-start'] >> visible=true").wait_for()
            web_settle(page)

        def recipe(page: Page) -> None:
            open_recipe(page)
            cap_modal(page, ABOVE_COLUMNS)

        def cook(page: Page) -> None:
            open_recipe(page)
            page.locator("[data-action='cook-start'] >> visible=true").click()
            page.locator("[data-action='cook-next'] >> visible=true").wait_for()
            web_settle(page)

        def sync(page: Page) -> None:
            web_nav(page, "Einkaufsliste")
            page.locator("[data-action='sync-open'] >> visible=true").click()
            page.locator(".sync-explain").first.wait_for(timeout=30_000)

        def studio(page: Page) -> None:
            page.locator("[data-action='settings'] >> visible=true").first.click()
            page.get_by_text("Design Studio").first.wait_for(timeout=30_000)
            page.wait_for_timeout(800)
            cap_modal(
                page,
                """const group = modal.querySelector("[data-input='language']").closest('.settings-group');
                   return group.getBoundingClientRect().bottom + 12;""",
            )

        for name, prepare in {
            "web-app-rezept": recipe,
            "web-app-kochmodus": cook,
            "web-app-einkaufs-sync": sync,
            "web-app-design-studio": studio,
        }.items():
            if not wanted(name):
                continue
            context, page = web_page(browser, app, name)
            try:
                prepare(page)
                page.wait_for_timeout(700)
                snap(page, name)
            except Exception as exc:  # noqa: BLE001
                FAILED.append(f"{name}: {str(exc).splitlines()[0]}")
                print(f"FAIL {FAILED[-1]}")
            finally:
                context.close()
        if wanted("web-app-handy"):
            context, page = web_page(browser, app, "web-app-handy", mobile=True)
            snap(page, "web-app-handy")
            context.close()
        if wanted("web-app-installieren"):
            context, page = web_page(browser, app, "web-app-installieren", mobile=True, ua=ANDROID)
            page.evaluate("document.querySelector('#pwa-install-trigger').click()")
            page.locator("#pwa-install-dialog[open]").wait_for(timeout=10_000)
            page.wait_for_timeout(700)
            snap(page, "web-app-installieren")
            context.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    arun(prepare_ha())
    prepare_mealie()
    arun(reset_sync_state())
    arun(start_session())
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--lang=de-DE"])
        try:
            run("dashboard-hell", lambda: shot_dashboard(browser, "dashboard-hell", "light"))
            run("dashboard-dunkel", lambda: shot_dashboard(browser, "dashboard-dunkel", "dark"))
            for name, kind in {
                "karte-kueche": "card",
                "karte-heute": "today",
                "karte-rezepte": "recipes",
                "karte-wochenplan": "mealplan",
                "karte-einkauf": "shopping",
                "karte-verwalten": "manage",
                "karte-kochinspo": "discover",
                "karte-vorraete": "pantry",
                "karte-gemeinsam-kochen": "family",
                "karte-kochbuch": "cookbook",
            }.items():
                run(name, lambda name=name, kind=kind: shot_card(browser, name, kind))
            run("wochenplan-hell", lambda: shot_wochenplan_hell(browser))
            run("rezept-hell", lambda: shot_recipe(browser))
            run("kochmodus-dunkel", lambda: shot_cook(browser))
            run("kochmodus-handy", lambda: shot_cook_phone(browser))
            run("einkaufsliste-handy", lambda: shot_shopping_phone(browser))
            run(
                ("bridge-integration", "bridge-optionen", "bridge-verbinden"),
                lambda: shot_bridge_pages(browser),
            )
            run("integration-suchen", lambda: shot_integration_search(browser))
            run(("karte-auswaehlen", "karten-editor"), lambda: shot_card_picker_and_editor(browser))
            run(
                (
                    "einkaufs-sync-leer",
                    "einkaufs-sync-einrichten",
                    "einkaufs-sync-vorschau",
                    "einkaufs-sync-status",
                ),
                lambda: shots_sync(browser),
            )
            run(("mealie-api-token", "mealie-token"), lambda: shot_mealie_token(browser))
            run(
                (
                    "web-app",
                    "web-app-statusleiste",
                    "web-app-wochenplan",
                    "web-app-einkaufsliste",
                    "web-app-rezept",
                    "web-app-kochmodus",
                    "web-app-einkaufs-sync",
                    "web-app-design-studio",
                    "web-app-handy",
                    "web-app-installieren",
                ),
                lambda: shots_web(browser),
            )
        finally:
            browser.close()
            abort_flows()
            arun(reset_sync_state())
    write_og()
    for line in FAILED:
        print(f"FAIL {line}")
    print(f"guide images in {OUT}")
    if FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

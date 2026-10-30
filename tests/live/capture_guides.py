"""Recapture the whole German screenshot set of the Mealie guides (fictional data only).

Usage: uv run --with playwright --with pillow python tests/live/capture_guides.py <out-dir>

Writes one webp per guide image (same names as the site's public/images/guides/mealie-atelier/; the pixel
width is kept and the height follows the window where a shot needs more room to show everything whole)
and the OG crops as 1200x630 jpg files into <out-dir>/og/ (same names as public/images/og/mealie/).
Every OG crop keeps the aspect ratio of its source box, so nothing is stretched. Everything is German: the Home Assistant user profile, the card
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
  GUIDE_KEPT   folder with the shipped guide images that are not captured (mealie-api-token feeds the OG crop)

No shot shows the credit line ("Mit ♥ von ..."): its text is made invisible before every screenshot, the
layout stays as shipped. The sync, cook mode and Design Studio dialogs are not cut by injected CSS: where one scrolls (it never gets taller than
1040 px), the window height is chosen so the dialog ends in a gap between sections.

Not captured on purpose: bring-integration and bring-todo-liste (they come from a real Bring! account)
and mealie-api-token (the shipped image is right; the OG crop is cut from it, not from a new capture).
The browser is Chromium in its new headless mode, which takes its UI language from --lang (the date field
then reads "tt.mm.jjjj" instead of "dd.mm.yyyy").
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
    "einkaufsliste-handy": (780, 1712),
    "integration-suchen": (1600, 1067),
    "karte-auswaehlen": (1600, 1200),
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
    "kochmodus-dunkel": (1600, 1067),
    "kochmodus-handy": (780, 1688),
    "rezept-hell": (1600, 1101),
    "web-app-design-studio": (1920, 1200),
    "web-app-einkaufs-sync": (1920, 1200),
    "web-app-einkaufsliste": (1920, 1680),
    "web-app-handy": (680, 1660),
    "web-app-installieren": (780, 1688),
    "web-app-kochmodus": (1920, 1200),
    "web-app-rezept": (1920, 1200),
    "web-app-statusleiste": (1920, 1000),
    "web-app-wochenplan": (1920, 1200),
    "web-app": (1920, 1340),
    "wochenplan-hell": (1600, 1067),
}
# OG crops, all 1200x630. name -> (source image, left, top, right as fractions of the source width,
# top as fraction of the source height). The box height follows from the 1200:630 ratio, so the crop is
# never stretched. A source can also be "phone": the portrait screenshot centred on a canvas of its own edge colour.
OG_SIZE = (1200, 630)
OG_CROPS: dict[str, tuple[str, float, float, float]] = {
    "dashboard": ("dashboard-dunkel", 0.325, 0.058, 1.0),
    "web-app": ("web-app-wochenplan", 0.0208, 0.0933, 0.9896),
    "web-app-phone": ("web-app-handy", 0.0, 0.0, 1.0),
    "mealie-token": ("mealie-api-token", 0.3125, 0.4124, 0.8625),
    "recipe-hero": ("dashboard-dunkel", 0.325, 0.4956, 0.995),
    "sync-status": ("einkaufs-sync-status", 0.2104, 0.3969, 0.7896),
    "bridge-options": ("bridge-optionen", 0.29375, 0.739, 0.70625),
}

# Folder with the shipped guide images that this script does not capture (mealie-api-token comes from there).
KEPT = Path(os.environ["GUIDE_KEPT"]) if os.environ.get("GUIDE_KEPT") else None

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
RULE_NAME = "Wochenmarkt zur To-do-Liste"
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
    css_width = width // 2 if width < 1000 else 1440
    return {"width": css_width, "height": round(height * css_width / width)}


def set_height(page: Page, name: str, css_height: int, css_width: int | None = None) -> None:
    """Resize the viewport and let the guide image follow: same pixel width, height from the new ratio."""
    css_width = css_width or page.viewport_size["width"]
    width = SIZES[name][0]
    SIZES[name] = (width, round(css_height * width / css_width))
    page.set_viewport_size({"width": css_width, "height": css_height})
    page.wait_for_timeout(500)


# Finds the first element for a selector, also inside shadow roots (the cards live in one).
DEEP = """
const deep = (selector, root = document) => {
  const hit = root.querySelector(selector);
  if (hit) return hit;
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) { const inner = deep(selector, el.shadowRoot); if (inner) return inner; }
  }
  return null;
};
"""


def grow_to_fit(page: Page, name: str, selector: str, limit: int = 4000) -> None:
    """Make the viewport tall enough that the scrolling element needs no scrolling: nothing is cut."""
    for _ in range(8):
        extra = page.evaluate(
            "([deepSrc, sel]) => { const deep = new Function(deepSrc + 'return deep;')(); "
            "const el = deep(sel); return el ? el.scrollHeight - el.clientHeight : 0; }",
            [DEEP, selector],
        )
        height = page.viewport_size["height"]
        if extra <= 1 or height >= limit:
            return
        set_height(page, name, min(limit, height + extra + 2))


def fit_document(page: Page, name: str) -> None:
    """Window as tall as the page, so the sidebar and the content end whole instead of at the window edge."""
    for _ in range(4):
        total = page.evaluate("document.documentElement.scrollHeight")
        if total <= page.viewport_size["height"]:
            return
        set_height(page, name, total)


def fit_modal_height(
    page: Page, name: str, target: float, selector: str = ".modal[role='dialog']"
) -> None:
    """Move the viewport height until the dialog box is `target` css pixels high (the dialog follows the window)."""
    modal = page.locator(f"{selector} >> visible=true").first
    for _ in range(6):
        have = modal.evaluate("el => el.getBoundingClientRect().height")
        if abs(have - target) <= 1:
            return
        set_height(page, name, page.viewport_size["height"] + round(target - have))
    raise RuntimeError(f"{name}: the dialog height did not settle on {target}")


def og_image(name: str, image: Image.Image, left: float, top: float, right: float) -> Image.Image:
    """A 1200x630 crop: the box keeps the 1200:630 ratio of the output, so there is no stretching."""
    w, h = image.size
    x0, x1 = round(left * w), round(right * w)
    y0 = round(top * h)
    y1 = y0 + round((x1 - x0) * OG_SIZE[1] / OG_SIZE[0])
    if y1 > h:
        raise SystemExit(f"og/{name}: the crop box ends {y1 - h}px below the source image")
    return image.crop((x0, y0, x1, y1)).resize(OG_SIZE, Image.Resampling.LANCZOS)


def og_phone(handy: Image.Image, install: Image.Image) -> Image.Image:
    """Two phones side by side on a canvas in the colour of the first one's edge, each a whole-row crop.

    Left the start page down to the gap above the next block, right the install dialog (its blurred top
    trimmed). Each phone is about 30 percent of the width, so the screen text stays readable in a small card.
    """
    height = OG_SIZE[1]
    left = handy.crop((0, 0, handy.width, round(handy.width * 1170 / 680)))
    right = install.crop((0, round(install.height * 480 / 1688), install.width, install.height))
    shots = [
        shot.resize((round(shot.width * height / shot.height), height), Image.Resampling.LANCZOS)
        for shot in (left, right)
    ]
    gap = 40
    total = sum(shot.width for shot in shots) + gap
    canvas = Image.new("RGB", OG_SIZE, handy.getpixel((2, handy.height // 2)))
    x = (OG_SIZE[0] - total) // 2
    for shot in shots:
        canvas.paste(shot, (x, 0))
        x += shot.width + gap
    return canvas


def load_source(name: str) -> Image.Image | None:
    """A source image of this run, else the saved one next to it, else the shipped one that is kept."""
    if name in RAW:
        return RAW[name]
    for folder in (OUT, KEPT):
        if folder and (folder / f"{name}.webp").exists():
            return Image.open(folder / f"{name}.webp").convert("RGB")
    return None


def write_og() -> None:
    og = OUT / "og"
    og.mkdir(parents=True, exist_ok=True)
    for name, (source, left, top, right) in OG_CROPS.items():
        image = load_source(source)
        if image is None:
            print(f"skip og/{name}: no {source} image (set GUIDE_KEPT for the shipped ones)")
            continue
        if name == "web-app-phone":
            install = load_source("web-app-installieren")
            if install is None:
                continue
            out = og_phone(image, install)
        else:
            out = og_image(name, image, left, top, right)
        out.save(og / f"{name}.jpg", "JPEG", quality=88)
        print(f"ok   og/{name} {out.width}x{out.height}")


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
    "Einkaufsliste",
    "Verwalten",
    "Kochinspo",
    "Vorräte",
    "Gemeinsam kochen",
    "Kochbuch",
]
SHOT_MODES = ["dashboard", "recipes", "mealplan", "shopping", "discover"]
SHOT_TABS = ["Küche", "Rezepte", "Wochenplan", "Einkaufsliste", "Kochinspo"]


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
        "atelier-edit": ("Küche", False),
        "atelier-phone": ("Atelier", False),
    }
    for url_path, (title, sidebar) in wanted_dashboards.items():
        if url_path in existing:
            current = next(d for d in dashboards if d["url_path"] == url_path)
            if current["show_in_sidebar"] != sidebar or current["title"] != title:
                await ha_all(
                    [
                        {
                            "type": "lovelace/dashboards/update",
                            "dashboard_id": current["id"],
                            "title": title,
                            "show_in_sidebar": sidebar,
                        }
                    ]
                )
        else:
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
    # The English onboarding left the built-in map dashboard titled "Map": a German sidebar shows "Karte".
    for entry in dashboards:
        if entry["id"] == "map" and entry["title"] != "Karte":
            await ha_all(
                [{"type": "lovelace/dashboards/update", "dashboard_id": "map", "title": "Karte"}]
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
    # One view only: Home Assistant then shows a title instead of a scrolling tab bar on a phone.
    phone = {
        "views": [
            {
                "title": "Einkaufsliste",
                "path": "shopping",
                "panel": True,
                "cards": [card("card", "light", mode="shopping")],
            }
        ]
    }
    configs.append({"type": "lovelace/config/save", "url_path": "atelier-phone", "config": phone})
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
    """Signed-in Home Assistant page with the clock pinned. Sign-in happens on the real clock."""
    context = new_context(browser, name, scheme, mobile=mobile)
    page = context.new_page()
    sign_in(page)
    if pin:
        context.clock.set_fixed_time(CLOCK)
    return context, page


def hide_credit(page: Page) -> None:
    """No credit line in any shot (it names the author). The text goes invisible, the layout stays as shipped."""
    page.evaluate(
        """() => {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync('.atelier-credit{visibility:hidden!important}');
            const walk = (root) => {
                if (!root.adoptedStyleSheets.includes(sheet)) root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
                for (const el of root.querySelectorAll('*')) if (el.shadowRoot) walk(el.shadowRoot);
            };
            walk(document);
        }"""
    )


def blur_all(page: Page) -> None:
    """Drop focus (through shadow roots) and park the mouse on an empty edge: no ring, no hover."""
    hide_credit(page)
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
    # The dialog is as tall as the window allows: grow the window until the footer buttons are whole in view.
    grow_to_fit(page, "kochmodus-dunkel", ".modal[role='dialog']")
    snap(page, "kochmodus-dunkel")
    context.close()


def shot_cook_phone(browser: Browser) -> None:
    context, page = ha_page(browser, "kochmodus-handy", "dark", mobile=True)
    open_dashboard(page, "atelier-shots-dark/recipes")
    page.locator(".recipe-tile >> visible=true").first.click()
    page.locator("[data-action='cook-start'] >> visible=true").click()
    page.locator("[data-action='cook-next'] >> visible=true").wait_for()
    settle_ha(page)
    # Top of the dialog: photo, progress, the "keep awake" checkbox and the step. The dialog scrolls and its
    # footer (Zurück, Zum Rezept, Weiter) is sticky, so the window height is chosen that the footer starts
    # right where the tool tray begins: no half row hides under the footer.
    modal = page.locator(".modal[role='dialog'] >> visible=true").first
    need = modal.evaluate(
        """modal => {
            const box = modal.getBoundingClientRect();
            const tray = modal.querySelector('.cook-utility').getBoundingClientRect();
            const footer = [...modal.querySelectorAll('*')].find((el) => {
                const css = getComputedStyle(el);
                return css.position === 'sticky' && css.bottom !== 'auto';
            });
            return (tray.top - box.top + modal.scrollTop) - 18 + footer.getBoundingClientRect().height;
        }"""
    )
    fit_modal_height(page, "kochmodus-handy", need)
    modal.evaluate("modal => { modal.scrollTop = 0; }")
    page.wait_for_timeout(600)
    snap(page, "kochmodus-handy")
    context.close()


def shot_shopping_phone(browser: Browser) -> None:
    context, page = ha_page(browser, "einkaufsliste-handy", "light", mobile=True)
    open_dashboard(page, "atelier-phone/shopping")
    # Scroll the page until the whole list card sits just below the Home Assistant header.
    top = page.locator(".shopping-card >> visible=true").first.evaluate(
        "el => el.getBoundingClientRect().top + window.scrollY"
    )
    page.evaluate("y => window.scrollTo(0, y)", top - 72)
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
        # The form is long: a window this tall holds all of it, down to "Benachrichtigungsdienst für Timer" and OK.
        set_height(page, "bridge-optionen", 1400)
        page.get_by_role("button", name=re.compile("^(Konfigurieren|Configure)")).first.click()
        page.locator("ha-form >> visible=true").first.wait_for(timeout=30_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        last = page.get_by_text("Benachrichtigungsdienst für Timer").first.bounding_box()
        if not last or last["y"] + last["height"] > page.viewport_size["height"] - 60:
            raise RuntimeError("bridge-optionen: the last option is not whole in view")
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
    page.get_by_text("Manuell", exact=True).first.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
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


SYNC_BOX_MAX = 1040  # css px: the dialog never grows taller, it scrolls


def fit_sync_box(page: Page, name: str, target: Callable[[], float]) -> None:
    """Window with the 3:2 ratio of the guide image in which the dialog box is `target()` css pixels high.

    The dialog is as wide as the window allows, so its content reflows when the window changes: the target
    is measured again after every resize until the dialog height matches it.
    """
    height = page.viewport_size["height"]
    for _ in range(10):
        set_height(page, name, height, css_width=round(height * 1.5))
        want = min(target(), SYNC_BOX_MAX)
        have = sync_modal(page).evaluate("el => el.getBoundingClientRect().height")
        if abs(have - want) <= 1:
            SIZES[name] = (SIZES[name][0], 1280)
            page.wait_for_timeout(600)
            return
        height += round(want - have)
    raise RuntimeError(f"{name}: the dialog height did not settle")


def sync_window(page: Page, name: str, first: int, last: int) -> None:
    """Show editor sections first..last (0 based) whole, with the dialog's own scrolling and nothing injected.

    The window height decides how tall the dialog is. For sections at the top the dialog ends in the gap
    below the last section; for sections at the bottom the dialog is scrolled to its end and starts in the gap
    above the first section, below the sticky close bar.
    """
    gap = 12

    def target() -> float:
        info = sync_modal(page).evaluate(
            """modal => {
                const box = modal.getBoundingClientRect();
                modal.scrollTop = 10 ** 6;
                const close = modal.querySelector('.modal-close').getBoundingClientRect();
                const head = close.bottom - box.top + 16;
                modal.scrollTop = 0;
                const cards = [...modal.querySelectorAll('.editor-card')].map((c) => {
                    const r = c.getBoundingClientRect();
                    return [r.top - box.top, r.bottom - box.top];
                });
                return {cards, head, total: modal.scrollHeight};
            }"""
        )
        if first == 0:
            return info["cards"][last][1] + gap
        return info["total"] - (info["cards"][first][0] - gap - info["head"])

    fit_sync_box(page, name, target)
    sync_modal(page).evaluate("(m, top) => { m.scrollTop = top; }", 0 if first == 0 else 10**6)
    page.wait_for_timeout(500)


def fit_ratio(page: Page, name: str) -> None:
    """A dialog that fits whole: a window with the image ratio and room around it (the dialog does not scroll)."""
    height = 0
    for _ in range(4):
        box = sync_modal(page).evaluate("el => el.getBoundingClientRect().height")
        height = max(viewport(name)["height"], round(box) + 220)
        set_height(page, name, height, css_width=round(height * 1.5))
    SIZES[name] = (SIZES[name][0], 1280)
    page.wait_for_timeout(600)


def shift_run_stamps(socket) -> None:
    """Show the server's run times on the pinned clock.

    The sync run is stamped by the server on the real clock, but every shot has the page clock fixed at 09:00.
    Without this the dialog would read "in 6 Stunden" instead of "gerade eben". Every `at` stamp of the last
    hour in a websocket message from Home Assistant moves to two seconds before the pinned time.
    """
    import json

    server = socket.connect_to_server()
    shown = (CLOCK - timedelta(seconds=2)).astimezone().isoformat()

    def shift(node: object) -> object:
        if isinstance(node, dict):
            return {
                key: shown if key == "at" and recent(value) else shift(value)
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [shift(item) for item in node]
        return node

    def recent(value: object) -> bool:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.astimezone()
        return abs(datetime.now().astimezone() - stamp) < timedelta(hours=1)

    def from_server(message: str | bytes) -> None:
        if isinstance(message, str) and '"at"' in message:
            message = json.dumps(shift(json.loads(message)))
        socket.send(message)

    socket.on_message(lambda message: server.send(message))
    server.on_message(from_server)


def shots_sync(browser: Browser) -> None:
    list_id = mealie_source_list()
    context, page = ha_page(browser, "einkaufs-sync-leer", "dark")
    context.route_web_socket("**/api/websocket", shift_run_stamps)
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
            sync_window(page, "einkaufs-sync-einrichten", 0, 2)
            snap(page, "einkaufs-sync-einrichten")
        if wanted("einkaufs-sync-vorschau") or wanted("einkaufs-sync-status"):
            page.locator("[data-action='sync-preview']").click()
            page.get_by_text(re.compile("Beispielzeile")).first.wait_for(timeout=30_000)
            page.wait_for_timeout(800)
            if wanted("einkaufs-sync-vorschau"):
                sync_window(page, "einkaufs-sync-vorschau", 4, 6)
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


def open_status_bar(page: Page) -> None:
    """Open the status bar; the window ends in the gap under the first sidebar entry that follows the sub line.

    Both the text row "Deine Küche, neu gedacht." and the sidebar entries are then whole at the bottom edge.
    """
    page.locator("details.atelier-connectivity summary").click()
    page.wait_for_timeout(600)
    bottom = page.evaluate(
        """() => {
            const all = (sel, root = document) => {
                let out = [...root.querySelectorAll(sel)];
                for (const el of root.querySelectorAll('*')) if (el.shadowRoot) out = out.concat(all(sel, el.shadowRoot));
                return out;
            };
            const line = all('*').find((el) => el.children.length === 0 && /deine küche, neu gedacht/i.test(el.textContent));
            const need = line.getBoundingClientRect().bottom + 8;
            const edges = all('[data-action=nav]').map((el) => el.getBoundingClientRect().bottom).filter((y) => y > need);
            return Math.min(...edges) + 4;
        }"""
    )
    set_height(page, "web-app-statusleiste", round(bottom))


def shots_web(browser: Browser) -> None:
    with WebApp() as app:
        names = {
            "web-app": lambda page: None,
            "web-app-statusleiste": open_status_bar,
            "web-app-wochenplan": lambda page: (
                web_nav(page, "Wochenplan"),
                fit_document(page, "web-app-wochenplan"),
            ),
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
            # The dialog scrolls (it never gets taller than 1040 px). End it after the "Bewegung" group, just
            # above the divider of the next one, so no group is cut and no lone divider hangs at the edge.
            edge = page.locator(".modal[role='dialog'] >> visible=true").first.evaluate(
                """modal => {
                    const language = modal.querySelector("[data-input='language']").closest('.settings-group');
                    return language.nextElementSibling.getBoundingClientRect().bottom - modal.getBoundingClientRect().top - 2;
                }"""
            )
            fit_modal_height(page, "web-app-design-studio", edge)

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
            # The window is as wide as the first four tabs of the scrolling tab row, so no tab label is cut at the
            # right edge, and ends just above the "Heute auf dem Teller" card instead of in the middle of it.
            edge = page.evaluate(
                """() => {
                    const all = (sel, root = document) => {
                        let out = [...root.querySelectorAll(sel)];
                        for (const el of root.querySelectorAll('*')) if (el.shadowRoot) out = out.concat(all(sel, el.shadowRoot));
                        return out;
                    };
                    const kicker = all('*').find((el) => el.children.length === 0 && /heute auf dem teller/i.test(el.textContent));
                    let card = kicker;
                    while (card.getBoundingClientRect().top > kicker.getBoundingClientRect().top - 10) card = card.parentElement || card.getRootNode().host;
                    return card.getBoundingClientRect().top + window.scrollY;
                }"""
            )
            set_height(page, "web-app-handy", round(edge) - 10)
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
        browser = play.chromium.launch(headless=True, channel="chromium", args=["--lang=de-DE"])
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

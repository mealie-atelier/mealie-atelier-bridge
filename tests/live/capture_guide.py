"""Step-by-step screenshots for the setup guides (fictional data only).

Usage: uv run --with playwright python tests/live/capture_guide.py [out-dir] [language]

Run capture_screens.py first (it registers the module resource and signs the rig user in once).
Saves the Mealie API token page, the integration search, the Bridge options, the card picker, the
visual card editor and one dashboard per card type, desktop light. Every shot is independent: a
failing one is reported and the rest still run.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable
from pathlib import Path

from capture_screens import BASE, sign_in
from ha_ws import HaSocket, read_token
from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "state" / "guide"
LANGUAGE = sys.argv[2] if len(sys.argv) > 2 else "de"
MEALIE = "http://localhost:19925"
CARDS_DASHBOARD = "atelier-cards"
EDIT_DASHBOARD = "atelier-edit"
CARD_TYPES = [
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

# View titles shown as tabs in Home Assistant's header: (English, German).
TITLES = {
    False: [
        "Kitchen",
        "Today",
        "Recipes",
        "Meal plan",
        "Shopping",
        "Manage",
        "Ideas",
        "Pantry",
        "Family",
        "Cookbook",
    ],
    True: [
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
    ],
}


async def prepare() -> None:
    """Idempotently create a dashboard with one panel view per card type and an editable one."""
    async with HaSocket(BASE, read_token(str(HERE / "state" / "ha-token"))) as socket:
        _, dashboards = await socket.call({"type": "lovelace/dashboards/list"})
        existing = {item["url_path"] for item in dashboards or []}
        for url_path, title in ((CARDS_DASHBOARD, "Atelier"), (EDIT_DASHBOARD, "Küche")):
            if url_path not in existing:
                ok, error = await socket.call(
                    {
                        "type": "lovelace/dashboards/create",
                        "url_path": url_path,
                        "title": title,
                        "mode": "storage",
                        "show_in_sidebar": url_path == EDIT_DASHBOARD,
                        "icon": "mdi:silverware-fork-knife",
                    }
                )
                if not ok:
                    raise SystemExit(f"dashboard: {error}")
        views = [
            {
                "title": TITLES[LANGUAGE == "de"][index],
                "path": kind,
                "panel": True,
                "cards": [
                    {
                        "type": f"custom:mealie-atelier-{kind}",
                        "theme": "light",
                        "language": LANGUAGE,
                    }
                ],
            }
            for index, kind in enumerate(CARD_TYPES)
        ]
        ok, error = await socket.call(
            {
                "type": "lovelace/config/save",
                "url_path": CARDS_DASHBOARD,
                "config": {"views": views},
            }
        )
        if not ok:
            raise SystemExit(f"cards dashboard: {error}")
        edit = {
            "views": [
                {
                    "title": "Küche" if LANGUAGE == "de" else "Kitchen",
                    "path": "kitchen",
                    "type": "sections",
                    "sections": [
                        {
                            "type": "grid",
                            "cards": [
                                {"type": "heading", "heading": "Mealie"},
                                {
                                    "type": "custom:mealie-atelier-today",
                                    "theme": "light",
                                    "language": LANGUAGE,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        ok, error = await socket.call(
            {"type": "lovelace/config/save", "url_path": EDIT_DASHBOARD, "config": edit}
        )
        if not ok:
            raise SystemExit(f"edit dashboard: {error}")


def shoot(page: Page, name: str) -> None:
    page.wait_for_timeout(900)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"ok   {name}")


def mealie_token_page(page: Page) -> None:
    page.goto(f"{MEALIE}/login")
    page.locator("input[type='text'], input[type='email']").first.fill("changeme@example.com")
    page.locator("input[type='password']").first.fill("MyPassword")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
    page.goto(f"{MEALIE}/user/profile/api-tokens")
    page.wait_for_load_state("networkidle")
    shoot(page, "mealie-api-token")


def integration_search(page: Page) -> None:
    page.goto(f"{BASE}/config/integrations/dashboard")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name=re.compile("Integration hinzufügen|Add integration")).click()
    search = page.get_by_placeholder(re.compile("Markennamen|brand name")).first
    search.wait_for(timeout=30_000)
    search.fill("Mealie")
    page.wait_for_timeout(1200)
    shoot(page, "ha-integration-search")
    page.keyboard.press("Escape")


def bridge_options(page: Page) -> None:
    page.goto(f"{BASE}/config/integrations/integration/mealie_atelier")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name=re.compile("^(Konfigurieren|Configure)")).first.click()
    page.locator("ha-form >> visible=true").first.wait_for(timeout=30_000)
    page.wait_for_load_state("networkidle")
    shoot(page, "bridge-options")
    page.keyboard.press("Escape")


def card_picker(page: Page) -> None:
    page.goto(f"{BASE}/{EDIT_DASHBOARD}/kitchen?edit=1")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    page.get_by_role("button", name=re.compile("Karte hinzufügen|Add card")).first.click()
    page.get_by_role("tab", name=re.compile("Nach Karte|By card")).click()
    search = page.locator("hui-card-picker input >> visible=true").first
    search.wait_for(timeout=30_000)
    search.fill("Mealie Atelier")
    page.wait_for_timeout(1500)
    shoot(page, "ha-card-picker")
    page.locator("hui-card-picker .card >> visible=true").first.click()
    page.locator("mealie-atelier-card-editor >> visible=true").first.wait_for(timeout=30_000)
    page.wait_for_timeout(1500)
    shoot(page, "ha-card-editor")
    page.keyboard.press("Escape")


def card_types(page: Page) -> None:
    for kind in CARD_TYPES:
        page.goto(f"{BASE}/{CARDS_DASHBOARD}/{kind}")
        # Focused views have no .view-stage, so wait for the launch screen and the network.
        page.wait_for_function("!document.getElementById('ha-launch-screen')", timeout=60_000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)
        shoot(page, f"card-{kind}")


def main() -> None:
    asyncio.run(prepare())
    OUT.mkdir(parents=True, exist_ok=True)
    steps: list[Callable[[Page], None]] = [
        mealie_token_page,
        integration_search,
        bridge_options,
        card_picker,
        card_types,
    ]
    failed = []
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 960},
                color_scheme="light",
                locale="de-DE" if LANGUAGE == "de" else "en-US",
                device_scale_factor=2,
                service_workers="block",
            )
            page = context.new_page()
            sign_in(page)
            for step in steps:
                try:
                    step(page)
                except Exception as exc:  # noqa: BLE001 - report and keep the other shots
                    page.screenshot(path=str(OUT / f"failed-{step.__name__}.png"))
                    failed.append(f"{step.__name__}: {str(exc).splitlines()[0]}")
            context.close()
        finally:
            browser.close()
    for line in failed:
        print(f"FAIL {line}")
    print(f"screenshots in {OUT}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

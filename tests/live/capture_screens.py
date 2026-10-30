"""Product screenshots of the cards inside the rig's Home Assistant (fictional data only).

Usage: uv run --with playwright python tests/live/capture_screens.py [out-dir] [language]

Run ui_smoke.py first (it registers the module resource). Builds `atelier-shots-light` and
`atelier-shots-dark` dashboards with one panel view per card mode, signs in as the rig user and
saves desktop and phone screenshots in light and dark: the main views, a recipe and cooking mode.
Default out-dir: state/shots.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ha_ws import HaSocket, read_token, state_dir
from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).parent
BASE = "http://localhost:18123"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "state" / "shots"
LANGUAGE = sys.argv[2] if len(sys.argv) > 2 else "de"
DASHBOARD = "atelier-shots"
MODES = ["dashboard", "recipes", "mealplan", "shopping", "discover"]
# View titles as a household would name them; they show as tabs in Home Assistant's header.
TITLES = {
    "de": ["Küche", "Rezepte", "Wochenplan", "Einkauf", "Kochinspo"],
    "en": ["Kitchen", "Recipes", "Meal plan", "Shopping", "Cook ideas"],
}
DEVICES = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 1180, "height": 820},
    "phone": {"width": 390, "height": 844},
}


async def prepare() -> None:
    """Idempotently create one screenshot dashboard per theme with a panel view per mode."""
    titles = TITLES.get(LANGUAGE, TITLES["en"])
    async with HaSocket(BASE, read_token(str(Path(state_dir(str(HERE))) / "ha-token"))) as socket:
        _, dashboards = await socket.call({"type": "lovelace/dashboards/list"})
        existing = {item["url_path"] for item in dashboards or []}
        for theme in ("light", "dark"):
            url_path = f"{DASHBOARD}-{theme}"
            if url_path not in existing:
                ok, error = await socket.call(
                    {
                        "type": "lovelace/dashboards/create",
                        "url_path": url_path,
                        "title": "Atelier",
                        "mode": "storage",
                        "show_in_sidebar": False,
                    }
                )
                if not ok:
                    raise SystemExit(f"dashboard: {error}")
            views = [
                {
                    "title": title,
                    "path": mode,
                    "panel": True,
                    "cards": [
                        {
                            "type": "custom:mealie-atelier-card",
                            "mode": mode,
                            "theme": theme,
                            "language": LANGUAGE,
                        }
                    ],
                }
                for mode, title in zip(MODES, titles, strict=True)
            ]
            ok, error = await socket.call(
                {"type": "lovelace/config/save", "url_path": url_path, "config": {"views": views}}
            )
            if not ok:
                raise SystemExit(f"dashboard config: {error}")


def sign_in(page: Page) -> None:
    page.goto(f"{BASE}/{DASHBOARD}-light/dashboard")
    page.wait_for_url("**/auth/authorize**", timeout=30_000)
    page.locator("input[name='username']").fill("atelier", timeout=30_000)
    page.locator("input[name='password']").fill("atelier-test-only")
    page.keyboard.press("Enter")
    page.wait_for_url(lambda url: "auth_callback" not in url and DASHBOARD in url, timeout=30_000)
    page.wait_for_load_state("networkidle")


def hide_sidebar(page: Page) -> None:
    """Hide Home Assistant's own sidebar, as on a kitchen tablet, so only one menu shows."""
    page.evaluate("localStorage.setItem('dockedSidebar', JSON.stringify('always_hidden'))")
    page.reload()
    page.wait_for_load_state("networkidle")


def settle(page: Page) -> None:
    """Wait until the card shows content and every visible photo has decoded."""
    page.locator("mealie-atelier-card .view-stage").first.wait_for(timeout=60_000)
    # Home Assistant's launch screen can still cover a freshly loaded dashboard.
    page.wait_for_function("!document.getElementById('ha-launch-screen')", timeout=60_000)
    page.wait_for_load_state("networkidle")
    for img in page.locator("mealie-atelier-card img >> visible=true").all():
        img.evaluate("img => img.decode().catch(() => null)")
    page.wait_for_timeout(900)


def shoot(page: Page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"ok   {name}")


def main() -> None:
    asyncio.run(prepare())
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as play:
        browser = play.chromium.launch(
            headless=True, args=["--lang=de-DE" if LANGUAGE == "de" else "--lang=en-US"]
        )
        try:
            for device, viewport in DEVICES.items():
                for theme in ("light", "dark"):
                    context = browser.new_context(
                        viewport=viewport,
                        color_scheme=theme,
                        locale="de-DE" if LANGUAGE == "de" else "en-US",
                        device_scale_factor=2,
                        is_mobile=device != "desktop",
                        has_touch=device != "desktop",
                        # HA's service worker can hold requests and stall screenshots; tests skip it.
                        service_workers="block",
                    )
                    page = context.new_page()
                    sign_in(page)
                    hide_sidebar(page)
                    for mode in MODES:
                        page.goto(f"{BASE}/{DASHBOARD}-{theme}/{mode}")
                        settle(page)
                        shoot(page, f"{device}-{theme}-{mode}")
                    page.goto(f"{BASE}/{DASHBOARD}-{theme}/recipes")
                    settle(page)
                    page.locator(".recipe-tile >> visible=true").first.click()
                    page.locator("[data-action='cook-start'] >> visible=true").wait_for()
                    settle(page)
                    shoot(page, f"{device}-{theme}-recipe")
                    page.locator("[data-action='cook-start'] >> visible=true").click()
                    page.locator("[data-action='cook-next'] >> visible=true").wait_for()
                    settle(page)
                    shoot(page, f"{device}-{theme}-cook")
                    context.close()
        finally:
            browser.close()
    print(f"screenshots in {OUT}")


if __name__ == "__main__":
    main()

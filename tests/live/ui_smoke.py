"""Browser acceptance of the cards inside the rig's Home Assistant (headless Chromium).

Usage: uv run --with playwright python tests/live/ui_smoke.py [ha-url] [out-dir]

Registers the bundle as a Lovelace module resource at the Marketplace plugin path, creates a
dashboard with the main card, signs in as the rig user and checks that seeded recipes render
without console errors. Saves light and dark screenshots to out-dir (default: state/screens).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ha_ws import HaSocket, read_token
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:18123").rstrip("/")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "state" / "screens"
RESOURCE = "/local/community/mealie-atelier-cards/mealie-atelier-cards.js"
DASHBOARD = "atelier-live"
CARD = {"type": "custom:mealie-atelier-card", "mode": "recipes"}
WHOLE_PHOTO = """async img => {
  try { await img.decode(); } catch { return false; }
  const body = new Uint8Array(await (await fetch(img.src)).arrayBuffer());
  const riff = new DataView(body.buffer).getUint32(4, true);
  return img.naturalWidth > 0 && body.length === riff + 8;
}"""


async def prepare() -> None:
    """Idempotently add the module resource and a one-card dashboard, like a user would."""
    async with HaSocket(BASE, read_token(str(HERE / "state" / "ha-token"))) as socket:
        _, resources = await socket.call({"type": "lovelace/resources"})
        if not any(item["url"].split("?")[0] == RESOURCE for item in resources or []):
            ok, error = await socket.call(
                {"type": "lovelace/resources/create", "res_type": "module", "url": RESOURCE}
            )
            if not ok:
                raise SystemExit(f"resource: {error}")
        _, dashboards = await socket.call({"type": "lovelace/dashboards/list"})
        if not any(item["url_path"] == DASHBOARD for item in dashboards or []):
            dashboard = {
                "type": "lovelace/dashboards/create",
                "url_path": DASHBOARD,
                "title": "Atelier",
                "mode": "storage",
                "show_in_sidebar": True,
            }
            ok, error = await socket.call(dashboard)
            if not ok:
                raise SystemExit(f"dashboard: {error}")
        config = {"views": [{"title": "Atelier", "panel": True, "cards": [CARD]}]}
        ok, error = await socket.call(
            {"type": "lovelace/config/save", "url_path": DASHBOARD, "config": config}
        )
        if not ok:
            raise SystemExit(f"dashboard config: {error}")


def main() -> None:
    asyncio.run(prepare())
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            for scheme in ("light", "dark"):
                context = browser.new_context(
                    viewport={"width": 1440, "height": 960}, color_scheme=scheme, locale="de-DE"
                )
                page = context.new_page()
                page.on(
                    "console",
                    lambda msg: errors.append(msg.text) if msg.type == "error" else None,
                )
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(f"{BASE}/{DASHBOARD}/0")
                page.wait_for_url("**/auth/authorize**", timeout=30_000)
                page.locator("input[name='username']").fill("atelier", timeout=30_000)
                page.locator("input[name='password']").fill("atelier-test-only")
                page.keyboard.press("Enter")
                # The auth callback loads the frontend once more; wait for the settled page.
                page.wait_for_url(
                    lambda url: url.endswith(f"/{DASHBOARD}/0") and "auth_callback" not in url,
                    timeout=30_000,
                )
                page.wait_for_load_state("networkidle")
                card = page.locator("mealie-atelier-card")
                card.wait_for(state="attached", timeout=60_000)
                # Seeded fictional recipe; the card renders in its shadow root.
                page.locator("text=Gnocchi >> visible=true").first.wait_for(timeout=60_000)
                page.evaluate("window.__atelierSmoke = true")
                photos = page.locator(".recipe-tile img >> visible=true")
                photos.first.wait_for(timeout=30_000)
                for index in range(photos.count()):
                    photos.nth(index).scroll_into_view_if_needed()
                    # A truncated WebP still decodes its size, so compare the body to its RIFF size.
                    loaded = photos.nth(index).evaluate(WHOLE_PHOTO)
                    if not loaded:
                        raise SystemExit(f"FAIL recipe photo {index + 1} did not load ({scheme})")
                page.evaluate("window.scrollTo(0, 0)")
                page.locator("text=Gnocchi >> visible=true").first.scroll_into_view_if_needed()
                page.wait_for_timeout(800)
                print(f"ok   {photos.count()} recipe photos arrive whole ({scheme})")
                page.screenshot(path=str(OUT / f"recipes-{scheme}.png"))
                if not page.evaluate("window.__atelierSmoke === true"):
                    raise SystemExit(f"FAIL the page reloaded during the photo check ({scheme})")
                print(f"ok   recipes render ({scheme})")
                context.close()
        finally:
            browser.close()
    relevant = [text for text in errors if "mealie" in text.lower() or "atelier" in text.lower()]
    if relevant:
        raise SystemExit("console errors:\n" + "\n".join(relevant))
    print(f"ok   no card console errors; screenshots in {OUT}")


if __name__ == "__main__":
    main()

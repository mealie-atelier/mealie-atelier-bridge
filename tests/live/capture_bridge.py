"""Screenshots of the Bridge's own surfaces in the rig (fictional data only).

Usage: uv run --with playwright python tests/live/capture_bridge.py [out-dir] [language]
Run capture_screens.py first (it builds the screenshot dashboards). Saves the setup form, the
integration page and the Smart Shopping Sync dialogs in light theme. A sync rule is created for the
dialog shots and deleted again; it is never activated, so nothing is sent.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from capture_screens import BASE, DASHBOARD, settle, sign_in
from ha_ws import HaSocket, read_token
from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "state" / "shots"
LANGUAGE = sys.argv[2] if len(sys.argv) > 2 else "de"
TOKEN = str(HERE / "state" / "ha-token")


async def ws(command: dict) -> object:
    async with HaSocket(BASE, read_token(TOKEN)) as socket:
        ok, value = await socket.call(command)
        if not ok:
            raise SystemExit(f"{command['type']}: {value}")
        return value


async def create_rule() -> str:
    shopping = await ws({"type": "mealie_atelier/shopping"})
    rule = {
        "name": "Bring" if LANGUAGE == "de" else "Groceries",
        "source_list_id": shopping["lists"][0]["id"],
        "destination_entity_id": "todo.shopping_list",
        "mode": "copy",
    }
    return (await ws({"type": "mealie_atelier/sync_rule_save", "rule": rule}))["rule"]["id"]


async def abort_flows() -> None:
    for flow in await ws({"type": "config_entries/flow/progress"}):
        if flow.get("handler") == "mealie_atelier":
            await ws({"type": "config_entries/flow/abort", "flow_id": flow["flow_id"]})


def shoot(page: Page, name: str) -> None:
    page.wait_for_timeout(700)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"ok   {name}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rule_id = asyncio.run(create_rule())
    try:
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

                page.goto(f"{BASE}/config/integrations/integration/mealie_atelier")
                page.wait_for_load_state("networkidle")
                shoot(page, "bridge-integration")

                page.get_by_role("button", name=re.compile("Add service|Dienst hinzufügen")).click()
                page.locator("ha-form input >> visible=true").first.wait_for(timeout=30_000)
                page.wait_for_load_state("networkidle")
                shoot(page, "bridge-setup")

                page.goto(f"{BASE}/{DASHBOARD}-light/shopping")
                settle(page)
                page.locator("[data-action='sync-open'] >> visible=true").click()
                page.locator("[data-action='sync-setup-open'] >> visible=true").first.wait_for()
                shoot(page, "bridge-sync-status")
                page.locator(
                    f"[data-action='sync-setup-open'][data-value='{rule_id}'].primary"
                ).click()
                page.wait_for_load_state("networkidle")
                shoot(page, "bridge-sync-setup")
                context.close()
            finally:
                browser.close()
    finally:
        try:
            asyncio.run(ws({"type": "mealie_atelier/sync_rule_delete", "rule_id": rule_id}))
        finally:
            asyncio.run(abort_flows())
    print(f"screenshots in {OUT}")


if __name__ == "__main__":
    main()

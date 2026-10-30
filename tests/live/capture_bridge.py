"""Screenshots of the Bridge's own surfaces in the rig (fictional data only).

Usage: uv run --with playwright python tests/live/capture_bridge.py [out-dir] [language]
Run capture_screens.py first (it builds the screenshot dashboards). Saves the setup form, the
integration page and the Smart Shopping Sync dialogs in light theme. A sync rule is created for the
dialog shots and deleted again. Its first batch is sent once to Home Assistant's built-in shopping
list (a throwaway destination) so the status shot shows an active rule with a last run; the sent
items are removed from that list again.

Set CARDS_BUNDLE to a built mealie-atelier-cards.js to capture a cards build other than the one the
rig mounts, for example a worktree's dist, without touching the mounted files.
"""

from __future__ import annotations

import asyncio
import os
import json
import re
import sys
import urllib.request
from pathlib import Path

from capture_screens import BASE, DASHBOARD, settle, sign_in
from ha_ws import HaSocket, read_token
from playwright.sync_api import Page, sync_playwright

HERE = Path(__file__).parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "state" / "shots"
LANGUAGE = sys.argv[2] if len(sys.argv) > 2 else "de"
TOKEN = str(HERE / "state" / "ha-token")
CARDS_BUNDLE = os.environ.get("CARDS_BUNDLE")


MEALIE = "http://localhost:19925"


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


async def ws(command: dict) -> object:
    async with HaSocket(BASE, read_token(TOKEN)) as socket:
        ok, value = await socket.call(command)
        if not ok:
            raise SystemExit(f"{command['type']}: {value}")
        return value


def list_name() -> str:
    return "Wochenmarkt" if LANGUAGE == "de" else "Weekly shop"


async def create_rule(source_list_id: str) -> str:
    rule = {
        "name": "Bring!" if LANGUAGE == "de" else "Groceries",
        "source_list_id": source_list_id,
        "destination_entity_id": "todo.shopping_list",
        "mode": "copy",
    }
    return (await ws({"type": "mealie_atelier/sync_rule_save", "rule": rule}))["rule"]["id"]


DESTINATION = "todo.shopping_list"


def item_notes() -> list[str]:
    if LANGUAGE == "de":
        return ["Äpfel", "Haferflocken", "Zitronen"]
    return ["Apples", "Oats", "Lemons"]


async def activate(rule_id: str) -> None:
    """Send the first batch so the rule is active and has a run to show."""
    await ws({"type": "mealie_atelier/sync_run", "rule_id": rule_id, "confirm_first_batch": True})


async def clear_destination() -> None:
    listed = await ws({"type": "todo/item/list", "entity_id": DESTINATION})
    ours = [i["uid"] for i in listed["items"] if i["summary"] in item_notes()]
    if ours:
        await ws(
            {
                "type": "call_service",
                "domain": "todo",
                "service": "remove_item",
                "target": {"entity_id": DESTINATION},
                "service_data": {"item": ours},
            }
        )


async def abort_flows() -> None:
    for flow in await ws({"type": "config_entries/flow/progress"}):
        if flow.get("handler") == "mealie_atelier":
            await ws({"type": "config_entries/flow/abort", "flow_id": flow["flow_id"]})


# The status dialog stacks the add button directly on the "Recent runs" card without a gap
# (cards: no margin after .form-actions). Until the cards repo fixes that, the shot adds the gap.
GAP_STYLE = ".atelier .sync-panel .form-actions + .editor-card { margin-block-start: var(--ds-space-3, 12px); }"


def add_gap(page: Page) -> None:
    page.locator(".sync-panel").first.evaluate(
        """(el, css) => {
            const style = document.createElement('style');
            style.textContent = css;
            el.getRootNode().appendChild(style);
        }""",
        GAP_STYLE,
    )


def drop_focus(page: Page) -> None:
    """Blur the autofocused control (through shadow roots) so no focus ring shows."""
    page.locator(".sync-panel").first.evaluate(
        """el => {
            let node = el.getRootNode().activeElement;
            if (node && node.blur) node.blur();
            if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
        }"""
    )


def fit_dialog(page: Page) -> None:
    """Lift the dialog's height cap and size the viewport to it, so nothing is cut off."""
    page.locator(".sync-panel").first.evaluate(
        """(el, css) => {
            const style = document.createElement('style');
            style.textContent = css;
            el.getRootNode().appendChild(style);
        }""",
        ".atelier .modal.sync-modal { max-height: none !important; overflow: visible !important; }",
    )
    page.wait_for_timeout(500)
    height = page.locator(".modal.sync-modal").first.evaluate(
        "el => el.getBoundingClientRect().height"
    )
    page.set_viewport_size({"width": 1440, "height": int(height) + 160})
    page.wait_for_timeout(500)


def shoot(page: Page, name: str, blur: bool = False) -> None:
    page.wait_for_timeout(700)
    if blur:
        drop_focus(page)
        page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"ok   {name}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # A throwaway source list named in the capture language keeps the shots in one language.
    list_id = mealie("POST", "/api/households/shopping/lists", {"name": list_name()})["id"]
    rule_id = ""
    try:
        for note in item_notes():
            mealie(
                "POST",
                "/api/households/shopping/items",
                {"shoppingListId": list_id, "note": note, "quantity": 0, "checked": False},
            )
        rule_id = asyncio.run(create_rule(list_id))
        asyncio.run(activate(rule_id))
        with sync_playwright() as play:
            browser = play.chromium.launch(
                headless=True, args=["--lang=de-DE" if LANGUAGE == "de" else "--lang=en-US"]
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 960},
                    color_scheme="light",
                    locale="de-DE" if LANGUAGE == "de" else "en-US",
                    device_scale_factor=2,
                    service_workers="block",
                )
                if CARDS_BUNDLE:
                    bundle = Path(CARDS_BUNDLE).read_bytes()
                    context.route(
                        "**/mealie-atelier-cards.js*",
                        lambda route: route.fulfill(
                            body=bundle, content_type="text/javascript; charset=utf-8"
                        ),
                    )
                # The dashboards are saved in German; the card's own language setting and Home
                # Assistant's profile language both come from the browser.
                context.add_init_script(
                    f"localStorage.setItem('selectedLanguage', JSON.stringify('{LANGUAGE}'));"
                    f"localStorage.setItem('mealie-atelier:design', "
                    f"JSON.stringify({{language: '{LANGUAGE}'}}));"
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
                add_gap(page)
                shoot(page, "bridge-sync-status")
                page.locator(f"[data-action='sync-setup-open'][data-value='{rule_id}']").click()
                page.wait_for_load_state("networkidle")
                fit_dialog(page)
                shoot(page, "bridge-sync-setup", blur=True)
                context.close()
            finally:
                browser.close()
    finally:
        try:
            if rule_id:
                asyncio.run(ws({"type": "mealie_atelier/sync_rule_delete", "rule_id": rule_id}))
        finally:
            try:
                mealie("DELETE", f"/api/households/shopping/lists/{list_id}")
            finally:
                try:
                    asyncio.run(clear_destination())
                finally:
                    asyncio.run(abort_flows())
    print(f"screenshots in {OUT}")


if __name__ == "__main__":
    main()

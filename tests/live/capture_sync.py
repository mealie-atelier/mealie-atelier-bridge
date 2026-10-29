"""Step-by-step screenshots of Smart Shopping Sync into a Bring list (fictional items only).

Usage: SYNC_TODO=todo.<list> uv run --with aiohttp --with playwright python capture_sync.py [out-dir]

Needs a Bring entry in the rig (its owner signs in through the config flow) and a dedicated
test list; household lists are never a target. Creates a Mealie list with three fictional items,
walks through the sync setup in the shopping card, shows the result in Home Assistant's To-do
panel and removes the rule, the To-do items and the Mealie list again. Every account address is
replaced in the DOM before a shot, and a shot that would still show one fails.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from capture_guide import CARDS_DASHBOARD, prepare
from capture_screens import BASE, sign_in
from ha_ws import HaSocket, read_token
from playwright.sync_api import Page, sync_playwright
from sync_live import mealie

HERE = Path(__file__).parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "state" / "guide-sync"
TODO = os.environ.get("SYNC_TODO", "")
SOURCE = "Wochenmarkt"
RULE = "Wochenmarkt nach Bring"
ITEMS = ["Tomaten", "Basilikum", "Mozzarella"]
PLACEHOLDER = "kueche@example.com"
# The only list this script may write to; household lists are refused by name.
TEST_LIST = os.environ.get("SYNC_TEST_LIST", "Mealie Atelier")

# Replace every e-mail address in light and shadow DOM, then report any that is still visible.
MASK = """placeholder => {
  const pattern = /[\\w.+-]+@[\\w-]+(\\.[\\w-]+)+/g;
  const left = [];
  const walk = root => {
    const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = it.nextNode(); node; node = it.nextNode()) {
      if (pattern.test(node.nodeValue)) node.nodeValue = node.nodeValue.replace(pattern, placeholder);
    }
    for (const el of root.querySelectorAll('*')) {
      for (const attr of ['title', 'aria-label', 'value']) {
        const value = el.getAttribute && el.getAttribute(attr);
        if (value && pattern.test(value)) el.setAttribute(attr, value.replace(pattern, placeholder));
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  const scan = root => {
    const text = (root.textContent || '');
    for (const match of text.match(pattern) || []) if (match !== placeholder) left.push(match);
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) scan(el.shadowRoot);
  };
  scan(document.documentElement);
  return left.length;
}"""


def shoot(page: Page, name: str) -> None:
    page.wait_for_timeout(900)
    if page.evaluate(MASK, PLACEHOLDER):
        raise SystemExit(f"FAIL {name}: an account address is still visible")
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"ok   {name}")


def card(page: Page):
    return page.locator("mealie-atelier-shopping")


def bring_integration(page: Page) -> None:
    page.goto(f"{BASE}/config/integrations/integration/bring")
    page.wait_for_load_state("networkidle")
    shoot(page, "sync-bring-integration")


def sync_setup(page: Page) -> None:
    page.goto(f"{BASE}/{CARDS_DASHBOARD}/shopping")
    page.wait_for_function("!document.getElementById('ha-launch-screen')", timeout=60_000)
    page.wait_for_load_state("networkidle")
    card(page).locator("[data-action='sync-open']").click()
    page.locator("[aria-labelledby='sync-title']").wait_for(timeout=30_000)
    shoot(page, "sync-empty")
    card(page).locator("[data-action='sync-setup-open']").first.click()
    source = card(page).locator("[data-input='sync-source']")
    source.wait_for(timeout=30_000)
    source.select_option(label=SOURCE)
    name = card(page).locator("[data-input='sync-name']")
    name.fill(RULE)
    destination = card(page).locator("[data-input='sync-destination']")
    destination.locator(f"option[value='{TODO}']").wait_for(state="attached", timeout=30_000)
    destination.select_option(value=TODO)
    source.scroll_into_view_if_needed()
    shoot(page, "sync-setup-source")
    card(page).locator(".sync-fieldset").first.scroll_into_view_if_needed()
    shoot(page, "sync-setup-mode")
    preview = card(page).locator("[data-action='sync-preview']")
    preview.click()
    # The preview re-renders the editor, so scroll to its result instead of the old button.
    card(page).get_by_text(re.compile("Beispielzeile")).first.wait_for(timeout=30_000)
    page.wait_for_timeout(600)
    card(page).get_by_text(re.compile(r"^06 /")).first.scroll_into_view_if_needed()
    shoot(page, "sync-preview")
    card(page).locator("[data-action='sync-save'][data-value='send']").click()
    card(page).get_by_text(re.compile("Letzter Lauf")).first.wait_for(timeout=60_000)
    shoot(page, "sync-status")


def todo_panel(page: Page) -> None:
    page.goto(f"{BASE}/todo?entity_id={TODO}")
    page.wait_for_load_state("networkidle")
    for item in ITEMS:
        page.get_by_text(item, exact=False).first.wait_for(timeout=60_000)
    shoot(page, "sync-bring-list")


async def guard() -> None:
    """Refuse any target but the dedicated test list, whatever SYNC_TODO says."""
    async with HaSocket(BASE, read_token(str(HERE / "state" / "ha-token"))) as socket:
        _, states = await socket.call({"type": "get_states"})
    name = next(
        (s["attributes"].get("friendly_name", "") for s in states or [] if s["entity_id"] == TODO),
        "",
    )
    if name != TEST_LIST:
        raise SystemExit(f"{TODO} is {name!r}, not the dedicated test list {TEST_LIST!r}")


async def cleanup(list_id: str | None) -> None:
    async with HaSocket(BASE, read_token(str(HERE / "state" / "ha-token"))) as socket:
        _, state = await socket.call({"type": "mealie_atelier/sync_state"})
        for rule in (state or {}).get("rules", []):
            if rule.get("name") == RULE:
                await socket.call(
                    {"type": "mealie_atelier/sync_rule_delete", "rule_id": rule["id"]}
                )
        _, listed = await socket.call({"type": "todo/item/list", "entity_id": TODO})
        ours = [i["uid"] for i in (listed or {}).get("items", []) if i["summary"] in ITEMS]
        if ours:
            await socket.call(
                {
                    "type": "call_service",
                    "domain": "todo",
                    "service": "remove_item",
                    "target": {"entity_id": TODO},
                    "service_data": {"item": ours},
                }
            )
        _, after = await socket.call({"type": "todo/item/list", "entity_id": TODO})
        left = [i["summary"] for i in (after or {}).get("items", []) if i["summary"] in ITEMS]
        print("ok   To-do list clean" if not left else f"FAIL left on {TODO}: {left}")
    if list_id:
        mealie("DELETE", f"/api/households/shopping/lists/{list_id}")
        print("ok   Mealie list removed")


def main() -> None:
    if not TODO.startswith("todo."):
        raise SystemExit("Set SYNC_TODO to the dedicated Bring test list")
    asyncio.run(guard())
    asyncio.run(prepare())
    OUT.mkdir(parents=True, exist_ok=True)
    list_id = None
    failed = []
    try:
        list_id = mealie("POST", "/api/households/shopping/lists", {"name": SOURCE})["id"]
        for note in ITEMS:
            mealie(
                "POST",
                "/api/households/shopping/items",
                {"shoppingListId": list_id, "note": note, "quantity": 0, "checked": False},
            )
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 960},
                    color_scheme="light",
                    locale="de-DE",
                    device_scale_factor=2,
                    service_workers="block",
                )
                page = context.new_page()
                sign_in(page)
                for step in (bring_integration, sync_setup, todo_panel):
                    try:
                        step(page)
                    except Exception as exc:  # noqa: BLE001 - report and keep the other shots
                        print(f"FAIL {step.__name__}: {exc}")
                        try:
                            if page.evaluate(MASK, PLACEHOLDER):
                                raise RuntimeError("address still visible")
                            page.screenshot(path=str(OUT / f"failed-{step.__name__}.png"))
                        except Exception:  # noqa: BLE001, S110 - the shot is only a debugging aid
                            pass
                        failed.append(f"{step.__name__}: {str(exc).splitlines()[0]}")
                context.close()
            finally:
                browser.close()
    finally:
        asyncio.run(cleanup(list_id))
    for line in failed:
        print(f"FAIL {line}")
    print(f"screenshots in {OUT}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

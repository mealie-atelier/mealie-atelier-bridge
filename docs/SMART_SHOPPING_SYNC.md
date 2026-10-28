# Smart Shopping Sync

Smart Shopping Sync copies items from one Mealie shopping list to one Home Assistant To-do list, for example the list of your Bring! integration. It runs inside the Mealie Atelier Bridge, so it keeps working when no dashboard is open. The standalone web app does not sync.

It uses Home Assistant's generic To-do entity, so any integration whose To-do list can add items works as a destination. The Bridge never asks for Bring credentials and never calls a provider directly.

## How it works

1. An administrator creates a rule in the Atelier shopping view: the Mealie source list, the To-do destination, **Copy** or **Move**, when to sync and how duplicates are handled. Lists of the core Mealie integration cannot be a destination, so items never loop back into Mealie.
2. The preview shows how each line will look at the destination, for example `300 g Kartoffeln`. Amounts and units are kept as written in Mealie and never converted. A note goes into the item description when the destination supports one, otherwise it is appended in parentheses.
3. Nothing syncs on its own until the first batch has been sent and confirmed. After that a rule runs after Atelier adds ingredients to the list and, if enabled, when a regular check of the list finds changes made elsewhere (every 2 minutes by default). Changes are collected for 30 seconds by default, so one recipe becomes one batch.
4. **Copy** leaves Mealie unchanged. **Move** checks an item off in Mealie only after the destination accepted it, or after an identical open line was found there.
5. If the destination rejects an item, it stays open in Mealie and shows as failed until you retry. If the destination does not answer in time, the Bridge reads the destination list before sending again; when it cannot tell whether the item arrived, the item waits for your decision instead of risking a duplicate.
6. If an item is edited or removed in Mealie after it was sent, the rule shows a conflict. The destination is never changed or cleaned up automatically; you choose to dismiss the conflict or send the item again. Dismissing keeps the destination as it is. If a Move rule had already checked the item off in Mealie, dismissing accepts the current Mealie state and the item is not checked off again.
7. Every rule can be paused, resumed and run by hand. Rules, the outbox and a short run log are stored in Home Assistant and survive restarts. Deleting a rule removes its history.

## Item mapping

- The title is the food name, or the note when no food is linked, with quantity and unit in front: `2 Zitronen`, `300 g Kartoffeln`. The amount survives even when a destination has no description field.
- Decimal amounts use a comma when Home Assistant runs in German.
- Units and wording are kept as written. Unlike measures are never merged, and free-text items are never dropped.
- Every item is tracked by its Mealie item ID, not by its text, so renaming an item is detected as a change.

## Duplicates

| Policy | Behaviour |
|---|---|
| Skip exact matches (default) | A line whose title, amount included, matches an open destination item or another line of the batch is not sent again. Case and spacing are ignored. |
| Skip exact, merge same unit | Like above, and lines of the same food and unit in one batch are added up into one line. |
| Send everything | Every Mealie item is sent, even if the destination already has it. |

## Guarantees

- One rule never runs twice at the same time; a change during a run triggers one follow-up run.
- Move mode never checks off a Mealie item whose delivery is not confirmed.
- An uncertain delivery is reconciled against the destination list before anything is resent.
- Exactly-once delivery cannot be promised for providers without an idempotency key. The Bridge promises the next best thing: visible uncertainty, reconciliation and no silent source completion.

## Privacy

- Item text goes only to the To-do integration you picked.
- The run log keeps time, rule, item count and outcome, never item text. Diagnostics contain only counts.
- Tokens and credentials are never logged.

## Tested destinations

| Destination | Status |
|---|---|
| Home Assistant Shopping list (`todo.shopping_list`) | Passed the live end-to-end test: preview, first-batch gate, exactly-once delivery, duplicate skip, after-add trigger, move mode. |
| Bring! | Supported through Home Assistant's Bring! integration; the live test against a dedicated test account is pending. |

Other To-do integrations that can add items should work. Please report results in the [discussions](https://github.com/orgs/mealie-atelier/discussions).

The live test is `tests/live/sync_live.py`; it runs against disposable Mealie and Home Assistant containers with fictional data (see [`tests/live/README.md`](../tests/live/README.md)).

## References

- [Mealie integration in Home Assistant](https://www.home-assistant.io/integrations/mealie)
- [Home Assistant To-do list integration](https://www.home-assistant.io/integrations/todo/)
- [Home Assistant `todo.add_item` action](https://www.home-assistant.io/actions/todo.add_item/)
- [Bring! integration in Home Assistant](https://www.home-assistant.io/integrations/bring)

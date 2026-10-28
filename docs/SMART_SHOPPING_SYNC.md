# Mealie Atelier Smart Shopping Sync

**Status:** Implemented in the Bridge (0.10.0). Live acceptance against real providers is in progress. The sync uses Home Assistant's generic To-do entity contract, so any provider that supports adding items should work.
**Date:** 2026-10-28
**Source context:** Friend-provided Mealie → Home Assistant To-do → Bring automation; Mealie Atelier planning archive inspected read-only.

## How it works

Smart Shopping Sync copies items from one Mealie shopping list to one Home Assistant To-do list, for example the list of your Bring! integration. It runs inside the Mealie Atelier Bridge in Home Assistant; the standalone web app does not sync.

1. An administrator creates a rule in the Atelier shopping view: the Mealie source list, the To-do destination, **Copy** or **Move**, when to sync and how duplicates are handled. Lists of the core Mealie integration cannot be a destination, so items never loop back into Mealie.
2. The preview shows how each line will look at the destination, for example `300 g Kartoffeln`. Amounts and units are kept as written in Mealie and never converted. A note goes into the item description when the destination supports one, otherwise it is appended in parentheses.
3. Nothing syncs on its own until the first batch has been sent and confirmed. After that a rule runs after Atelier adds ingredients to the list and, if enabled, when a regular check of the list finds changes made elsewhere (every 2 minutes by default). Changes are collected for 30 seconds by default, so one recipe becomes one batch.
4. **Copy** leaves Mealie unchanged. **Move** checks an item off in Mealie only after the destination accepted it, or after an identical open line was found there.
5. If the destination rejects an item, it stays open in Mealie and shows as failed until you retry. If the destination does not answer in time, the Bridge reads the destination list before sending again; when it cannot tell whether the item arrived, the item waits for your decision instead of risking a duplicate.
6. If an item is edited or removed in Mealie after it was sent, the rule shows a conflict. The destination is never changed or cleaned up automatically; you choose to dismiss the conflict or send the item again. Dismissing keeps the destination as it is. If a Move rule had already checked the item off in Mealie, dismissing accepts the current Mealie state as is and the item is not checked off again (this deviates from the Wave 2 contract, section A.7, which says the record becomes `sent`).
7. Every rule can be paused, resumed and run by hand. Rules, the outbox and a short audit log (time, rule, count and outcome; no item text) are stored in Home Assistant and survive restarts.

## Product intent

Turn recipe planning into a dependable household shopping flow: ingredients added to a Mealie list can be copied or transferred to a selected Home Assistant To-do list such as Bring. Configure it in Atelier without editing YAML. Keep the feature optional and preserve normal Mealie shopping-list behavior.

## Recommended architecture

- **Cards UI:** Add an “Einkaufs-Sync” setup and status surface beside the existing shopping list and recipe-to-list workflow.
- **Bridge:** Own the sync rule, event handling, durable queue, retry state, reconciliation and safe source completion. Keep durable state in Home Assistant integration storage, not in a browser tab or card-local storage.
- **Source:** One selected Mealie shopping list. Read structured source items from Mealie, preserving stable item IDs, food/note text, quantity, unit and checked state.
- **Destination:** One selected HA `todo` entity per rule. Reuse the user's already configured HA integration; do not ask for Bring credentials or call Bring's private API.
- **Standalone app:** Continue to work without Home Assistant. Explain that live sync is an optional HA capability; do not add an implicit cloud dependency.

Home Assistant's Mealie integration exposes each Mealie shopping list as a To-do entity and documents a five-minute update interval. The generic To-do layer provides list-read/add/update actions. Bring synchronizes lists with HA; its integration documents state-based list examples and cloud polling. This makes the HA To-do entity the extensible v1 connector boundary. [Mealie integration](https://www.home-assistant.io/integrations/mealie), [To-do integration](https://www.home-assistant.io/integrations/todo/), [Bring! integration](https://www.home-assistant.io/integrations/bring)

## Setup flow

1. Select a Mealie source list.
2. Select an available HA To-do destination; show entity name, integration/provider and reported supported actions.
3. Choose **Copy** (leave Mealie unchanged) or **Move** (mark a Mealie item complete only after destination success or confirmed duplicate).
4. Choose when to sync: **Manual**, **After Atelier adds recipe ingredients** (default for immediacy), and optionally **Watch for changes elsewhere** (debounced).
5. Preview a sample line and the first pending batch, including how quantity, unit and note will appear at the destination.
6. Send the first batch as a preview/confirmation. Then show live status and controls to pause, retry, or inspect the last result.

Do not enable auto-sync by surprise. The setup screen must identify which list is the source and which is the destination before any write.

## Item mapping and duplicate policy

- Use the item's food name (or note when no food is linked) as the title.
- Include quantity and unit in the title, such as `300 g Kartoffeln`, so the essential amount survives even when a destination To-do provider does not support descriptions. Append a short note in the description only when the selected entity supports it.
- Preserve the source unit and wording. Do not silently convert units, merge unlike measures, or drop ambiguous free-text items.
- Offer a predictable duplicate policy: skip an exact pending/sent match by normalized title and quantity; optionally merge only when both normalized ingredient and unit match. Keep distinct recipes' separate lines when the quantities cannot be combined safely.
- Keep a durable mapping key from `(source entity/list, Mealie item ID)` to target result and state. Do not use display text alone as the primary identity.

Home Assistant's standard To-do `add_item` contract does not guarantee that every destination supports a description or returns a durable destination ID. Detect provider capability and adapt the preview; never depend on a Bring-only field for correctness. [Add To-do item](https://www.home-assistant.io/actions/todo.add_item/)

## Reliability contract

Use a persisted, serialized outbox per source/destination pair with item states such as `pending`, `sending`, `sent`, `source_completed`, `failed`, and `needs_reconciliation`.

- Debounce source bursts (about 30 seconds by default); expose the delay in settings. A source change made through Atelier can enqueue the exact items after Mealie confirms its write, avoiding an unnecessary wait for HA polling.
- For changes made outside Atelier, use the selected source-list watcher and debounce. Explain that the official HA Mealie entity can take up to five minutes to reflect remote Mealie changes; the watcher is not an instant Mealie webhook.
- Serialize processing for each rule. Do not launch overlapping transfers for the same list pair.
- Only mark the source item completed after the destination accepted it or a matching destination item was confirmed under the selected duplicate policy.
- Explicit destination failures leave the source open and visible as failed. Do not hide failures behind `continue_on_error` and then acknowledge the source.
- If the destination write times out or the process stops after the write but before recording success, read the destination list and reconcile before retrying. If the outcome remains ambiguous, pause that item for review instead of risking a duplicate.
- After an item is sent, compare later source snapshots against the recorded source fingerprint. If its name, quantity, unit, note, or checked/deleted state changes, mark the sync record `changed_after_send` or `removed_after_send` and show a conflict. Never silently overwrite or delete the destination item. V1 leaves reconciliation to an explicit user action; destination completion does not flow back to Mealie.
- Never claim mathematical exactly-once delivery unless the destination supports an idempotency key. The product guarantee is safe reconciliation, visible uncertainty and no silent source completion.
- Keep audit entries minimal: time, rule, item count and outcome; never log Mealie tokens, credentials, or full household content.

### Important issue in the supplied automation

The example sets `continue_on_error: true` on `todo.add_item` and then always marks the Mealie item completed. If adding to Bring fails, the source is still acknowledged and the ingredient can disappear from the user's open list. The Atelier implementation must gate source completion on confirmed destination success. The 30-second state trigger is useful for batching but can be delayed by the native Mealie integration's five-minute refresh and can miss edits that do not change the To-do entity's item count.

## Scope and extensibility

**V1:** One-way Mealie list → one HA To-do destination, copy/move modes, manual and Atelier-originated sync, debounced external watcher, durable queue, duplicate reconciliation, DE/EN UI, pause/retry, and clear failure states. Destination completion never changes Mealie in copy mode. Do not automatically mirror edits or deletions after a successful transfer.

**Compatible follow-on destinations:** Any Home Assistant integration that exposes a To-do entity with item creation support. Add provider-specific options only when a real provider offers useful capabilities not represented by HA's generic entity contract.

**Later:** Multiple destination rules, category/aisle mapping, store-specific lists, Bring urgency tags, scheduled digest summaries, and direct standalone connectors. Each requires provider capability and privacy review; none belongs in the initial sync contract.

Do not couple the product to Bring alone. HA integrations provide the adapter boundary; standalone mode remains useful without HA.

## UX, accessibility, privacy

- Use existing Atelier tokens and primitives; the configuration surface is a native Atelier panel, not a raw YAML editor.
- Make copy vs. move, source vs. destination, selected account/provider, pending count and last result explicit in text and icons.
- Support keyboard-only navigation, screen readers, touch targets and reduced motion. Use restrained state transitions; never rely on color or animation alone for errors or success.
- Provide an actionable error (“Bring is unavailable; 4 items remain open in Mealie”) and a safe retry. Never show “Done” when the destination outcome is unknown.
- The Bridge sends data only to the configured HA To-do provider. The standalone app does not sync shopping data to third parties.

## Verification plan

The sync is covered by automated tests; live acceptance checks the following against disposable instances:

1. Mapping for linked foods, note-only items, fractions, decimals, units, and optional notes.
2. Batching when one recipe adds many lines; multiple recipes added within the debounce window.
3. Copy mode leaves Mealie untouched; move mode completes only confirmed items.
4. Target success, target rejection, unavailable entity, timeout, HA restart mid-transfer, and retry after each state.
5. Exact duplicates, similar-but-not-equal names, unit mismatch, quantity mismatch, source edits/deletions after send, and destination item changed by a household member.
6. Two sync rules running concurrently; same rule receives updates while processing.
7. Real To-do providers (for example Bring! and the local HA Shopping list); list a provider as tested only after it passed.
8. Remote edits through Mealie UI with the documented polling delay; Atelier-originated recipe-to-list updates.
9. Standalone Web without HA, read-only Bridge mode, disabled sync, and German/English accessibility checks.
10. HA service traces confirm source completion occurs only after a successful destination write or verified duplicate.

## Release gates and open implementation inputs

- Confirm exact minimum Home Assistant and Mealie versions from canonical source and test matrix.
- Confirm the Bridge config flow can expose source/destination entity selectors and manage persisted sync rules cleanly.
- Validate Bring's current To-do entity capabilities (add, update, description) against a disposable HA instance; the generic HA docs do not promise identical optional-field support across providers.
- Run mutation tests against disposable Mealie/HA/Bring accounts; do not use household lists for public fixtures.
- Do not publish until retry/reconciliation and the “failed add never completes source” test pass.

## References

- [Mealie integration in Home Assistant](https://www.home-assistant.io/integrations/mealie)
- [Home Assistant To-do list integration](https://www.home-assistant.io/integrations/todo/)
- [Home Assistant `todo.add_item` action](https://www.home-assistant.io/actions/todo.add_item/)
- [Bring! integration in Home Assistant](https://www.home-assistant.io/integrations/bring)

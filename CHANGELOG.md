# Changelog

All notable changes to the Mealie Atelier Bridge are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- Shopping list editing. The mutate command accepts `shopping_update` (note and quantity), `shopping_delete` and `shopping_clear_checked`, behind the same write permission as the other shopping actions. The offline replay ledger accepts the same three actions: an update is compared with the value the user saw and becomes a conflict when another device changed it, a delete of an item that is already gone counts as done, and none of them is ever blocked as a possible duplicate after a crash. A new error code `shopping_item_not_found` reports an update of an item that no longer exists. The status payload advertises `features.shopping_edit`.

## [0.10.1] - 2026-10-29

### Added

- The AI helpers also work with an Anthropic API key (Claude); the provider follows the key.

### Fixed

- Install docs match Home Assistant 2026.11 (Marketplace "Add from link" with GitHub login, manual resource under Dashboards, "By card" tab, editor wording), name Mealie 3.x as recommended and gain an uninstall section. The Smart Shopping Sync doc uses the UI policy names. The comparison table no longer claims the built-in integration cannot write. README screenshot pairs fit GitHub's column width.
- Consistent German and English copy: "das Token", "Sicherung", "Benachrichtigungsdienst", the Mealie page name "API-Token" and "die Mealie Atelier Bridge".
- Cancelling a kitchen timer that already finished or is being delivered succeeds instead of failing, so the card can remove it.
- Kitchen timer notifications follow the Home Assistant language (German or English).
- `notify.persistent_notification` is no longer offered as the timer notification service and no longer creates a second notification. `notify.send_message` is rejected, because it can never deliver.
- Translated validation errors (too many timers, foreign timer, recipe changed, protected nutrition values) reach the card with their own error code instead of `invalid_request`.
- A refused USDA FoodData Central key is reported as `usda_key_invalid` (and an exhausted rate limit as `usda_rate_limited`) instead of a Mealie error.
- AI alternatives and the AI recipe review answer in the requested language (optional `language` field, else the Home Assistant language) with a matching disclaimer. The status lists `features.ai` so cards can hide the AI review without a key.
- The photo size limit reads 10 MB in all Bridge messages, like the cards.
- The security policy, the sponsor links and the Show and tell link match the organization defaults. The brand README no longer promises a license in CREDITS.md. The social preview mark is complete. The changelog links its version headings.
- A Mealie account can only be set up once, even when it is reachable under two addresses. Existing entries are migrated to an account based id.
- Existing entries with the old generated title are renamed during the upgrade, and reconfiguring the address renames a generated title too. Administrators stored in the write list are dropped during the upgrade. A list that held only administrators becomes empty, so every user may change data again.
- Entries are titled `Mealie Atelier Bridge · user@host`, so several servers stay distinguishable. The setup dialogs use the same product name.
- A pasted browser address such as `/login`, `/api` or `/g/home` is cut back to the server address. A port above 65535 is reported as an invalid address.
- Reconfigure and the options no longer send the saved Mealie token, USDA key or AI key to the browser. Leave a key field empty to keep it, or use the new remove switch.
- Administrators are no longer listed under the users allowed to change data, and the help text explains that a selection restricts everyone else.
- The reconfigure step shows the port 9090 hint for the Mealie Home Assistant app, and the example address uses port 9925.
- The setup retry message names only the HTTP status or error type, instead of mixing English text into the German message.
- The Mealie token, the USDA key and the AI key are entered in masked password fields.
- The timer notify service only accepts lowercase ASCII names such as `notify.mobile_app_pixel`.
- The example address in the URL help text is shown as code, so Home Assistant no longer turns it into a link.
- AI failures show their own message instead of claiming Mealie is unreachable.
- `mealie_atelier/status` accepts the `entry_id` that a card bound to one entry sends, instead of rejecting the request.
- The sign-up links in the options are passed as description placeholders, because hassfest rejects URLs inside translations.
- At most six requests per entry reach Mealie at the same time. Mealie stops answering for good once about 30 arrive at once, which a tablet with several cards could trigger.
- The live sync test also removes test items whose title Bring prefixes with a quantity.

### Changed

- New Home Assistant brand icons and logos (the Week Dial bridge mark), in light and dark variants, at normal and 2x size. They replace the photo icon.
- Live tests: `sync_live.py` can target a dedicated Bring list (`SYNC_TODO`), and `capture_sync.py` records the sync setup for the guide. It writes only to the list named `SYNC_TEST_LIST` (default "Mealie Atelier") and fails if an account address would be visible.
- The options explain what the USDA FoodData Central key and the OpenAI or Anthropic key are for, what is lost without them and where to get them, with links.

## [0.10.0] - 2026-10-29

### Added

- Per-user write grants. A new option limits who may change shopping lists and meal plans to selected Home Assistant users. An empty selection keeps the old behavior for everyone, administrators always can, and read-only mode still wins over any grant. Recipe management stays admin only. Journal entries and kitchen timers are personal and are blocked only by read-only mode. Every mutating command passes the same permission gate, and the status payload reports the permissions of the calling user.
- Structured recipe editing backend: ingredients with linked foods and units, recipe basics (name, description, servings), steps, cover photo, step photos and restore. Food and unit search creates a new entry only when no exact match exists.
- Recipe backups. Before every recipe write the Bridge saves a snapshot in Home Assistant storage (newest 10 per recipe, up to 200 recipes, the newest 3 cover photos as private files). If the snapshot cannot be saved, nothing is changed in Mealie. Backups can be listed and restored, and they are deleted when the integration is removed.
- Photo uploads in ordered chunks with size, checksum and file type checks (JPEG, PNG or WebP, at most 10 MB).
- Smart Shopping Sync. One Mealie shopping list can be copied or moved to a Home Assistant to-do list. The sync uses Home Assistant's generic To-do entity contract, so a provider that supports adding items should work. It keeps a persisted outbox per rule, checks an item off in Mealie (move mode) only after the destination accepted it, reconciles timeouts against the destination before sending again, flags edits made after sending as conflicts, and never runs automatically before an administrator confirmed the first batch. At most 5 rules, with pause, resume, run, retry and conflict handling. Diagnostics contain counts only. See [Smart Shopping Sync](docs/SMART_SHOPPING_SYNC.md).
- Offline replay ledger for the offline shopping queue of the cards (`mealie_atelier/offline_replay`). Queued toggles and adds are replayed idempotently: the operation is persisted before the Mealie write, conflicts with changes made elsewhere are detected, and a possibly sent add is never posted twice. The status payload advertises `features.offline_queue`.
- Config flow steps for re-authentication and reconfiguration, with a guard against switching an entry to a different Mealie account. Every option has a label and a help text, in German and English.
- Diagnostics with redacted credentials and counts instead of content.
- Marketplace readiness: brand folder with Marketplace-sized icons (`icon.png` 256 px, `icon@2x.png` 512 px), a complete `manifest.json` (dependencies, documentation, issue tracker, category) and a `validate.yml` workflow that runs the HACS action and hassfest.
- A disposable live acceptance rig in `tests/live/` that starts Mealie and Home Assistant in Docker, seeds fictional demo recipes and runs a smoke test and a browser test of the cards.
- A local development environment with `uv`, a pinned lock file and a `.githooks/pre-commit` hook that runs Ruff and the tests.

### Changed

- The integration stores its state on `entry.runtime_data` and uses translated exceptions, in line with current Home Assistant conventions.
- The option for experimental ingredient editing now enables the structured ingredient editor. Plain-text ingredient replacement is no longer available and is refused with `use_structured_editor`.
- Removing the integration also deletes the shopping sync outbox, which holds item titles.
- Documentation (FAQ, installation guide, contributing notes) was brought up to date.

### Fixed

- Kitchen timers: finished timers are removed after two days and only active timers count against the global cap of 300, so stored finished timers can no longer block new ones for everyone. Each user may also have at most 20 active timers.
- Photo proxy: recipe photos, step images and the original-photo backup were cut off at the first network chunk (about 64 KiB). Bodies are now read until the end or the size limit, so large photos arrive whole.
- Four contract error codes (`invalid_request`, `mealie_auth`, `mealie_error`, `conflict`) had no translated message, so German users saw raw English. Every contract error code is now translated.
- Invalid recipe update payloads are rejected before a backup is taken, so they cannot push real restore points out of the ten slots.
- Setup loads the backup store before starting timers and sync, so a failed setup leaves nothing running. Timers and background sync passes shut down cleanly on unload.
- Offline ledger: the record of an operation in hand is no longer pruned when the ledger is full, a completed add expires only by age, open records are bounded, and a stored operation without an action no longer breaks pruning. A lost answer therefore cannot make the same add reach Mealie twice.

## Earlier history

Before 0.10.0 the repository was an unreleased developer preview with no tagged versions. It contained the initial import of the Bridge source (secure server-side Mealie access, WebSocket commands for recipes, meal plans, shopping lists, nutrition, cooking sessions and timers, optional read-only mode, German and English setup translations), the pasta and tomatoes brand artwork, security and support links, and the first FAQ.

[Unreleased]: https://github.com/mealie-atelier/mealie-atelier-bridge/compare/v0.10.1...HEAD
[0.10.1]: https://github.com/mealie-atelier/mealie-atelier-bridge/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/mealie-atelier/mealie-atelier-bridge/releases/tag/v0.10.0

# Changelog

All notable changes to the Mealie Atelier Bridge are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `mealie_atelier/status` accepts the `entry_id` that a card bound to one entry sends, instead of rejecting the request.
- The sign-up links in the options are passed as description placeholders, because hassfest rejects URLs inside translations.

### Changed

- The options explain what the USDA FoodData Central and OpenAI keys are for, what is lost without them and where to get them, with links.

## [0.10.0] - 2026-10-29

### Added

- Per-user write grants. A new option limits who may change shopping lists and meal plans to selected Home Assistant users. An empty selection keeps the old behavior for everyone, administrators always can, and read-only mode still wins over any grant. Recipe management stays admin only. Journal entries and kitchen timers are personal and are blocked only by read-only mode. Every mutating command passes the same permission gate, and the status payload reports the permissions of the calling user.
- Structured recipe editing backend: ingredients with linked foods and units, recipe basics (name, description, servings), steps, cover photo, step photos and restore. Food and unit search creates a new entry only when no exact match exists.
- Recipe backups. Before every recipe write the Bridge saves a snapshot in Home Assistant storage (newest 10 per recipe, up to 200 recipes, the newest 3 cover photos as private files). If the snapshot cannot be saved, nothing is changed in Mealie. Backups can be listed and restored, and they are deleted when the integration is removed.
- Photo uploads in ordered chunks with size, checksum and file type checks (JPEG, PNG or WebP, at most 10 MiB).
- Smart Shopping Sync. One Mealie shopping list can be copied or moved to a Home Assistant To-do list. The sync uses Home Assistant's generic To-do entity contract, so a provider that supports adding items should work. It keeps a persisted outbox per rule, checks an item off in Mealie (move mode) only after the destination accepted it, reconciles timeouts against the destination before sending again, flags edits made after sending as conflicts, and never runs automatically before an administrator confirmed the first batch. At most 5 rules, with pause, resume, run, retry and conflict handling. Diagnostics contain counts only. See [Smart Shopping Sync](docs/SMART_SHOPPING_SYNC.md).
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

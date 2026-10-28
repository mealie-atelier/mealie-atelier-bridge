# Live test rig

A disposable Mealie and Home Assistant pair for acceptance tests of the Bridge and the cards.
Fictional demo recipes only; never point these scripts at a real household instance.

```bash
./rig.sh up       # Mealie :19925, Home Assistant beta :18123, onboarded, seeded, integration added
./rig.sh status   # containers and versions
./rig.sh down     # stop and delete volumes and generated state
```

- The Bridge is mounted read-only from `custom_components/mealie_atelier`.
- The cards bundle from `../mealie-atelier-cards/dist` is mounted where the Marketplace puts plugins:
  `/local/community/mealie-atelier-cards/mealie-atelier-cards.js`.
- Smoke test of the Bridge against the rig: `python3 tests/live/smoke.py` (35 checks: reads, a recipe
  edit with backup and restore, the structured-editor guard, shopping sync reads, offline replay).
- Smart Shopping Sync end to end: `python3 tests/live/sync_live.py`. Syncs throwaway Mealie lists
  into Home Assistant's built-in shopping list (`todo.shopping_list`, no account needed): preview,
  first-batch gate, exactly-once delivery, duplicate skip, the after-add trigger and move mode.
  Everything it creates is removed again.
- Browser acceptance of the cards inside Home Assistant (headless Chromium, light and dark):
  `uv run --with playwright python tests/live/ui_smoke.py`. It registers the module resource at the
  Marketplace path, adds an `atelier-live` dashboard, checks that every recipe photo arrives whole and
  that the recipe dialog opens above the sidebar, and saves screenshots to `state/screens/`.
- Product screenshots (desktop and phone, light and dark, every mode plus a recipe and cooking
  mode): `uv run --with playwright python tests/live/capture_screens.py [out-dir] [de|en]`, after
  `ui_smoke.py`. Default out-dir is `state/shots/`.
- Language: `SEED_LANGUAGE=en ./rig.sh up` seeds English recipes and an English Home Assistant user
  (default `de`). The seed is idempotent, adds a short fictional shopping list and removes items the
  smoke test left behind.
- Ad-hoc commands: `python3 tests/live/ha_ws.py http://localhost:18123 tests/live/state/ha-token '{"type": "mealie_atelier/status"}'`.
- Version matrix: `HA_IMAGE=ghcr.io/home-assistant/home-assistant:stable` or
  `MEALIE_IMAGE=ghcr.io/mealie-recipes/mealie:v2.8.0 ./rig.sh up`.
- Tokens and the HA config live in `state/` (git-ignored, private to your user). Both ports listen on
  127.0.0.1 only. The Home Assistant login is `atelier` / `atelier-test-only`, Mealie uses its
  documented first-run admin. Both exist only inside the disposable containers.

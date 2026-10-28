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
- Smoke test of the Bridge against the rig: `python3 tests/live/smoke.py` (25 checks: reads, a recipe
  edit with backup and restore, the structured-editor guard, shopping sync reads).
- Ad-hoc commands: `python3 tests/live/ha_ws.py http://localhost:18123 tests/live/state/ha-token '{"type": "mealie_atelier/status"}'`.
- Version matrix: `HA_IMAGE=ghcr.io/home-assistant/home-assistant:stable` or
  `MEALIE_IMAGE=ghcr.io/mealie-recipes/mealie:v2.8.0 ./rig.sh up`.
- Tokens and the HA config live in `state/` (git-ignored, mode 600). The Home Assistant login is
  `atelier` / `atelier-test-only`, Mealie uses its documented first-run admin. Both exist only inside
  the disposable containers.

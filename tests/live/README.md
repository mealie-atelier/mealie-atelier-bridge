# Live test rig

A disposable Mealie and Home Assistant pair for acceptance tests of the Bridge and the cards.
Fictional demo recipes only; never point these scripts at a real household instance.

```bash
./rig.sh up       # Mealie on :19925, Home Assistant (beta) on :18123, onboarded and seeded
./rig.sh status   # containers and versions
./rig.sh down     # stop and delete volumes and generated state
```

- The Bridge is mounted read-only from `custom_components/mealie_atelier`.
- The cards bundle from `../mealie-atelier-cards/dist` is mounted where the Marketplace puts plugins:
  `/local/community/mealie-atelier-cards/mealie-atelier-cards.js`.
- Version matrix: `HA_IMAGE=ghcr.io/home-assistant/home-assistant:stable` or
  `MEALIE_IMAGE=ghcr.io/mealie-recipes/mealie:v2.8.0 ./rig.sh up`.
- Tokens and the HA config live in `state/` (git-ignored, mode 600). The Home Assistant login is
  `atelier` / `atelier-test-only`, Mealie uses its documented first-run admin. Both exist only inside
  the disposable containers.

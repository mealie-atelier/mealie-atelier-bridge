# Mealie Atelier Bridge

An optional Home Assistant custom integration that connects Mealie to Mealie Atelier Cards. It does not replace Home Assistant's official Mealie integration.

<img align="right" width="132" alt="Mealie Atelier Bridge icon with pasta and tomatoes" src="custom_components/mealie_atelier/brand/icon.png">

## What it provides

- Secure server-side Mealie API access for the Atelier Cards.
- Authenticated Home Assistant WebSocket commands for recipes, meal plans, shopping lists, nutrition workflows, cooking sessions, and timers.
- German and English setup translations.
- Optional read-only mode enforced by the backend.
- Optional per-user write grants: pick which Home Assistant users may change shopping lists and meal plans (empty means everyone). Administrators always can; recipe editing stays admin only.

- Optional Smart Shopping Sync from one Mealie shopping list to a Home Assistant To-do list, such as your Bring! list. Live acceptance against real providers is still pending.

## Project status and help

- [Open work and release gates](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/OPEN_TODOS.md)
- [Bridge frequently asked questions](docs/FAQ.md)
- [Setup and troubleshooting discussions](https://github.com/orgs/mealie-atelier/discussions)
- [Cards and standalone app](https://github.com/mealie-atelier/mealie-atelier-cards)

## Install

For guided setup and the read-only alternative, use the [Cards setup guide](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/INSTALLATION_0.9.5.md). Install this Bridge through HACS as an integration, then add it from **Settings → Devices & services**. Enter the direct Mealie base URL reachable from Home Assistant and an API token. Install the Cards package separately as a dashboard plugin.

Until a tagged release is published, this repository is source code for development and should not be treated as a stable catalog package.

## Set up Smart Shopping Sync

The sync runs in the Bridge, so it keeps working when no dashboard is open. The standalone web app does not sync.

1. Set up the destination first through its own Home Assistant integration (for example Bring!), so it appears as a To-do entity. The Bridge never asks for Bring credentials.
2. As a Home Assistant administrator, open the shopping view of the Atelier Cards and start the sync setup. Read-only mode must be off.
3. Pick the Mealie source list and the To-do destination. Lists of the core Mealie integration are not offered, so items cannot loop back into Mealie.
4. Choose **Copy** (Mealie stays unchanged) or **Move** (an item is checked off in Mealie only after the destination accepted it or an identical open line was found there), when to sync, and the duplicate policy.
5. Check the preview and send the first batch. Nothing syncs on its own before this confirmation.
6. Use the rule status to pause, resume, run by hand, retry failed items and decide on conflicts.

How a rule behaves after setup, including failures and conflicts, is described in [`docs/SMART_SHOPPING_SYNC.md`](docs/SMART_SHOPPING_SYNC.md#how-it-works). Diagnostics include only counts, never item text.

## Security model

Tokens are stored in Home Assistant config-entry data and sent server-to-server. Never put a Mealie token into card YAML or browser code. The Bridge exposes explicit, allowlisted commands rather than an arbitrary API proxy. Read-only mode blocks write operations at the backend boundary. Disable SSL verification only on a trusted private network with a known self-signed certificate.

## Manual local verification

```sh
python -m pip install aiohttp pytest ruff
pytest -q
ruff check custom_components tests
ruff format --check custom_components tests
python -m compileall -q custom_components/mealie_atelier
```

The repositories intentionally have no GitHub Actions or automatic dependency-update pull requests; maintainers run and review these checks manually before release. These checks use local fixtures. They do not verify a live Home Assistant, Mealie, Bring, HACS, or physical device. See the [Cards release checklist](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/RELEASE_READINESS_0.9.5.md) for the external acceptance gates.

## License and support

MIT licensed. This is an unofficial Mealie companion.

Made with love by Daniel Boberg · [alles-automatisch.de](https://alles-automatisch.de/). Support is optional: [Ko-fi](https://ko-fi.com/allesautomatisch).

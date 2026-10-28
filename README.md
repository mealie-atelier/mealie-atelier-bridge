# Mealie Atelier Bridge

An optional Home Assistant custom integration that connects Mealie to Mealie Atelier Cards. It does not replace Home Assistant's official Mealie integration.

![Mealie Atelier Bridge icon](custom_components/mealie_atelier/brand/icon.png)

## What it provides

- Secure server-side Mealie API access for the Atelier Cards.
- Authenticated Home Assistant WebSocket commands for recipes, meal plans, shopping lists, nutrition workflows, cooking sessions, and timers.
- German and English setup translations.
- Optional read-only mode enforced by the backend.

The Bridge currently does **not** implement the planned Mealie-to-Home-Assistant To-do/Bring shopping sync. The safety and implementation requirements are tracked in [`docs/SMART_SHOPPING_SYNC.md`](docs/SMART_SHOPPING_SYNC.md).

## Install

For guided setup and the read-only alternative, use the [Cards setup guide](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/INSTALLATION_0.9.5.md). Install this Bridge through HACS as an integration, then add it from **Settings → Devices & services**. Enter the direct Mealie base URL reachable from Home Assistant and an API token. Install the Cards package separately as a dashboard plugin.

Until a tagged release is published, this repository is source code for development and should not be treated as a stable catalog package.

## Security model

Tokens are stored in Home Assistant config-entry data and sent server-to-server. Never put a Mealie token into card YAML or browser code. The Bridge exposes explicit, allowlisted commands rather than an arbitrary API proxy. Read-only mode blocks write operations at the backend boundary. Disable SSL verification only on a trusted private network with a known self-signed certificate.

## Local verification

```sh
python -m pip install aiohttp pytest ruff
pytest -q
ruff check custom_components tests
ruff format --check custom_components tests
python -m compileall -q custom_components/mealie_atelier
```

These checks use local fixtures. They do not verify a live Home Assistant, Mealie, Bring, HACS, or physical device. See the [Cards release checklist](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/RELEASE_READINESS_0.9.5.md) for the external acceptance gates.

## License and support

MIT licensed. This is an unofficial Mealie companion.

Made with love by Daniel Boberg · [alles-automatisch.de](https://alles-automatisch.de/). Support is optional: [Ko-fi](https://ko-fi.com/allesautomatisch).

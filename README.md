<p align="center"><img width="112" alt="Mealie Atelier Bridge icon: handmade pasta with tomatoes" src="https://raw.githubusercontent.com/mealie-atelier/mealie-atelier-bridge/main/custom_components/mealie_atelier/brand/icon@2x.png"></p>

<h1 align="center">Mealie Atelier Bridge</h1>

<p align="center"><strong>The Home Assistant integration behind Mealie Atelier.</strong><br>It keeps your Mealie token on the server and gives the Atelier cards recipe editing with backups, per-user write grants, offline shopping and Smart Shopping Sync to any To-do list, such as Bring!.</p>

<p align="center">
<a href="https://github.com/mealie-atelier/mealie-atelier-bridge/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/mealie-atelier/mealie-atelier-bridge?style=flat-square&color=75465d"></a>
<a href="https://github.com/mealie-atelier/mealie-atelier-bridge/actions/workflows/validate.yml"><img alt="Validate" src="https://img.shields.io/github/actions/workflow/status/mealie-atelier/mealie-atelier-bridge/validate.yml?branch=main&style=flat-square&label=validate"></a>
<a href="https://hacs.xyz"><img alt="HACS Integration" src="https://img.shields.io/badge/HACS-Integration-41BDF5?style=flat-square"></a>
<img alt="Home Assistant 2026.10 or newer" src="https://img.shields.io/badge/Home%20Assistant-2026.10%2B-41BDF5?style=flat-square">
<a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/github/license/mealie-atelier/mealie-atelier-bridge?style=flat-square"></a>
</p>

<p align="center">
<a href="https://mealie.alles-automatisch.de"><strong>Try the live demo</strong></a> ·
<a href="https://alles-automatisch.de/lernen/mealie-atelier-einrichten">Setup guide</a> ·
<a href="https://github.com/mealie-atelier/mealie-atelier-cards">Atelier Cards</a> ·
<a href="https://github.com/orgs/mealie-atelier/discussions">Discussions</a>
</p>

<p align="center"><img width="900" alt="Mealie Atelier kitchen overview in a Home Assistant dashboard, light theme" src="https://raw.githubusercontent.com/mealie-atelier/mealie-atelier-cards/main/assets/screens/dashboard-light.webp"></p>

<p align="center"><sub><a href="#why-a-bridge">Why a Bridge</a> · <a href="#install">Install</a> · <a href="#configuration-reference">Configuration</a> · <a href="#smart-shopping-sync">Smart Shopping Sync</a> · <a href="#security">Security</a> · <a href="#documentation">Docs</a> · <a href="#development">Development</a> · <a href="#support-the-project">Support</a></sub></p>

## Why a Bridge

The [Atelier Cards](https://github.com/mealie-atelier/mealie-atelier-cards) can already browse recipes through Home Assistant's built-in [Mealie integration](https://www.home-assistant.io/integrations/mealie/). The Bridge adds everything that needs a trusted server side. It complements the built-in integration and does not replace it.

| | Built-in Mealie integration | With the Bridge |
| --- | --- | --- |
| Recipes, search and cooking mode | ✓ | ✓ |
| Recipe and step photos | | ✓ |
| Shopping lists and meal plan writes | | ✓ |
| Recipe editor with backups and restore | | ✓ |
| Per-user write grants and read-only mode | | ✓ |
| Offline shopping, replayed exactly once | | ✓ |
| Smart Shopping Sync to a To-do list | | ✓ |
| Cooking timers that notify after the browser closes | | ✓ |

## Install

Requirements: Home Assistant 2026.10 or newer and a Mealie server that Home Assistant can reach.

### 1. Download the integration

The Bridge is submitted to the default catalog (`hacs/default`) that the Home Assistant Marketplace and HACS both read. Pick one route:

| Route | Steps |
| --- | --- |
| **Home Assistant Marketplace** (Home Assistant 2026.11 and newer) | Open the Marketplace, search for **Mealie Atelier Bridge** and download it. Available as soon as the catalog listing is merged. |
| **HACS custom repository** (available now) | Add the repository as a custom repository of type **Integration**, then download it. The button below does this in one click. |
| **Manual** | Download the source of the [latest release](https://github.com/mealie-atelier/mealie-atelier-bridge/releases/latest) and copy `custom_components/mealie_atelier` into the `custom_components` folder of your configuration. |

[![Open the repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mealie-atelier&repository=mealie-atelier-bridge&category=integration)

Restart Home Assistant after the download.

### 2. Connect Mealie

Add the integration and enter the Mealie address Home Assistant can reach and a Mealie API token (Mealie: **User profile → API Tokens**).

[![Add the integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=mealie_atelier)

<p align="center"><img width="440" alt="Setup form asking for the Mealie server URL and API token" src="https://raw.githubusercontent.com/mealie-atelier/mealie-atelier-bridge/main/assets/screens/setup.webp"> <img width="440" alt="Mealie Atelier Bridge on the Home Assistant integration page" src="https://raw.githubusercontent.com/mealie-atelier/mealie-atelier-bridge/main/assets/screens/integration.webp"></p>

### 3. Add the cards

Install [Mealie Atelier Cards](https://github.com/mealie-atelier/mealie-atelier-cards) and add a **Mealie Atelier** card to a dashboard. The card finds the Bridge on its own.

Step-by-step guides in German: [setup](https://alles-automatisch.de/lernen/mealie-atelier-einrichten) and [troubleshooting](https://alles-automatisch.de/lernen/mealie-atelier-fehlerbehebung). The full English walkthrough is the [installation guide](docs/INSTALLATION.md).

## Configuration reference

<details>
<summary>Setup form and integration options</summary>

**Setup form**

| Field | Meaning |
| --- | --- |
| Mealie server URL | The direct address Home Assistant can reach, with `http://` or `https://` and the port. No `/login` or `/api` path, no ingress URL, no credentials in the URL. |
| Mealie API token | Created in Mealie under **User profile → API Tokens**. Stored in the Home Assistant config entry and never sent to the browser. |
| Verify TLS certificate | On by default. Turn it off only on a trusted private network with a self-signed certificate. |

**Options** (**Settings → Devices & services → Mealie Atelier Bridge → Configure**)

| Option | Meaning |
| --- | --- |
| Read-only mode | Blocks all changes to recipes, meal plans and shopping lists. It wins over every write grant. |
| Users allowed to change data | Limits shopping list and meal plan changes to the chosen Home Assistant users. Empty means every user. Administrators can always write, and recipe editing stays admin only. |
| Structured ingredient editing | Lets administrators edit ingredients with linked foods and units. A backup is saved before every change. |
| USDA FoodData Central API key | Optional. Free key for nutrition lookup. |
| AI API key (OpenAI or Anthropic) | Optional. Turns on the AI helpers: food search suggestions, ingredient substitutes and the recipe check. A key starting with `sk-ant-` uses Anthropic Claude, any other key uses OpenAI. |
| Timer notification service | Optional. A notify service such as `notify.mobile_app_your_phone` that gets a push message when a kitchen timer ends. |

Re-authentication and **Reconfigure** are available from the integration page. Details and troubleshooting are in the [installation guide](docs/INSTALLATION.md).

</details>

## Smart Shopping Sync

Send what you need from a Mealie shopping list to the list your household actually shops with, for example Bring! through its Home Assistant integration. The sync runs in Home Assistant, so it works when no dashboard is open.

<p align="center"><img width="440" alt="Shopping sync rule with its state and actions" src="https://raw.githubusercontent.com/mealie-atelier/mealie-atelier-bridge/main/assets/screens/sync-status.webp"> <img width="440" alt="Sync rule editor with source list, destination list and copy or move mode" src="https://raw.githubusercontent.com/mealie-atelier/mealie-atelier-bridge/main/assets/screens/sync-setup.webp"></p>

1. Set up the destination through its own integration first, so it appears as a To-do list. The Bridge never asks for Bring credentials.
2. As an administrator, open the Atelier shopping view and add a sync rule: Mealie source list, To-do destination, **Copy** or **Move**, when to sync and how to handle duplicates.
3. Check the preview and send the first batch. Nothing syncs on its own before this confirmation.

**Move** checks an item off in Mealie only after the destination accepted it. An uncertain delivery is checked against the destination before anything is resent, and conflicts wait for your decision. Details are in [`docs/SMART_SHOPPING_SYNC.md`](docs/SMART_SHOPPING_SYNC.md).

## Security

- The Mealie token lives in the Home Assistant config entry and is only used server to server. It never reaches card YAML or the browser.
- The Bridge exposes an allowlist of WebSocket commands, not an open API proxy. Every write checks the Home Assistant user's grant and read-only mode on the server.
- Every recipe edit is backed up first and can be restored from the editor.
- Diagnostics and the sync log contain counts only, never item text or tokens.

Found a vulnerability? Please report it privately, see the [security policy](.github/SECURITY.md).

## Documentation

| Topic | Where |
| --- | --- |
| Installation and options | [docs/INSTALLATION.md](docs/INSTALLATION.md) |
| Shopping sync behavior and guarantees | [docs/SMART_SHOPPING_SYNC.md](docs/SMART_SHOPPING_SYNC.md) |
| Questions and answers | [docs/FAQ.md](docs/FAQ.md) |
| Changes per version | [CHANGELOG.md](CHANGELOG.md) |
| Dashboard cards and standalone app | [Mealie Atelier Cards](https://github.com/mealie-atelier/mealie-atelier-cards#readme) |
| Live demo | [mealie.alles-automatisch.de](https://mealie.alles-automatisch.de) |
| Guides (German) | [alles-automatisch.de/lernen/mealie-atelier](https://alles-automatisch.de/lernen/mealie-atelier) |

## Development

```sh
uv sync
uv run pytest -q
uv run ruff check && uv run ruff format --check
git config core.hooksPath .githooks
```

Live acceptance tests run against disposable Mealie and Home Assistant containers with fictional data: smoke tests, browser checks of the cards, the shopping sync end to end and the screenshots in this README. See [`tests/live/README.md`](tests/live/README.md). Contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Support the project

Mealie Atelier is free, open source and independent. If it makes your kitchen a little calmer:

- [Buy me a coffee on Ko-fi](https://ko-fi.com/allesautomatisch)
- [Become a member of the Alles Automatisch YouTube channel](https://www.youtube.com/@alles-automatisch/join)
- Star the repository and share your setup in [Show and tell](https://github.com/orgs/mealie-atelier/discussions)

## License

[MIT](LICENSE). Mealie Atelier is an unofficial companion and is not affiliated with Mealie or Home Assistant.

<p align="center"><sub>Made with love by Daniel Boberg · <a href="https://alles-automatisch.de">alles-automatisch.de</a></sub></p>

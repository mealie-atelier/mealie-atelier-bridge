# Installation

Mealie Atelier has three independently usable parts: the standalone web app, the Mealie Atelier Cards for Home Assistant, and this optional Bridge integration. Only the cards and the Bridge need Home Assistant. This guide sets up the Bridge and verifies it with a card.

You can try the cards against the public demo at https://mealie.alles-automatisch.de first.

## Choose a connection

- **Built-in Mealie integration:** Home Assistant's official Mealie integration gives the cards read-only recipe browsing. No Bridge is needed for that.
- **Mealie Atelier Bridge:** This integration adds the full server-side feature set: recipe editing with backups, per-user write grants, the offline shopping queue and Smart Shopping Sync.

## Requirements

- Home Assistant 2026.10 or newer.
- A Mealie server that Home Assistant can reach over the network. Mealie 3.x is recommended (tested with v3.29).
- A Mealie API token (see below).

## 1. Install the Bridge

Choose one route.

**Catalog (Marketplace or HACS).** The Bridge and the cards are submitted to the `hacs/default` catalog, which the Home Assistant Marketplace (Home Assistant 2026.11 and newer, where HACS moved into core) and HACS both read. Once listed, search for **Mealie Atelier Bridge**, download it and continue with the restart.

**Custom repository (until the catalog listing is live).**

- **Marketplace (Home Assistant 2026.11 and newer):** Open the Marketplace and choose **Add from link**. Paste `https://github.com/mealie-atelier/mealie-atelier-bridge`. Adding a repository needs a GitHub connection, so complete the one-time **Connect GitHub** login (a free GitHub account is enough). Then download **Mealie Atelier Bridge**. For the cards, add `https://github.com/mealie-atelier/mealie-atelier-cards` the same way and download it too.
- **Classic HACS:** Open the HACS menu, choose **Custom repositories** and add the Bridge repository with the type **Integration**. Add the cards repository with the type **Dashboard**. Download both.

Manual installation also works: copy the `custom_components/mealie_atelier` folder into the `custom_components` folder of your Home Assistant configuration.

## 2. Restart Home Assistant

Restart Home Assistant so it loads the new integration.

## 3. Add the integration

1. Open **Settings → Devices & services**.
2. Choose **Add integration** and search for **Mealie Atelier Bridge**.
3. Fill in the form:
   - **Mealie server URL:** the direct address Home Assistant can reach, including `http://` or `https://` and the port, for example `http://mealie.example:9925`. For the Mealie Home Assistant app use port 9090. Do not use a `/login` or `/api` path, an ingress URL or credentials in the URL.
   - **Mealie API token:** create it in Mealie under **User profile → API Tokens** and paste it here. The token needs the permissions of the account for the features you plan to use. It is stored in the Home Assistant config entry and never goes into card YAML or browser code.
   - **Verify TLS certificate:** leave it enabled for normal HTTPS certificates. Disable it only on a trusted private network with a self-signed certificate.

Home Assistant checks the connection before it saves. If Mealie later rejects the token, Home Assistant asks you to re-authenticate. To change the address or token, use **Reconfigure** on the integration. Reconfiguring to a token of a different Mealie account is refused, because timers and studio data are stored per entry.

## 4. Review the options

Open the integration and choose **Configure**. The options page is called **Security and interaction**.

| Option | Meaning |
| --- | --- |
| **Read-only mode** | Blocks all changes to recipes, meal plans and shopping lists. Useful for shared tablets, and a good start while you evaluate the Bridge. It wins over every write grant. |
| **Users allowed to change data** | Limits shopping list and meal plan changes to the chosen Home Assistant users. Empty means every user. Administrators can always write, and selecting only administrators limits writing to them. Recipe editing stays admin only. |
| **Structured ingredient editing** | Lets administrators edit ingredients with linked foods and units. A backup is saved before every change. |
| **USDA FoodData Central API key** | Optional. Free key used for nutrition lookup. The field never shows a saved key: leave it empty to keep it. Enable **Remove the saved USDA key** and save to delete it. |
| **AI API key (OpenAI or Anthropic)** | Optional. Powers the AI helpers: food search terms, ingredient substitutes and the recipe check. The provider follows the key: keys starting with `sk-ant-` go to Anthropic, any other key to OpenAI. The field never shows a saved key: leave it empty to keep it. Enable **Remove the saved AI key** and save to turn the AI helpers off. |
| **Timer notification service** | Optional. A notify service such as `notify.mobile_app_your_phone` that receives a push message when a kitchen timer ends. Leave empty for in-app notifications only. |

Make a Mealie backup before you test recipe edits.

## 5. Verify with a card

1. Make sure the Mealie Atelier Cards are installed and registered as a resource. The Marketplace and HACS do this for you on dashboards you manage in the UI. No YAML is needed for that. For a manual install, turn on **Advanced mode** in your user profile, open **Settings → Dashboards → ⋮ → Resources** and add `/local/community/mealie-atelier-cards/mealie-atelier-cards.js` as a **JavaScript module**, then reload the browser page. Only if you manage Lovelace resources in YAML, add the same URL with `type: module` under `lovelace: resources:`. See the [cards README](https://github.com/mealie-atelier/mealie-atelier-cards#yaml-resource).
2. Edit a dashboard, choose **Add card**, switch to the **By card** tab (the cards create no entities, so **By entity** finds nothing) and search for **Mealie Atelier**.
3. In the editor step **Connect kitchen**, pick your connection under **Your Mealie connection** if several are listed. The cards prefer the Bridge by default. You can check or change this later in the card settings under **Data connection** (**Automatic · Atelier first**).
4. Confirm recipe search, recipe detail and cooking steps. With read-only mode off, try a small change such as ticking a shopping item.

If a card shows an error, the message names the cause (for example a rejected token or an unreachable Mealie). Download the integration's diagnostics from **Settings → Devices & services** when you ask for help. They hide credentials and contain counts instead of content.

## Uninstall

1. Remove the Mealie Atelier cards from your dashboards first. A card left behind shows **Configuration error** once its files are gone.
2. Open **Settings → Devices & services → Mealie Atelier Bridge**, then use the menu of the entry and choose **Delete**. This also deletes the recipe backups and the offline outbox stored by the Bridge.
3. Uninstall the Bridge and the cards in the Marketplace or HACS. For a manual install, remove the cards resource under **Settings → Dashboards → ⋮ → Resources** and delete the `custom_components/mealie_atelier` folder and the cards files.
4. Restart Home Assistant.

## Next steps

- Set up the shopping sync: [Smart Shopping Sync](SMART_SHOPPING_SYNC.md).
- Common questions: [FAQ](FAQ.md).
- Guides in German: [Mealie Atelier](https://alles-automatisch.de/lernen/mealie-atelier), [setup](https://alles-automatisch.de/lernen/mealie-atelier-einrichten) and [troubleshooting](https://alles-automatisch.de/lernen/mealie-atelier-fehlerbehebung).

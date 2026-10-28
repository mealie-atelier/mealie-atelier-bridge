# Home Assistant installation

Mealie Atelier has three independently usable parts: the standalone Web app, the Home Assistant Cards package, and this optional Bridge integration. Only the Cards and Bridge require Home Assistant.

## Choose a connection

- **Built-in Mealie integration:** Use Home Assistant's official Mealie integration for read-only recipe browsing. The Atelier Cards guide explains its limitations and setup.
- **Mealie Atelier Bridge:** Add this integration for the full server-side Atelier feature set. Install it from HACS as an Integration or install its `custom_components/mealie_atelier` folder manually, restart Home Assistant, and add **Mealie Atelier Bridge** from **Settings → Devices & services**.

Install **Mealie Atelier Cards** separately from HACS as a Dashboard plugin. No YAML is needed for the usual HACS installation and card setup. If you manage Lovelace resources in YAML, register the built `dist/mealie-atelier-cards.js` file as a JavaScript module.

## Connect Mealie

Enter the direct Mealie base URL that Home Assistant can reach, for example `http://mealie.example:9000`. Do not include `/login`, `/api`, an ingress URL, or credentials in the URL. Create a Mealie API token with the permissions needed for the features you plan to use. The token is stored in the Home Assistant config entry and is never placed into card YAML or browser code.

Use SSL verification for normal HTTPS certificates. Disable it only on a trusted private network when you knowingly use a self-signed certificate.

## Verify the connection

After setup, add a Mealie Atelier card and choose the Bridge provider. Start with read-only mode if you are evaluating the integration. Confirm recipe search, detail, and cooking steps before enabling write actions. Make a Mealie backup before testing recipe edits.

See the [complete Cards guide](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/INSTALLATION_0.9.5.md) for screenshots, native read-only setup, YAML-managed fallback, troubleshooting, and the standalone-app option.

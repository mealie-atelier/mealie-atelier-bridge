# Bridge frequently asked questions

## Is the Bridge required to use Mealie Atelier?

No. It is an optional Home Assistant integration. The standalone Web app works without Home Assistant, and Cards can use Home Assistant's built-in Mealie integration for supported read-only discovery.

## Is the Bridge ready for a stable HACS release?

Not yet. The current source is a developer preview. Local fixtures and Python tests do not verify a live Home Assistant or Mealie server. See the [canonical open-work index](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/OPEN_TODOS.md) and the [Cards release checklist](https://github.com/mealie-atelier/mealie-atelier-cards/blob/main/docs/RELEASE_READINESS_0.9.5.md).

## Where are API credentials stored?

The Mealie token is stored in Home Assistant config-entry data and sent from the Bridge to the configured Mealie server. Never place it in card YAML, public screenshots, logs, or browser code. Review the [security model](../README.md#security-model) before setup.

## Does the Bridge sync Mealie groceries to Bring?

No. Smart shopping sync is planned, not implemented. The [specification](SMART_SHOPPING_SYNC.md) describes safe copy/move semantics, failure handling, retries, duplicate reconciliation, and tests required before release.

## Can I connect a different Home Assistant To-do provider?

The planned sync targets Home Assistant's generic To-do entity contract. Provider support must be tested because integrations may differ in optional fields and failure behavior. Do not assume compatibility until it is listed as tested in the open-work/release records.

## Are recipe changes backed up?

Yes. Before every recipe write (structured ingredients, name, description, servings, steps, cover photo, step photos and restores) the Bridge saves a snapshot in Home Assistant storage: the newest 10 per recipe, for up to 200 recipes. Cover photos are kept as private files under `mealie_atelier/backups/` in the Home Assistant config folder, the newest 3 per recipe. If the backup cannot be written, nothing is changed in Mealie. Removing the integration deletes its backups.

## Does removing a step photo delete the file in Mealie?

No. Mealie v3.29 has no endpoint to delete a recipe asset. Removing or replacing a step photo only removes the reference from the step and the entry from the recipe's asset list. The image file stays on the Mealie disk until you clean it up there.

## Where do I ask for help or report a security issue?

Use [GitHub Discussions](https://github.com/orgs/mealie-atelier/discussions) for setup questions and ideas. Report reproducible bugs through the repository's Issue forms. Use the private reporting process in [SECURITY.md](../.github/SECURITY.md) for vulnerabilities; do not post credentials or private recipe data publicly.

## Corrections log

- 2026-10-28 — Added a Bridge-specific FAQ that clearly separates implemented integration behavior from the planned shopping sync and links to the canonical project status.

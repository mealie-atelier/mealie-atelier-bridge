# Bridge frequently asked questions

## Is the Bridge required to use Mealie Atelier?

No. It is an optional Home Assistant integration. The standalone web app works without Home Assistant, and the cards can use Home Assistant's built-in Mealie integration for read-only discovery. The Bridge adds the server-side features: recipe editing with backups, per-user write grants, the offline shopping queue and Smart Shopping Sync.

## How do I install it?

Through the Home Assistant Marketplace (Home Assistant 2026.11 and newer) or HACS, once the Bridge is listed in the `hacs/default` catalog. Until then add it as a custom repository: in the Marketplace choose **Add from link** (this needs a one-time GitHub login), in classic HACS use **Custom repositories** with the type Integration. The [installation guide](INSTALLATION.md) covers every step.

## Where are API credentials stored?

The Mealie token is stored in Home Assistant config-entry data and sent from the Bridge to the configured Mealie server. Never place it in card YAML, public screenshots, logs or browser code. Review the security model in the [README](../README.md) before setup.

## Who may change data?

Read-only mode blocks every change to recipes, meal plans and shopping lists. With read-only mode off, the option **Users allowed to change data** limits shopping list and meal plan changes to the chosen Home Assistant users. If it is empty, every user may change them. Administrators always can. Recipe editing is admin only. Kitchen timers and journal entries are personal and are blocked only by read-only mode.

## Does the Bridge sync Mealie groceries to a to-do list such as Bring!?

Yes. Smart Shopping Sync copies or moves the items of one Mealie shopping list to a Home Assistant to-do list. Nothing syncs on its own until an administrator has sent and confirmed the first batch. See [Smart Shopping Sync](SMART_SHOPPING_SYNC.md) for the behavior, including failures and conflicts.

## Can I use a different Home Assistant to-do provider?

The sync uses Home Assistant's generic to-do entity contract, so any provider that supports adding items should work. Providers differ in optional fields such as descriptions and in failure behavior, so test your provider with a small list first. Lists of the core Mealie integration cannot be a destination.

## What happens when I am offline in the cards?

The cards queue shopping changes while offline and replay them through the Bridge (`mealie_atelier/offline_replay`). The Bridge records every operation before it writes to Mealie, so a replay after a lost answer does not add the same item twice. If an item was changed elsewhere in the meantime, you are asked to review it instead of overwriting it.

## Are recipe changes backed up?

Yes. Before every recipe write (structured ingredients, name, description, servings, steps, cover photo, step photos and restores) the Bridge saves a snapshot in Home Assistant storage: the newest 10 per recipe, for up to 200 recipes. Cover photos are kept as private files under `mealie_atelier/backups/` in the Home Assistant config folder, the newest 3 per recipe. If the backup cannot be written, nothing is changed in Mealie. Removing the integration deletes its backups.

## Does removing a step photo delete the file in Mealie?

No. Mealie v3.29 has no endpoint to delete a recipe asset. Removing or replacing a step photo only removes the reference from the step and the entry from the recipe's asset list. The image file stays on the Mealie disk until you clean it up there.

## How do I uninstall it?

Remove the Mealie Atelier cards from your dashboards first, because a leftover card shows **Configuration error**. Then delete the integration entry (this deletes the backups and the offline outbox), uninstall the Bridge and the cards via the Marketplace or HACS (for a manual install, remove the resource and the files by hand), and restart Home Assistant.

## Why is a large photo cut off or refused?

Photos are read to the end since 0.10.0, so large photos arrive whole. Uploads accept JPEG, PNG and WebP of at most 10 MB.

## Where do I ask for help or report a security issue?

- Guides in German: [Mealie Atelier](https://alles-automatisch.de/lernen/mealie-atelier), [setup](https://alles-automatisch.de/lernen/mealie-atelier-einrichten) and [troubleshooting](https://alles-automatisch.de/lernen/mealie-atelier-fehlerbehebung).
- [GitHub Discussions](https://github.com/orgs/mealie-atelier/discussions) for setup questions and ideas.
- The repository's bug report template for reproducible bugs.
- The private reporting process in [SECURITY.md](../.github/SECURITY.md) for vulnerabilities. Do not post credentials or private recipe data publicly.

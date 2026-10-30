# WebSocket API of the Mealie Atelier Bridge

The Mealie Atelier Bridge exposes an allowlist of Home Assistant WebSocket commands under the `mealie_atelier/` prefix. The Mealie Atelier cards use them. Every write checks the Home Assistant user's grant and read-only mode on the server. Mealie tokens never leave Home Assistant.

## Commands

Every command below is registered by the Bridge and nothing else is reachable. Access levels: `read` is open to every Home Assistant user. `write` needs the write grant and read-only mode off. `admin` needs a Home Assistant administrator and read-only mode off. `personal` (own timers and journal) is blocked only by read-only mode, grants do not apply. Commands take an optional `entry_id` to pick the entry.

| Command | Purpose | Access |
| --- | --- | --- |
| `mealie_atelier/status` | Loaded entries, permissions, limits and features. | read |
| `mealie_atelier/recipes` | Page and search of recipes. | read |
| `mealie_atelier/recipe` | One recipe by slug. | read |
| `mealie_atelier/recipe_backups` | List the backups kept for a recipe. Each entry carries `ingredient_count` and `step_count` (null for old backups); cards build the localized summary from them. `summary` is an English fallback only. | read |
| `mealie_atelier/recipe_restore` | Restore a recipe from one of its backups. | admin |
| `mealie_atelier/recipe_image_set` | Replace the main recipe photo from an upload. | admin |
| `mealie_atelier/recipe_image_delete` | Remove the main recipe photo. | admin |
| `mealie_atelier/step_image_add` | Attach a photo to a recipe step. | admin |
| `mealie_atelier/step_image_remove` | Remove the photo of a recipe step. | admin |
| `mealie_atelier/upload_begin` | Start a chunked photo upload. | admin |
| `mealie_atelier/upload_chunk` | Send one chunk of a photo upload. | admin |
| `mealie_atelier/ingredients_get` | Read the structured ingredients of a recipe. | read |
| `mealie_atelier/ingredients_save` | Save structured ingredients. | admin |
| `mealie_atelier/foods` | Search the Mealie food catalog. | read |
| `mealie_atelier/units` | Search the Mealie unit catalog. | read |
| `mealie_atelier/food_create` | Create a food. | admin |
| `mealie_atelier/unit_create` | Create a unit. | admin |
| `mealie_atelier/mealplan` | Meal plan entries for a date range. | read |
| `mealie_atelier/shopping` | Shopping lists and their items. | read |
| `mealie_atelier/metadata` | Categories, tags, cookbooks and other lookups. | read |
| `mealie_atelier/mutate` | Recipe and shopping actions. Recipe create, update and import are admin, the shopping actions need write. | write or admin |
| `mealie_atelier/offline_replay` | Replay shopping edits queued while the card was offline. | write |
| `mealie_atelier/nutrition_prepare` | Prepare a nutrition calculation for a recipe. | read |
| `mealie_atelier/nutrition_search` | Search USDA FoodData Central. | read |
| `mealie_atelier/nutrition_ai` | AI estimate for nutrition values. | read |
| `mealie_atelier/nutrition_calculate` | Calculate nutrition values from the ingredients. | read |
| `mealie_atelier/nutrition_save` | Save nutrition values to Mealie. | admin |
| `mealie_atelier/studio_state` | Journal, pantry, family members and session state. | read |
| `mealie_atelier/studio_update` | Change journal, pantry, family members, helper tasks and session state. The journal needs only that read-only mode is off, family members and helper task edits need a Home Assistant admin. | write |
| `mealie_atelier/studio_substitute` | AI ingredient substitution. | read |
| `mealie_atelier/studio_ai_audit` | AI check of a recipe. | read |
| `mealie_atelier/alarm_capabilities` | Whether background timers and push notifications are available. | read |
| `mealie_atelier/alarm_create` | Start a background kitchen timer. | personal |
| `mealie_atelier/alarm_cancel` | Cancel a background kitchen timer. | personal |
| `mealie_atelier/alarms` | List the active timers of the user, all timers for an admin. | read |
| `mealie_atelier/sync_state` | Rules, counts and recent runs of Smart Shopping Sync. | read |
| `mealie_atelier/sync_destinations` | To-do lists a rule can send to. | read |
| `mealie_atelier/sync_preview` | Show the first batch of a rule or draft without sending. | read |
| `mealie_atelier/sync_records` | List the items of a rule by state. | read |
| `mealie_atelier/sync_rule_save` | Create or change a rule. | admin |
| `mealie_atelier/sync_rule_delete` | Delete a rule. | admin |
| `mealie_atelier/sync_run` | Run a rule now. Confirming the first batch needs admin. | write or admin |
| `mealie_atelier/sync_pause` | Pause a rule. | write |
| `mealie_atelier/sync_resume` | Resume a rule. | write |
| `mealie_atelier/sync_retry` | Retry failed items. | write |
| `mealie_atelier/sync_resolve` | Dismiss or send again an item under review. | write |

## Status

`mealie_atelier/status` lists every loaded entry. Besides the permissions and limits, each entry carries:

| Field | Meaning |
| --- | --- |
| `read_only` | The entry is in read-only mode. Write actions fail with `read_only`. |
| `features.ai` | Same as `ai_enabled`, kept for older cards. |
| `ai_enabled` | `true` when an AI API key is configured. |
| `ai_provider` | `openai`, `anthropic` or `none`. Keys starting with `sk-ant-` use `anthropic`, any other key uses `openai`. |

## AI language

`mealie_atelier/studio_substitute` and `mealie_atelier/studio_ai_audit` accept an optional `language` (`de` or `en`). The AI prompt and the disclaimer follow it: German answers address the reader informally with "du". Without `language` the Home Assistant language is used. Any language other than German is answered in English.

## Error codes

A failed command returns an error whose `code` is stable and whose message is English. Cards choose their message by code, never by message text. Many codes also carry a `translation_key` equal to the code, with a text in `strings.json`, but not every path attaches one: `ai_error`, `conflict`, `invalid_request`, `mealie_auth`, `mealie_error`, `not_configured` and `unauthorized` are sent both with and without it. A card must therefore always map an error by its `code`, never by `translation_key` or message text.

| Code | When |
| --- | --- |
| `timer_not_found` | No timer with that id. Cancelling it counts as success. |
| `timer_not_permitted` | The timer belongs to another user. |
| `timer_exists` | A timer with that id already exists. |
| `too_many_timers` | The user reached the limit of 20 active timers, or the Bridge reached its global limit of 300 active timers. |
| `invalid_timer` | The timer id, label or deadline is invalid. |
| `usda_key_invalid` | The USDA FoodData Central key was refused. |
| `usda_rate_limited` | The USDA FoodData Central limit has been reached. |
| `ai_error` | The AI provider failed or returned something unusable. |
| `ai_not_configured` | An AI call was made without an API key in the options. |
| `invalid_request` | Any other invalid input. |
| `sync_source_gone`, `sync_source_checked` | Send again was refused because the item no longer exists in Mealie or is ticked off there. The item stays under review. |
| `mealie_auth`, `mealie_error` | Mealie refused or failed the request. |
| `entry_reauth` | The addressed entry exists but Mealie rejected its token. The user must re-authenticate in Settings, Devices & services. Without `entry_id` this is reported when exactly one entry exists. |
| `entry_unavailable` | The addressed entry exists but cannot reach Mealie right now and Home Assistant is retrying. Without `entry_id` this is reported when exactly one entry exists. |
| `not_configured` | No Mealie Atelier Bridge entry exists, or the addressed entry is unknown or not loaded for another reason. |

The other codes (`read_only`, `write_not_permitted`, `backup_failed`, `upload_*`, `sync_*`, `offline_*` and so on) are listed in `custom_components/mealie_atelier/strings.json` under `exceptions`.

## Shopping list edits

`mealie_atelier/mutate` accepts these shopping actions besides `shopping_add` and `shopping_toggle`. They need the same write permission and fail with `read_only` in read-only mode.

| Action | Payload | Result |
| --- | --- | --- |
| `shopping_add` | `list_id`, `note` | A free text item. It has no quantity, so Mealie shows the text without a leading "1". |
| `shopping_update` | `item_id` and at least one of `name`, `note`, `quantity`. `name` and `note` are never combined. | The updated item. A rename turns an item linked to a food into a free text item. An empty `note` removes the note. Every edit sends an empty `display`, so Mealie rebuilds it from quantity, unit, name and note, as the web app server does. `quantity` is a number from 0 to 100000. |
| `shopping_delete` | `item_id` | `{ok, already_applied}`. An item that is already gone counts as deleted. |
| `shopping_clear_checked` | `list_id`, optional `expected_ids` | Deletes the ticked items of that list. With `expected_ids` (optional online, 1 to 1000 IDs) only the ticked items whose ID is in it go and a ticked item the user never saw stays. Without it every ticked item of the list is deleted. An offline replay always needs `expected_ids`. |

`mealie_atelier/offline_replay` takes the same actions inside `operation.payload` with these extra fields, so a change made on another device meanwhile is reported instead of overwritten:

- `shopping_toggle` needs `expected_checked`.
- `shopping_update` may carry `expected_name`, `expected_note` and `expected_quantity` for each changed field. Without one, that edit wins.
  - `expected_name` is the text the list showed: the display text, the label rebuilt from quantity, unit, name and note, or the name and note without quantity and unit. Chained offline edits and a quantity edit on another device do not conflict, while a changed name or note does. The note alone of an item linked to a food never matches.
  - A rename should also send `expected_quantity`, the quantity the user saw (a number from 0 to 100000). Only a leading number equal to it counts as the amount; any other leading number, or every number of an item shown with quantity 0, belongs to the name. Without it the current quantity of the item is used, so a rename of "10 Eier" can match an item another device renamed to "Eier" with quantity 10.
- `shopping_clear_checked` needs `expected_ids`, the IDs of the ticked items the user saw (1 to 1000). It becomes a conflict when another device ticked or unticked an item meanwhile. Items the user saw ticked that are gone are fine.

Codes: `offline_conflict` (the record stays a conflict until the card discards it), `shopping_item_not_found` (an update targets an item that no longer exists; a replay reports it again) and `invalid_request` for malformed payloads. The web app server answers the same payloads and codes on `/api/bridge`.

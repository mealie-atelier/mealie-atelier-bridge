# WebSocket API of the Mealie Atelier Bridge

The Mealie Atelier Bridge exposes an allowlist of Home Assistant WebSocket commands under the `mealie_atelier/` prefix. The Mealie Atelier cards use them. Every write checks the Home Assistant user's grant and read-only mode on the server. Mealie tokens never leave Home Assistant.

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
| `mealie_auth`, `mealie_error` | Mealie refused or failed the request. |
| `not_configured` | No loaded Mealie Atelier Bridge entry. |

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

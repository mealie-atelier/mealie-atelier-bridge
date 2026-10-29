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

A failed command returns an error whose `code` is stable and whose message is English. Cards choose their message by code, never by message text. Codes raised as Home Assistant exceptions (the timer, USDA, upload and similar codes) also carry a `translation_key` equal to the code, with a text in `strings.json`. The fallback codes `ai_error`, `conflict`, `invalid_request`, `mealie_auth`, `mealie_error`, `not_configured` and `unauthorized` carry no `translation_key`: a card must map them by code.

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

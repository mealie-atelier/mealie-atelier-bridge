# Contributing

## Source language and style

- Write developer-facing source, identifiers, comments, logs, and repository documentation in English.
- Keep Home Assistant user-facing setup strings in `custom_components/mealie_atelier/translations/` and maintain German/English parity.
- Keep the integration optional; do not make the standalone Web app or the Cards package require Home Assistant.
- Preserve server-side authorization, explicit allowlists, safe URL handling, and fail-closed behavior for writes.
- Use Ruff formatting and linting. Add tests for data handling, permissions, retries, translations, and compatibility contracts.

## Project layout

- `custom_components/mealie_atelier/`: integration, API client, config flow, translations, and WebSocket commands.
- `tests/`: local unit and contract tests.
- `docs/`: feature specifications, release gates, and dated corrections.

## Manual verification

```sh
pytest -q
ruff check custom_components tests
ruff format --check custom_components tests
python -m compileall -q custom_components/mealie_atelier
```

GitHub Actions are disabled across the organization; use no workflows or automated release checks. Run and review these commands manually before a release. Do not describe local mock tests as live Home Assistant or Mealie acceptance. Follow `docs/SMART_SHOPPING_SYNC.md` before implementing or advertising shopping-list synchronization.

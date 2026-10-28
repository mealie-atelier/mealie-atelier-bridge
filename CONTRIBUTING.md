# Contributing

## Source language and style

- Write developer-facing source, identifiers, comments, logs, and repository documentation in English.
- Keep Home Assistant user-facing setup strings in `custom_components/mealie_atelier/translations/` and maintain German/English parity.
- Keep the integration optional; do not make the standalone Web app or the Cards package require Home Assistant.
- Preserve server-side authorization, explicit allowlists, safe URL handling, and fail-closed behavior for writes.
- Use Ruff formatting and linting. Add tests for data handling, permissions, retries, translations, and compatibility contracts.

## Project layout

- `custom_components/mealie_atelier/`: integration, API client, config flow, translations, and WebSocket commands.
- `tests/`: local unit and contract tests. `tests/live/` holds the disposable Docker rig (Mealie and Home Assistant) for live acceptance.
- `docs/`: installation guide, FAQ and feature specifications.
- `CHANGELOG.md`: user-facing changes. Add an entry with every change that users can notice.

## Manual verification

Install [uv](https://docs.astral.sh/uv/) and Python 3.14.2 or newer (the minimum for Home Assistant 2026.10), then:

```sh
uv sync
git config core.hooksPath .githooks   # runs the checks below before every commit
uv run ruff check
uv run ruff format --check
uv run pytest -q
```

The only GitHub workflow is `.github/workflows/validate.yml`, which runs the HACS action and hassfest for the catalog. Tests and linting run locally: the `.githooks/pre-commit` hook runs the three commands above before every commit, so run them before you open a pull request. Do not describe local mock tests as live Home Assistant or Mealie acceptance; the rig in `tests/live/` is the live check. Follow `docs/SMART_SHOPPING_SYNC.md` when changing shopping-list synchronization.

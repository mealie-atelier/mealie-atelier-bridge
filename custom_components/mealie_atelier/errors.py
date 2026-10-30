"""Coded errors shared by the editing, backup and upload modules."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


class AtelierError(HomeAssistantError):
    """An error with a stable WebSocket code, an English message and optional placeholders."""

    def __init__(self, code: str, message: str, placeholders: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.placeholders = placeholders


def send_atelier_error(connection, msg: dict, exc: AtelierError) -> None:
    """Send the code with its translation key; the message never carries household content."""
    connection.send_error(
        msg["id"],
        exc.code,
        exc.message[:220],
        translation_key=exc.code,
        translation_domain=DOMAIN,
        translation_placeholders=exc.placeholders,
    )


def structured_editor_required() -> AtelierError:
    """Plain-text ingredient replacement is gone; the structured editor replaces it."""
    return AtelierError(
        "use_structured_editor",
        "Ingredients are edited with the structured ingredient editor, not as plain text",
    )


def missing_entry_code(hass: HomeAssistant, entry_id: str | None) -> str:
    """Why no loaded entry answers: not set up at all, token rejected or Mealie unreachable."""
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
    else:
        # A card not bound to an entry means the only one, when there is exactly one.
        entries = hass.config_entries.async_entries(DOMAIN)
        entry = entries[0] if len(entries) == 1 else None
    if entry is None or entry.domain != DOMAIN:
        return "not_configured"
    if entry.state is ConfigEntryState.SETUP_ERROR and list(
        entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    ):
        return "entry_reauth"
    if entry.state in (ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY):
        return "entry_unavailable"
    return "not_configured"


def send_missing_entry(hass: HomeAssistant, connection, msg: dict, message: str) -> None:
    """Answer a command that addressed an entry which is not loaded."""
    code = missing_entry_code(hass, msg.get("entry_id"))
    connection.send_error(
        msg["id"],
        code,
        EXTRA_MESSAGES.get(code, message),
        translation_key=code,
        translation_domain=DOMAIN,
    )


EXTRA_MESSAGES = {
    "entry_reauth": "The Mealie token was rejected. Re-authenticate in Settings, Devices & services",
    "entry_unavailable": "Mealie Atelier Bridge cannot reach Mealie right now",
}

"""Coded errors shared by the editing, backup and upload modules."""

from __future__ import annotations

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

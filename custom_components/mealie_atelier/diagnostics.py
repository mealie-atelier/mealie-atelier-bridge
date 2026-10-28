"""Diagnostics for Mealie Atelier: configuration shape and counts, never content or secrets."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_AI_KEY, CONF_API_TOKEN, CONF_FDC_KEY, CONF_READ_ONLY, DOMAIN
from .models import MealieAtelierConfigEntry

TO_REDACT = {CONF_API_TOKEN, CONF_AI_KEY, CONF_FDC_KEY, CONF_URL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MealieAtelierConfigEntry
) -> dict[str, Any]:
    """Return redacted entry data and counts that help debugging without exposing content."""
    integration = await async_get_integration(hass, DOMAIN)
    data: dict[str, Any] = {
        "integration_version": str(integration.version),
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
            "read_only": bool(entry.options.get(CONF_READ_ONLY, False)),
        },
    }
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        state = runtime.studio.state
        family = state.get("family") or {}
        data["counts"] = {
            "alarms": len(runtime.alarms.alarms),
            "pantry_items": len(state.get("pantry") or []),
            "family_members": len(family.get("members") or []),
            "studio_revision": state.get("revision", 0),
        }
    return data

"""Typed runtime data attached to each Mealie Atelier config entry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .alarms import TimerManager
from .api import MealieClient
from .const import DOMAIN
from .recipe_backups import RecipeBackupStore
from .uploads import UploadSessions


@dataclass
class StudioContext:
    """Persistent kitchen studio state; writers must hold the lock."""

    store: Store
    state: dict[str, Any]
    lock: asyncio.Lock


@dataclass
class MealieAtelierData:
    """Everything one loaded entry owns."""

    client: MealieClient
    studio: StudioContext
    alarms: TimerManager
    backups: RecipeBackupStore
    uploads: UploadSessions


type MealieAtelierConfigEntry = ConfigEntry[MealieAtelierData]


def get_loaded_entry(
    hass: HomeAssistant, entry_id: str | None = None
) -> MealieAtelierConfigEntry | None:
    """Return a loaded entry by id, or the first loaded entry when no id is given."""
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            return None
        return entry
    return next(iter(hass.config_entries.async_loaded_entries(DOMAIN)), None)

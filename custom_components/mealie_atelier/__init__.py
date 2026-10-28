"""Mealie Atelier: secure API bridge for polished HA cards and a kitchen UI."""

from __future__ import annotations

import asyncio

from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .alarms import TimerManager
from .api import MealieAuthError, MealieClient, MealieError
from .const import CONF_API_TOKEN, CONF_TIMER_NOTIFY_SERVICE, CONF_VERIFY_SSL, DOMAIN
from .http import MealieAtelierImageView, MealieAtelierRecipeAssetView
from .models import MealieAtelierConfigEntry, MealieAtelierData, StudioContext
from .recipe_backups import RecipeBackupStore
from .recipe_editing import async_register_commands as async_register_editing_commands
from .studio import initial as studio_initial
from .uploads import UploadSessions
from .websocket import async_register_commands

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register frontend bridge once; no YAML configuration needed."""
    async_register_commands(hass)
    async_register_editing_commands(hass)
    hass.http.register_view(MealieAtelierImageView())
    hass.http.register_view(MealieAtelierRecipeAssetView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> bool:
    """Attach an authenticated client for this Mealie account."""
    client = MealieClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_API_TOKEN],
        entry.data.get(CONF_VERIFY_SSL, True),
    )
    try:
        await client.check_connection()
    except MealieAuthError as exc:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from exc
    except MealieError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(exc)},
        ) from exc
    store = Store(hass, 1, f"mealie_atelier_studio_{entry.entry_id}")
    state = await store.async_load() or studio_initial()
    manager = TimerManager(hass, entry.entry_id, entry.options.get(CONF_TIMER_NOTIFY_SERVICE, ""))
    await manager.async_start()
    entry.runtime_data = MealieAtelierData(
        client=client,
        studio=StudioContext(store=store, state=state, lock=asyncio.Lock()),
        alarms=manager,
        backups=await RecipeBackupStore.async_load(hass, entry.entry_id),
        uploads=UploadSessions(),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> bool:
    """Stop the timer scheduler on unload; do not remove core Mealie."""
    await entry.runtime_data.alarms.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> None:
    """Delete the recipe backups (store and photo files) together with the entry."""
    store = await RecipeBackupStore.async_load(hass, entry.entry_id)
    await store.async_remove()

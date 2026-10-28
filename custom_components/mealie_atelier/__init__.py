"""Mealie Atelier: secure API bridge for polished HA cards and a kitchen UI."""

from __future__ import annotations

import asyncio
import logging

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
from .offline_ledger import OfflineLedger
from .offline_ledger import async_register_commands as async_register_offline_commands
from .models import MealieAtelierConfigEntry, MealieAtelierData, StudioContext
from .recipe_backups import RecipeBackupStore
from .recipe_editing import async_register_commands as async_register_editing_commands
from .studio import initial as studio_initial
from .uploads import UploadSessions
from .shopping_sync import SyncManager
from .shopping_sync.store import SyncStore
from .shopping_sync import async_register_commands as async_register_sync_commands
from .websocket import async_register_commands

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register frontend bridge once; no YAML configuration needed."""
    async_register_commands(hass)
    async_register_editing_commands(hass)
    async_register_sync_commands(hass)
    async_register_offline_commands(hass)
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
    # Load everything that can fail before starting background work, so a failed setup
    # leaves no timer or sync task running.
    backups = await RecipeBackupStore.async_load(hass, entry.entry_id)
    offline = await OfflineLedger.async_load(hass, entry.entry_id, client)
    store = Store(hass, 1, f"mealie_atelier_studio_{entry.entry_id}")
    state = await store.async_load() or studio_initial()
    manager = TimerManager(hass, entry.entry_id, entry.options.get(CONF_TIMER_NOTIFY_SERVICE, ""))
    await manager.async_start()
    sync = SyncManager(hass, entry.entry_id, client)
    try:
        await sync.async_start()
    except Exception:
        # Shopping sync is optional: a sync problem never blocks the rest of the Bridge.
        _LOGGER.exception("Shopping sync could not start")
    entry.runtime_data = MealieAtelierData(
        client=client,
        studio=StudioContext(store=store, state=state, lock=asyncio.Lock()),
        alarms=manager,
        backups=backups,
        uploads=UploadSessions(),
        sync=sync,
        offline=offline,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> bool:
    """Stop the timer scheduler and shopping sync on unload; do not remove core Mealie."""
    await entry.runtime_data.alarms.async_stop()
    await entry.runtime_data.sync.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> None:
    """Delete recipe backups (store and photo files) and the sync outbox with the entry."""
    store = await RecipeBackupStore.async_load(hass, entry.entry_id)
    await store.async_remove()
    # The outbox holds shopping item titles; nothing of a removed entry may stay behind.
    await SyncStore(hass, entry.entry_id).async_remove()
    # The ledger keeps shopping notes and item ids of queued edits.
    await Store(hass, 1, f"mealie_atelier_offline_{entry.entry_id}").async_remove()

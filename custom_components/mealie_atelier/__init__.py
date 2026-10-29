"""Mealie Atelier: secure API bridge for polished HA cards and a kitchen UI."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .alarms import TimerManager
from .api import MealieAuthError, MealieClient, MealieError
from .config_flow import entry_title
from .const import (
    CONF_API_TOKEN,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    CONF_WRITE_USERS,
    DOMAIN,
)
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


def _failure_detail(exc: MealieError) -> str:
    """Language-neutral reason for the translated setup-retry message."""
    if exc.status:
        return f"HTTP {exc.status}"
    return type(exc.__cause__ or exc).__name__


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version 2 keys the unique id on the Mealie account alone, not on "<url>:<account>"."""
    if entry.version < 2:
        account_id = str(entry.unique_id).rsplit(":", 1)[-1]
        taken = any(
            other.unique_id == account_id
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id
        )
        if taken:
            # Two entries already point at the same account (the old URL-scoped ids allowed
            # that). Keep the legacy id on the second one instead of colliding.
            _LOGGER.warning("Another entry already uses this Mealie account: %s", entry.title)
            hass.config_entries.async_update_entry(entry, version=2)
        else:
            hass.config_entries.async_update_entry(entry, unique_id=account_id, version=2)
        await _retitle_legacy_entry(hass, entry)
        await _drop_admin_write_grants(hass, entry)
    return True


async def _retitle_legacy_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename a generated "Mealie Atelier · user" title; a title the user chose stays."""
    prefix = "Mealie Atelier · "
    username = entry.title.removeprefix(prefix)
    if entry.title.startswith(prefix) and username and "@" not in username:
        hass.config_entries.async_update_entry(
            entry, title=entry_title(entry.data[CONF_URL], username)
        )


async def _drop_admin_write_grants(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Administrators always may write, so a stored admin id only narrows the list for others."""
    grants = entry.options.get(CONF_WRITE_USERS)
    if not grants:
        return
    kept = []
    for user_id in grants:
        user = await hass.auth.async_get_user(user_id)
        if user is None or not user.is_admin:
            kept.append(user_id)
    if kept != list(grants):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_WRITE_USERS: kept}
        )


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
            translation_placeholders={"error": _failure_detail(exc)},
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

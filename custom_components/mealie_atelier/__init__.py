"""Mealie Atelier: secure API bridge for polished HA cards and a kitchen UI."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .alarms import TimerManager
from .api import MealieAuthError, MealieClient, MealieError
from .config_flow import entry_title, legacy_entry_title
from .const import (
    ALARMS_STORE_KEY,
    CONF_API_TOKEN,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    DOMAIN,
    STUDIO_STORE_KEY,
)
from .errors import request_reauth
from .http import MealieAtelierImageView, MealieAtelierRecipeAssetView
from .offline_ledger import OfflineLedger
from .permissions import is_read_only
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

# How often an idle Bridge checks that Mealie still accepts its token.
AUTH_CHECK_INTERVAL = timedelta(minutes=15)

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


_RETITLE_PENDING = "retitle_legacy_title"


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
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, _RETITLE_PENDING: True}, version=2
            )
        else:
            hass.config_entries.async_update_entry(
                entry,
                unique_id=account_id,
                data={**entry.data, _RETITLE_PENDING: True},
                version=2,
            )
    return True


def _retitle_legacy_entry(hass: HomeAssistant, entry: ConfigEntry, username: str | None) -> None:
    """Rename the title the legacy config flow generated, once, for an entry migrated from v1.

    Only "Mealie Atelier · <username>" is replaced, or "Mealie Atelier · Kitchen" when Mealie
    returned no username (the legacy fallback). Any other title, including a custom one of the
    same shape, stays. The marker is removed afterwards, so later renames are never reverted.
    """
    username_now = username or "Kitchen"
    if entry.title == legacy_entry_title(entry.data[CONF_URL], username_now):
        # The generated title of earlier versions repeated the product name and was cut off on
        # a phone. Only the exact generated form is replaced, a renamed entry stays.
        hass.config_entries.async_update_entry(
            entry, title=entry_title(entry.data[CONF_URL], username_now)
        )
    if not entry.data.get(_RETITLE_PENDING):
        return
    username = username or "Kitchen"
    legacy = f"Mealie Atelier · {username}"
    data = {key: value for key, value in entry.data.items() if key != _RETITLE_PENDING}
    title = entry_title(entry.data[CONF_URL], username) if entry.title == legacy else entry.title
    hass.config_entries.async_update_entry(entry, title=title, data=data)


async def async_setup_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> bool:
    """Attach an authenticated client for this Mealie account."""
    client = MealieClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_API_TOKEN],
        entry.data.get(CONF_VERIFY_SSL, True),
    )
    try:
        account = await client.check_connection()
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
    # Any caller of the client (cards, shopping sync, ledger) that hits a revoked token starts
    # the reauth flow, not only the websocket commands.
    client.on_auth_rejected = lambda exc: request_reauth(hass, entry.entry_id, exc)

    async def _check_token(_now) -> None:
        try:
            await client.check_connection()
        except MealieError:
            # Auth errors start reauth through the client hook; outages are not our concern here.
            pass

    # Without any traffic a revoked token would go unnoticed until someone opens a card.
    stop_auth_check = async_track_time_interval(hass, _check_token, AUTH_CHECK_INTERVAL)
    _retitle_legacy_entry(hass, entry, account.get("username"))
    # Load everything that can fail before starting background work, so a failed setup
    # leaves no timer or sync task running.
    backups = await RecipeBackupStore.async_load(hass, entry.entry_id)
    offline = await OfflineLedger.async_load(hass, entry.entry_id, client)
    store = Store(hass, 1, STUDIO_STORE_KEY.format(entry.entry_id))
    state = await store.async_load() or studio_initial()
    manager = TimerManager(hass, entry.entry_id, entry.options.get(CONF_TIMER_NOTIFY_SERVICE, ""))
    await manager.async_start()
    sync = SyncManager(hass, entry.entry_id, client, read_only=lambda: is_read_only(entry))
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
        stop_auth_check=stop_auth_check,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> bool:
    """Stop the timer scheduler and shopping sync on unload; do not remove core Mealie."""
    if entry.runtime_data.stop_auth_check is not None:
        entry.runtime_data.stop_auth_check()
    await entry.runtime_data.alarms.async_stop()
    await entry.runtime_data.sync.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: MealieAtelierConfigEntry) -> None:
    """Delete everything stored for the entry: backups, sync outbox, ledger, studio and timers."""
    store = await RecipeBackupStore.async_load(hass, entry.entry_id)
    await store.async_remove()
    # The outbox holds shopping item titles; nothing of a removed entry may stay behind.
    await SyncStore(hass, entry.entry_id).async_remove()
    # The ledger keeps shopping notes and item ids of queued edits.
    await Store(hass, 1, f"mealie_atelier_offline_{entry.entry_id}").async_remove()
    # The studio store holds family names, pantry and journal, the timer store the timers.
    await Store(hass, 1, STUDIO_STORE_KEY.format(entry.entry_id)).async_remove()
    await Store(hass, 1, ALARMS_STORE_KEY.format(entry.entry_id)).async_remove()

"""Mealie Atelier: secure API bridge for polished HA cards and a kitchen UI."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .alarms import TimerManager
from .api import MealieAuthError, MealieClient, MealieError
from .const import CONF_API_TOKEN, CONF_TIMER_NOTIFY_SERVICE, CONF_VERIFY_SSL, DOMAIN
from .http import MealieAtelierImageView, MealieAtelierRecipeAssetView
from .studio import initial as studio_initial
from .websocket import async_register_commands


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register frontend bridge once; no YAML configuration needed."""
    hass.data.setdefault(DOMAIN, {})
    async_register_commands(hass)
    hass.http.register_view(MealieAtelierImageView())
    hass.http.register_view(MealieAtelierRecipeAssetView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except MealieError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc
    hass.data[DOMAIN][entry.entry_id] = client
    store = Store(hass, 1, f"mealie_atelier_studio_{entry.entry_id}")
    state = await store.async_load() or studio_initial()
    hass.data.setdefault(DOMAIN + "_studio", {})[entry.entry_id] = {
        "store": store,
        "state": state,
        "lock": asyncio.Lock(),
    }
    manager = TimerManager(hass, entry.entry_id, entry.options.get(CONF_TIMER_NOTIFY_SERVICE, ""))
    await manager.async_start()
    hass.data.setdefault(DOMAIN + "_alarms", {})[entry.entry_id] = manager
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove client on unload; do not remove core Mealie."""
    manager = hass.data.get(DOMAIN + "_alarms", {}).pop(entry.entry_id, None)
    if manager:
        await manager.async_stop()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    hass.data.get(DOMAIN + "_studio", {}).pop(entry.entry_id, None)
    return True

"""Config flow for Mealie Atelier. This is not a replacement for core Mealie."""

from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MealieAuthError, MealieClient, MealieError
from .const import (
    CONF_AI_KEY,
    CONF_API_TOKEN,
    CONF_EXPERIMENTAL_INGREDIENTS,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    DOMAIN,
)


class MealieAtelierConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a secure, server-side connection to Mealie."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Offer a safe, UI-configurable read-only switch for shared tablets."""
        return MealieAtelierOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = MealieClient(
                    async_get_clientsession(self.hass),
                    user_input[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    user_input[CONF_VERIFY_SSL],
                )
                account = await client.check_connection()
                await self.async_set_unique_id(f"{client.url.casefold()}:{account['id']}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Mealie Atelier · {account.get('username', 'Kitchen')}",
                    data={**user_input, CONF_URL: client.url},
                )
            except MealieAuthError:
                errors["base"] = "invalid_auth"
            except (MealieError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            except (ValueError, TypeError):
                errors["base"] = "invalid_url"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL): str,
                    vol.Required(CONF_API_TOKEN): str,
                    vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                }
            ),
            errors=errors,
        )


class MealieAtelierOptionsFlow(config_entries.OptionsFlowWithReload):
    """Change dashboard write permissions without editing YAML."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_READ_ONLY, default=False): bool,
                vol.Required(CONF_EXPERIMENTAL_INGREDIENTS, default=False): bool,
                vol.Optional(CONF_FDC_KEY, default=""): str,
                vol.Optional(CONF_AI_KEY, default=""): str,
                vol.Optional(CONF_TIMER_NOTIFY_SERVICE, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
        )

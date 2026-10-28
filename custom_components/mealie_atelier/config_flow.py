"""Config flow for Mealie Atelier. This is not a replacement for core Mealie."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import MealieAuthError, MealieClient, MealieError
from .const import (
    CONF_AI_KEY,
    CONF_API_TOKEN,
    CONF_EXPERIMENTAL_INGREDIENTS,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    CONF_WRITE_USERS,
    DOMAIN,
)


async def _validate(
    hass: HomeAssistant, url: str, token: str, verify_ssl: bool
) -> tuple[MealieClient, dict[str, Any]]:
    """Connect to Mealie and return the normalized client plus the account it belongs to."""
    client = MealieClient(async_get_clientsession(hass), url.strip(), token.strip(), verify_ssl)
    return client, await client.check_connection()


def _error_key(error: Exception) -> str:
    """Map a validation failure to a translated error key."""
    if isinstance(error, MealieAuthError):
        return "invalid_auth"
    if isinstance(error, (MealieError, aiohttp.ClientError)):
        return "cannot_connect"
    if isinstance(error, (ValueError, TypeError)):
        return "invalid_url"
    return "unknown"


def _unique_id(client: MealieClient, account: Mapping[str, Any]) -> str:
    return f"{client.url.casefold()}:{account['id']}"


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
                client, account = await _validate(
                    self.hass,
                    user_input[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    user_input[CONF_VERIFY_SSL],
                )
            except Exception as error:  # noqa: BLE001
                errors["base"] = _error_key(error)
            else:
                await self.async_set_unique_id(_unique_id(client, account))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Mealie Atelier · {account.get('username', 'Kitchen')}",
                    data={
                        CONF_URL: client.url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): str,
                        vol.Required(CONF_API_TOKEN): str,
                        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start re-authentication when Mealie rejects the stored token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask only for a new token and keep URL and account unchanged."""
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                client, account = await _validate(
                    self.hass,
                    entry.data[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    entry.data.get(CONF_VERIFY_SSL, True),
                )
            except Exception as error:  # noqa: BLE001
                errors["base"] = _error_key(error)
            else:
                if _unique_id(client, account) != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip()},
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): str}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Change the server URL, token or TLS check of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                client, account = await _validate(
                    self.hass,
                    user_input[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    user_input[CONF_VERIFY_SSL],
                )
            except Exception as error:  # noqa: BLE001
                errors["base"] = _error_key(error)
            else:
                # The unique_id is "<url>:<account id>". A new URL is fine, a different Mealie
                # account is not: studio data and timers are stored per entry and would leak.
                if str(account["id"]) != str(entry.unique_id).rsplit(":", 1)[-1]:
                    return self.async_abort(reason="wrong_account")
                unique_id = _unique_id(client, account)
                if unique_id != entry.unique_id and any(
                    other.unique_id == unique_id for other in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data_updates={
                        CONF_URL: client.url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip(),
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                    reason="reconfigure_successful",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_URL): str,
                        vol.Required(CONF_API_TOKEN): str,
                        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                    }
                ),
                user_input or entry.data,
            ),
            errors=errors,
        )


class MealieAtelierOptionsFlow(config_entries.OptionsFlowWithReload):
    """Change dashboard write permissions without editing YAML."""

    async def async_step_init(self, user_input=None):
        errors = {}
        # Home Assistant has no user selector. Offer active, non-system users and forget stored
        # ids of users that were deleted or deactivated, so the form never submits a stale one.
        users = sorted(
            (
                user
                for user in await self.hass.auth.async_get_users()
                if not user.system_generated and user.is_active
            ),
            key=lambda user: (user.name or user.id).casefold(),
        )
        known = {user.id for user in users}
        if user_input is not None:
            service = user_input.get(CONF_TIMER_NOTIFY_SERVICE, "").strip()
            if service and not (
                service.startswith("notify.") and service[7:].replace("_", "").isalnum()
            ):
                errors[CONF_TIMER_NOTIFY_SERVICE] = "invalid_notify_service"
            else:
                return self.async_create_entry(
                    # The selector only accepts ids from its options, so no stale id reaches here.
                    data={
                        **user_input,
                        CONF_TIMER_NOTIFY_SERVICE: service,
                        CONF_WRITE_USERS: user_input.get(CONF_WRITE_USERS, []),
                    }
                )
        # Offer the notify services that exist right now, but still allow typing one that
        # is not registered yet (for example a phone that is currently offline).
        services = self.hass.services.async_services_for_domain("notify")
        notify_options = sorted(f"notify.{name}" for name in services if name != "send_message")
        schema = vol.Schema(
            {
                vol.Required(CONF_READ_ONLY, default=False): bool,
                vol.Optional(CONF_WRITE_USERS, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=user.id, label=user.name or user.id)
                            for user in users
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_EXPERIMENTAL_INGREDIENTS, default=False): bool,
                vol.Optional(CONF_FDC_KEY, default=""): str,
                vol.Optional(CONF_AI_KEY, default=""): str,
                vol.Optional(CONF_TIMER_NOTIFY_SERVICE, default=""): SelectSelector(
                    SelectSelectorConfig(
                        options=notify_options,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        suggested = dict(user_input or self.config_entry.options)
        suggested[CONF_WRITE_USERS] = [
            user_id for user_id in suggested.get(CONF_WRITE_USERS) or [] if user_id in known
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

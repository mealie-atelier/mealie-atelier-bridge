"""Config flow for Mealie Atelier. This is not a replacement for core Mealie."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MealieAuthError, MealieClient, MealieError
from .const import (
    ANTHROPIC_KEYS_URL,
    CONF_AI_KEY,
    CONF_API_TOKEN,
    CONF_EXPERIMENTAL_INGREDIENTS,
    CONF_FDC_KEY,
    CONF_READ_ONLY,
    CONF_TIMER_NOTIFY_SERVICE,
    CONF_VERIFY_SSL,
    CONF_WRITE_USERS,
    DOMAIN,
    OPENAI_KEYS_URL,
    USDA_SIGNUP_URL,
)


async def _validate(
    hass: HomeAssistant, url: str, token: str, verify_ssl: bool
) -> tuple[MealieClient, dict[str, Any]]:
    """Connect to Mealie and return the normalized client plus the account it belongs to."""
    client = MealieClient(async_get_clientsession(hass), _clean_url(url), token.strip(), verify_ssl)
    return client, await client.check_connection()


# Path segments a browser address carries but the API base address must not.
_NON_BASE_SEGMENTS = frozenset({"login", "api", "g"})


def _clean_url(url: str) -> str:
    """Cut a pasted browser address (/login, /g/home, /api/docs) back to the server base."""
    url = url.strip()
    parts = urlsplit(url)
    segments = parts.path.split("/")
    for index, segment in enumerate(segments):
        if segment.casefold() in _NON_BASE_SEGMENTS:
            segments = segments[:index]
            break
    else:
        return url
    # Query and fragment belong to the cut browser page, so they go too.
    return urlunsplit((parts.scheme, parts.netloc, "/".join(segments), "", ""))


def _error_key(error: Exception) -> str:
    """Map a validation failure to a translated error key."""
    if isinstance(error, MealieAuthError):
        return "invalid_auth"
    if isinstance(error, (MealieError, aiohttp.ClientError)):
        return "cannot_connect"
    if isinstance(error, (ValueError, TypeError)):
        return "invalid_url"
    return "unknown"


def _unique_id(account: Mapping[str, Any]) -> str:
    """One entry per Mealie account, however many addresses reach the server."""
    return str(account["id"])


def account_id_of(unique_id: str | None) -> str:
    """Account id of a unique id, also of the legacy "<url>:<account id>" form."""
    return str(unique_id).rsplit(":", 1)[-1]


def entry_title(url: str, username: str) -> str:
    """Name the user and the server, since every default Mealie user is called admin."""
    return f"Mealie Atelier Bridge · {username}@{urlsplit(url).netloc}"


def _entry_title(client: MealieClient, account: Mapping[str, Any]) -> str:
    return entry_title(client.url, account.get("username", "Kitchen"))


# Explicit password selector so masking does not depend on frontend name heuristics.
SECRET_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
CONF_CLEAR_FDC_KEY = "clear_fdc_key"
CONF_CLEAR_AI_KEY = "clear_ai_key"
_CLEARABLE_KEYS = ((CONF_FDC_KEY, CONF_CLEAR_FDC_KEY), (CONF_AI_KEY, CONF_CLEAR_AI_KEY))
NOTIFY_SERVICE_RE = re.compile(r"notify\.[a-z0-9_]+")


class MealieAtelierConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a secure, server-side connection to Mealie."""

    VERSION = 2

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
                await self.async_set_unique_id(_unique_id(account))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_entry_title(client, account),
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
                        vol.Required(CONF_API_TOKEN): SECRET_SELECTOR,
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
                _client, account = await _validate(
                    self.hass,
                    entry.data[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    entry.data.get(CONF_VERIFY_SSL, True),
                )
            except Exception as error:  # noqa: BLE001
                errors["base"] = _error_key(error)
            else:
                if _unique_id(account) != account_id_of(entry.unique_id):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip()},
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): SECRET_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Change the server URL, token or TLS check of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                # An empty token keeps the saved one, so the form never has to carry it.
                token = user_input.get(CONF_API_TOKEN, "").strip() or entry.data[CONF_API_TOKEN]
                client, account = await _validate(
                    self.hass,
                    user_input[CONF_URL],
                    token,
                    user_input[CONF_VERIFY_SSL],
                )
            except Exception as error:  # noqa: BLE001
                errors["base"] = _error_key(error)
            else:
                # A new URL is fine, a different Mealie account is not: studio data and timers
                # are stored per entry and would leak.
                if _unique_id(account) != account_id_of(entry.unique_id):
                    return self.async_abort(reason="wrong_account")
                # Follow the new host in the title, unless the user renamed the entry.
                username = account.get("username", "Kitchen")
                generated = entry.title == entry_title(entry.data[CONF_URL], username)
                return self.async_update_reload_and_abort(
                    entry,
                    title=entry_title(client.url, username) if generated else entry.title,
                    data_updates={
                        CONF_URL: client.url,
                        CONF_API_TOKEN: token,
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
                        vol.Optional(CONF_API_TOKEN, default=""): SECRET_SELECTOR,
                        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                    }
                ),
                # Never suggest the saved token: suggested values travel to the browser.
                {
                    CONF_URL: (user_input or entry.data)[CONF_URL],
                    CONF_VERIFY_SSL: (user_input or entry.data).get(CONF_VERIFY_SSL, True),
                },
            ),
            errors=errors,
        )


class MealieAtelierOptionsFlow(config_entries.OptionsFlowWithReload):
    """Change dashboard write permissions without editing YAML."""

    async def async_step_init(self, user_input=None):
        errors = {}
        # Home Assistant has no user selector. Offer active, non-system, non-admin users and forget stored
        # ids of users that were deleted or deactivated, so the form never submits a stale one.
        users = sorted(
            (
                user
                for user in await self.hass.auth.async_get_users()
                if not user.system_generated and user.is_active and not user.is_admin
            ),
            key=lambda user: (user.name or user.id).casefold(),
        )
        known = {user.id for user in users}
        if user_input is not None:
            service = user_input.get(CONF_TIMER_NOTIFY_SERVICE, "").strip()
            if service and not NOTIFY_SERVICE_RE.fullmatch(service):
                errors[CONF_TIMER_NOTIFY_SERVICE] = "invalid_notify_service"
            else:
                data = {
                    **user_input,
                    CONF_TIMER_NOTIFY_SERVICE: service,
                    # The selector only accepts ids from its options, so no stale id reaches here.
                    CONF_WRITE_USERS: user_input.get(CONF_WRITE_USERS, []),
                }
                # The form never shows a saved key, so an empty field means "keep it".
                for key, clear_key in _CLEARABLE_KEYS:
                    clear = data.pop(clear_key, False)
                    new = str(data.get(key) or "").strip()
                    saved = self.config_entry.options.get(key, "")
                    data[key] = "" if clear else new or saved
                return self.async_create_entry(data=data)
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
                vol.Optional(CONF_FDC_KEY, default=""): SECRET_SELECTOR,
                vol.Optional(CONF_CLEAR_FDC_KEY, default=False): bool,
                vol.Optional(CONF_AI_KEY, default=""): SECRET_SELECTOR,
                vol.Optional(CONF_CLEAR_AI_KEY, default=False): bool,
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
        if not user_input:
            # Stored secrets never travel to the browser. A key the user just typed stays.
            for key, _clear_key in _CLEARABLE_KEYS:
                suggested.pop(key, None)
        suggested[CONF_WRITE_USERS] = [
            user_id for user_id in suggested.get(CONF_WRITE_USERS) or [] if user_id in known
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            # Hassfest rejects URLs inside translations, so the sign-up links are placeholders.
            description_placeholders={
                "openai_keys_url": OPENAI_KEYS_URL,
                "anthropic_keys_url": ANTHROPIC_KEYS_URL,
                "usda_signup_url": USDA_SIGNUP_URL,
            },
        )

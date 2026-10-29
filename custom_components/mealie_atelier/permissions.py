"""Who may change what: pure rules plus one helper that answers a refused command."""

from __future__ import annotations

from typing import Any, Literal

from homeassistant.auth.models import User
from homeassistant.config_entries import ConfigEntry

from .const import CONF_READ_ONLY, CONF_WRITE_USERS, DOMAIN

Level = Literal["write", "admin"]

_MESSAGES = {
    "read_only": "Mealie Atelier is in read-only mode",
    "unauthorized": "Only Home Assistant admins can do this",
    "write_not_permitted": (
        "Your Home Assistant user may not change data. "
        "An administrator can grant access in the Mealie Atelier Bridge options"
    ),
}


def is_read_only(entry: ConfigEntry) -> bool:
    return bool(entry.options.get(CONF_READ_ONLY, False))


def can_write(entry: ConfigEntry, user: User | None) -> bool:
    """Shopping lists, meal plans and kitchen state. An empty grant list means everyone."""
    if user is None or user.system_generated or is_read_only(entry):
        return False
    grants = entry.options.get(CONF_WRITE_USERS) or []
    return user.is_admin or not grants or user.id in grants


def can_admin(entry: ConfigEntry, user: User | None) -> bool:
    """Recipe management and other destructive actions. Grants never widen this."""
    return bool(user and user.is_admin and not is_read_only(entry))


def deny_reason(entry: ConfigEntry, user: User | None, level: Level) -> str | None:
    """Return the error code that refuses the user, or None when the action is allowed."""
    if is_read_only(entry):
        return "read_only"
    if level == "admin":
        return None if can_admin(entry, user) else "unauthorized"
    return None if can_write(entry, user) else "write_not_permitted"


def check(connection: Any, msg: dict, entry: ConfigEntry, level: Level) -> bool:
    """Send the refusal and return False, or return True when the command may proceed."""
    return _answer(connection, msg, deny_reason(entry, connection.user, level))


def check_personal(connection: Any, msg: dict, entry: ConfigEntry) -> bool:
    """Own journal and own timers: only read-only mode blocks them, grants do not apply."""
    return _answer(connection, msg, "read_only" if is_read_only(entry) else None)


def _answer(connection: Any, msg: dict, code: str | None) -> bool:
    if code is None:
        return True
    connection.send_error(
        msg["id"], code, _MESSAGES[code], translation_key=code, translation_domain=DOMAIN
    )
    return False

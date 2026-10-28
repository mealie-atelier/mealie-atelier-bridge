"""Persistent, server-side kitchen timers for native Home Assistant cards.

Timers continue after Lovelace tabs close and are reloaded after HA restarts.
Delivery uses HA's notification services, never browser setTimeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from uuid import UUID

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ACTIVE_STATUSES = ("scheduled", "retry", "sending")
FINISHED_RETENTION_MS = 2 * 24 * 3600_000
MAX_ACTIVE_TIMERS = 300
MAX_ACTIVE_TIMERS_PER_USER = 20


def _invalid(key: str) -> ServiceValidationError:
    """Build a translated validation error for a user-triggered timer problem."""
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def _uuid(value):
    try:
        return str(UUID(value)) == value.lower()
    except (ValueError, TypeError, AttributeError):
        return False


class TimerManager:
    def __init__(self, hass, entry_id, notify_service=""):
        self.hass = hass
        self.store = Store(hass, 1, f"mealie_atelier_alarms_{entry_id}")
        self.notify_service = (
            notify_service
            if isinstance(notify_service, str)
            and notify_service.startswith("notify.")
            and notify_service[7:].replace("_", "").isalnum()
            else ""
        )
        self.alarms = []
        self.lock = asyncio.Lock()
        self.task = None

    async def async_start(self):
        stored = await self.store.async_load() or {"alarms": []}
        self.alarms = stored.get("alarms", [])
        async with self.lock:
            if self._prune(int(time.time() * 1000)):
                await self._save()
        # Home Assistant is expected to have a running event loop here.
        self.task = asyncio.create_task(self._run(), name="mealie_atelier_alarm_scheduler")

    async def async_stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def _prune(self, now):
        """Drop finished timers past the retention window. Returns True if any were removed."""
        kept = [
            a
            for a in self.alarms
            if a.get("status") in ACTIVE_STATUSES
            or a.get("deadline", 0) > now - FINISHED_RETENTION_MS
        ]
        changed = len(kept) != len(self.alarms)
        self.alarms = kept
        return changed

    async def _save(self):
        await self.store.async_save({"alarms": self.alarms})

    async def async_create(self, owner, timer):
        now = int(time.time() * 1000)
        uid = timer.get("id") if isinstance(timer, dict) else None
        label = timer.get("label") if isinstance(timer, dict) else None
        due = timer.get("deadline") if isinstance(timer, dict) else None
        if (
            not _uuid(uid)
            or not isinstance(label, str)
            or not label.strip()
            or len(label) > 100
            or type(due) is not int
            or due < now + 1000
            or due > now + 24 * 3600_000
        ):
            raise _invalid("invalid_timer")
        async with self.lock:
            existing = next((a for a in self.alarms if a["id"] == uid), None)
            if existing:
                if (
                    existing["owner"] != owner
                    or existing["deadline"] != due
                    or existing["label"] != label.strip()
                ):
                    raise _invalid("timer_exists")
                return deepcopy(existing)
            if self._prune(now):
                await self._save()
            active = [a for a in self.alarms if a.get("status") in ACTIVE_STATUSES]
            if (
                len(active) > MAX_ACTIVE_TIMERS
                or sum(1 for a in active if a["owner"] == owner) >= MAX_ACTIVE_TIMERS_PER_USER
            ):
                raise _invalid("too_many_timers")
            entry = {
                "id": uid,
                "label": label.strip(),
                "deadline": due,
                "owner": owner,
                "status": "scheduled",
                "attempts": 0,
                "channels": [],
                "createdAt": now,
                "nextAttemptAt": due,
            }
            self.alarms.append(entry)
            await self._save()
            return deepcopy(entry)

    async def async_cancel(self, actor, uid, is_admin=False):
        async with self.lock:
            entry = next((a for a in self.alarms if a["id"] == uid), None)
            if not entry:
                raise _invalid("timer_not_found")
            if entry["owner"] != actor and not is_admin:
                raise _invalid("timer_not_permitted")
            if entry["status"] in ("sending", "sent"):
                raise _invalid("timer_dispatched")
            entry["status"] = "cancelled"
            await self._save()
            return deepcopy(entry)

    def list(self, actor, admin=False):
        return {"alarms": deepcopy([a for a in self.alarms if admin or a["owner"] == actor])}

    async def _deliver(self, entry):
        title = "Mealie Atelier · Küchentimer"
        message = f"{entry['label']} ist fertig!"
        channels = []
        # HA always receives an internal persistent notification (visible after reopening HA).
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": f"mealie_atelier_{entry['id']}",
                },
                blocking=True,
            )
            channels.append("ha_persistent")
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning(
                "Kitchen timer persistent notification failed (%s)", type(error).__name__
            )
        if self.notify_service:
            service = self.notify_service.split(".", 1)[1]
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": title,
                        "message": message,
                        "data": {"tag": f"mealie-atelier-{entry['id']}"},
                    },
                    blocking=True,
                )
                channels.append("mobile_push_accepted")
            except Exception as error:  # noqa: BLE001
                _LOGGER.warning(
                    "Kitchen timer mobile notification failed (%s)", type(error).__name__
                )
        return channels

    async def async_tick(self):
        now = int(time.time() * 1000)
        async with self.lock:
            if self._prune(now):
                await self._save()
            next_alarm = next(
                (
                    a
                    for a in self.alarms
                    if a["status"] in ("scheduled", "retry", "sending")
                    and a.get("nextAttemptAt", a["deadline"]) <= now
                ),
                None,
            )
            if not next_alarm:
                return False
            if now - next_alarm["deadline"] > 24 * 3600_000:
                next_alarm["status"] = "expired"
                await self._save()
                return False
            next_alarm["status"] = "sending"
            next_alarm["attempts"] += 1
            await self._save()
            uid = next_alarm["id"]
            snapshot = deepcopy(next_alarm)
        channels = await self._deliver(snapshot)
        async with self.lock:
            entry = next((a for a in self.alarms if a["id"] == uid), None)
            if not entry or entry["status"] != "sending":
                return True
            entry["channels"] = channels
            if channels:
                entry["status"] = "sent"
                entry["sentAt"] = now
            elif entry["attempts"] >= 10:
                entry["status"] = "failed"
            else:
                entry["status"] = "retry"
                entry["nextAttemptAt"] = now + min(120000, 2000 * 2 ** min(entry["attempts"], 6))
            await self._save()
        return True

    async def _run(self):
        while True:
            try:
                await self.async_tick()
            except Exception as error:  # noqa: BLE001
                # Keep the timer scheduler alive while leaving a diagnostic without timer data.
                _LOGGER.error("Kitchen timer scheduler iteration failed (%s)", type(error).__name__)
            await asyncio.sleep(1)

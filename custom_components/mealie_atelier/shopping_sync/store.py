"""Durable sync state in Home Assistant's .storage: rules, outbox records and a small audit log."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import (
    SYNC_AUDIT_LIMIT,
    SYNC_PRUNE_DAYS,
    SYNC_RECORD_LIMIT,
    SYNC_STORE_MINOR_VERSION,
    SYNC_STORE_VERSION,
)
from .model import PROTECTED_STATES, RecordState

_LOGGER = logging.getLogger(__name__)


def empty() -> dict[str, Any]:
    return {"rules": {}, "records": {}, "audit": []}


def _valid(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("rules"), dict)
        and isinstance(data.get("records"), dict)
        and isinstance(data.get("audit"), list)
        and all(isinstance(rule, dict) for rule in data["rules"].values())
        and all(
            isinstance(record, dict) and record.get("state") in RecordState
            for record in data["records"].values()
        )
    )


def _parse(value: Any) -> datetime:
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    return parsed or datetime.min.replace(tzinfo=dt_util.UTC)


def prune(
    data: dict[str, Any],
    *,
    present: dict[str, set[str]] | None = None,
    now: datetime | None = None,
) -> int:
    """Drop old finished records; never a record that still needs attention. Returns the count.

    `present` maps rule ids to the source item ids seen in the latest read of that rule's list;
    only finished records whose item is gone and that are older than the retention are dropped.
    """
    records = data["records"]
    before = len(records)
    for key, record in list(records.items()):
        if record.get("rule_id") not in data["rules"]:
            del records[key]
    if present:
        cutoff = (now or dt_util.utcnow()) - timedelta(days=SYNC_PRUNE_DAYS)
        for key, record in list(records.items()):
            seen = present.get(record["rule_id"])
            if (
                seen is not None
                and record["state"] == RecordState.SOURCE_COMPLETED
                and record["source_item_id"] not in seen
                and _parse(record.get("updated_at")) < cutoff
            ):
                del records[key]
    overflow = len(records) - SYNC_RECORD_LIMIT
    if overflow > 0:
        # Completed items go first: a dropped `sent` record of an item that is still open in
        # Mealie would be enqueued again on the next run.
        finished = sorted(
            (record for record in records.values() if record["state"] not in PROTECTED_STATES),
            key=lambda record: (
                record["state"] != RecordState.SOURCE_COMPLETED,
                _parse(record.get("updated_at")),
            ),
        )
        for record in finished[:overflow]:
            del records[record["key"]]
    del data["audit"][:-SYNC_AUDIT_LIMIT]
    return before - len(records)


class SyncStore:
    """Thin wrapper around `Store` that never lets a damaged file break setup."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.key = f"mealie_atelier_sync_{entry_id}"
        self._store: Store[dict[str, Any]] = Store(
            hass, SYNC_STORE_VERSION, self.key, minor_version=SYNC_STORE_MINOR_VERSION
        )
        self.data: dict[str, Any] = empty()

    async def async_load(self) -> dict[str, Any]:
        try:
            loaded = await self._store.async_load()
        except Exception:  # noqa: BLE001
            # A sync problem must never fail setup: keep the damaged file aside and start empty.
            loaded = False
        if loaded is None:
            self.data = empty()
        elif loaded is not False and _valid(loaded):
            self.data = loaded
        else:
            _LOGGER.warning("Shopping sync storage is unreadable; starting empty")
            await self.hass.async_add_executor_job(self._set_aside)
            self.data = empty()
        prune(self.data)
        return self.data

    def _set_aside(self) -> None:
        path = self.hass.config.path(".storage", self.key)
        if os.path.exists(path):
            os.replace(path, f"{path}.corrupt")

    async def async_save(self) -> None:
        """Write now; used around destination calls so a crash leaves a known state."""
        await self._store.async_save(self.data)

    def async_delay_save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 2)

    async def async_remove(self) -> None:
        await self._store.async_remove()

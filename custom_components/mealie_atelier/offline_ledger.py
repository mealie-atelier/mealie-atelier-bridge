"""Durable idempotency and optimistic conflict checks for offline shopping edits.

Mirrors the standalone app's `offline-ledger.mjs`. A started non-idempotent add is never blindly
replayed after a crash: the `sending` state is persisted before Mealie is touched.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import re
import time
from typing import Any

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import MealieClient, MealieError
from .const import OFFLINE_PRUNE_DAYS, OFFLINE_RECORD_LIMIT, OFFLINE_STORE_VERSION
from .errors import AtelierError
from .recipe_editing import _run

_LOGGER = logging.getLogger(__name__)

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE
)
_ID = re.compile(r"^[a-zA-Z0-9_-]{1,150}$")
_STATES = frozenset({"checking", "sending", "completed", "conflict", "uncertain"})
_MAX_AGE_SECONDS = OFFLINE_PRUNE_DAYS * 86400


# Actions that read Mealie before any write and can be repeated safely, so a lost answer or a
# crash never needs a blind-replay guard. Only an add must never be sent twice.
_RECHECKABLE = frozenset(
    {"shopping_toggle", "shopping_update", "shopping_delete", "shopping_clear_checked"}
)
_ACTIONS = _RECHECKABLE | {"shopping_add"}


def _invalid(message: str) -> AtelierError:
    return AtelierError("invalid_request", message)


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 100000
    )


def _clean_update(payload: dict[str, Any]) -> dict[str, Any]:
    """An update names its item, the new note and/or quantity, and the value seen for each."""
    if not _is_id(payload.get("item_id")):
        raise _invalid("Update needs an item ID")
    clean: dict[str, Any] = {"item_id": payload["item_id"]}
    if "note" in payload:
        note = payload["note"]
        expected = payload.get("expected_note")
        if (
            not isinstance(note, str)
            or not 0 < len(note.strip()) <= 250
            or not (expected is None or isinstance(expected, str))
            or "expected_note" not in payload
        ):
            raise _invalid("A note change needs the new and the original note")
        clean["note"] = note.strip()
        clean["expected_note"] = expected
    if "quantity" in payload:
        quantity = payload["quantity"]
        expected = payload.get("expected_quantity")
        if (
            not _is_number(quantity)
            or not (expected is None or _is_number(expected))
            or "expected_quantity" not in payload
        ):
            raise _invalid("A quantity change needs the new and the original quantity")
        clean["quantity"] = quantity
        clean["expected_quantity"] = expected
    if len(clean) == 1:
        raise _invalid("Update needs a note or a quantity")
    return clean


def validate_operation(operation: Any) -> dict[str, Any]:
    """Return the canonical operation or raise `invalid_request` (contract H.1)."""
    if (
        not isinstance(operation, dict)
        or not isinstance(operation.get("id"), str)
        or not _UUID.fullmatch(operation["id"])
    ):
        raise _invalid("Invalid offline operation ID")
    action = operation.get("action")
    if action not in _ACTIONS:
        raise _invalid("Unsupported offline operation")
    payload = operation.get("payload")
    if not isinstance(payload, dict):
        raise _invalid("Invalid operation payload")
    if action == "shopping_toggle":
        if (
            not _is_id(payload.get("item_id"))
            or not isinstance(payload.get("checked"), bool)
            or not isinstance(payload.get("expected_checked"), bool)
        ):
            raise _invalid("Toggle needs item ID, desired and original state")
        clean = {key: payload[key] for key in ("item_id", "checked", "expected_checked")}
    elif action == "shopping_delete":
        if not _is_id(payload.get("item_id")):
            raise _invalid("Delete needs an item ID")
        clean = {"item_id": payload["item_id"]}
    elif action == "shopping_clear_checked":
        if not _is_id(payload.get("list_id")):
            raise _invalid("Clearing ticked items needs a list ID")
        clean = {"list_id": payload["list_id"]}
    elif action == "shopping_update":
        clean = _clean_update(payload)
    else:
        note = payload.get("note")
        if (
            not _is_id(payload.get("list_id"))
            or not isinstance(note, str)
            or not 0 < len(note.strip()) <= 250
        ):
            raise _invalid("Add needs an existing list and note")
        clean = {"list_id": payload["list_id"], "note": note}
    return {"id": operation["id"], "action": action, "payload": copy.deepcopy(clean)}


def _valid(data: Any) -> bool:
    return isinstance(data, dict) and all(
        isinstance(key, str)
        and isinstance(record, dict)
        and record.get("state") in _STATES
        and isinstance(record.get("operation"), dict)
        and isinstance(record.get("updated_at"), (int, float))
        for key, record in data.items()
    )


# A toggle, update, delete or clear re-reads Mealie first, so forgetting it is safe. An add that may have
# reached Mealie (`sending`, `uncertain`) is never forgotten: that would allow a blind duplicate.
_PRUNABLE = ("completed", "checking", "conflict")


def _cap_prunable(record: dict[str, Any]) -> bool:
    if record["state"] == "completed":
        return record["operation"].get("action") != "shopping_add"
    return record["state"] in _PRUNABLE


def prune(data: dict[str, dict[str, Any]], now: float, keep: str | None = None) -> None:
    """Drop prunable records older than 30 days, then the oldest ones over the cap.

    `keep` is the operation being processed: its record must survive, or a lost answer could
    let the same add reach Mealie twice.
    """
    for key, record in list(data.items()):
        if key == keep:
            continue
        if record["state"] in _PRUNABLE and record["updated_at"] < now - _MAX_AGE_SECONDS:
            del data[key]
    overflow = len(data) - OFFLINE_RECORD_LIMIT
    if overflow > 0:
        # Completed toggles go first, then the oldest re-checkable toggles. A completed add only
        # expires by age: its answer may have been lost, and a replay must still find it.
        prunable = sorted(
            (key for key, record in data.items() if key != keep and _cap_prunable(record)),
            key=lambda key: (data[key]["state"] != "completed", data[key]["updated_at"]),
        )
        for key in prunable[:overflow]:
            del data[key]


class OfflineLedger:
    """Per-entry ledger; one lock serialises every operation of the entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: MealieClient) -> None:
        self._client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, OFFLINE_STORE_VERSION, f"mealie_atelier_offline_{entry_id}"
        )
        self._lock = asyncio.Lock()
        self.data: dict[str, dict[str, Any]] = {}

    @classmethod
    async def async_load(
        cls, hass: HomeAssistant, entry_id: str, client: MealieClient
    ) -> OfflineLedger:
        """Load the ledger; a damaged file starts empty and never fails setup."""
        ledger = cls(hass, entry_id, client)
        try:
            loaded = await ledger._store.async_load()
        except Exception:  # noqa: BLE001
            loaded = False
        if loaded is None:
            ledger.data = {}
        elif loaded is not False and _valid(loaded):
            ledger.data = loaded
        else:
            _LOGGER.warning("Offline ledger storage is unreadable; starting empty")
            ledger.data = {}
        return ledger

    async def async_remove(self) -> None:
        await self._store.async_remove()

    async def _save(self) -> None:
        await self._store.async_save(self.data)

    async def process(self, operation: Any) -> dict[str, Any]:
        """Validate and apply one operation exactly once."""
        safe = validate_operation(operation)
        async with self._lock:
            return await self._process_locked(safe)

    async def _process_locked(self, op: dict[str, Any]) -> dict[str, Any]:
        data = self.data
        prune(data, time.time())
        record = data.get(op["id"])
        if record is not None and record["operation"] != op:
            raise AtelierError(
                "offline_op_reused", "That operation ID was already used with different content"
            )
        if record is not None and record["state"] == "completed":
            return {**record["result"], "replayed": True}
        if record is not None and record["state"] == "conflict":
            raise AtelierError(
                "offline_conflict",
                record.get("message") or "A newer edit exists. Resolve it on this device",
            )
        if record is not None and record["state"] == "uncertain" and op["action"] == "shopping_add":
            raise _add_uncertain()
        if record is None:
            record = data[op["id"]] = {
                "operation": op,
                "state": "checking",
                "updated_at": time.time(),
            }
            prune(data, time.time(), keep=op["id"])
            await self._save()

        async def set_state(state: str, **fields: Any) -> None:
            record.update(state=state, updated_at=time.time(), **fields)
            await self._save()

        payload = op["payload"]
        action = op["action"]
        current: dict[str, Any] | None = None
        if action == "shopping_toggle":
            try:
                current = await self._client.shopping_item(payload["item_id"])
            except Exception:
                await set_state("checking")
                raise
            if not isinstance(current.get("checked"), bool):
                raise AtelierError("mealie_error", "Mealie returned an invalid shopping item")
            if current["checked"] == payload["checked"]:
                result = {"ok": True, "already_applied": True, "item": current}
                await set_state("completed", result=result)
                return result
            if current["checked"] != payload["expected_checked"]:
                message = "This item was changed on another device. Review both values"
                await set_state("conflict", message=message)
                raise AtelierError("offline_conflict", message)
        elif action == "shopping_update":
            try:
                current = await self._client.shopping_item(payload["item_id"])
            except MealieError as err:
                if err.status == 404:
                    message = "This item no longer exists in Mealie"
                    await set_state("conflict", message=message)
                    raise AtelierError("shopping_item_not_found", message) from err
                await set_state("checking")
                raise
            except Exception:
                await set_state("checking")
                raise
            changes = {key: payload[key] for key in ("note", "quantity") if key in payload}
            if all(_same(key, current.get(key), value) for key, value in changes.items()):
                result = {"ok": True, "already_applied": True, "item": current}
                await set_state("completed", result=result)
                return result
            for key in changes:
                if not _same(key, current.get(key), payload[f"expected_{key}"]):
                    message = "This item was changed on another device. Review both values"
                    await set_state("conflict", message=message)
                    raise AtelierError("offline_conflict", message)
        elif record["state"] in ("uncertain", "sending") and action not in _RECHECKABLE:
            raise _add_uncertain()

        # Persist the sending marker BEFORE touching Mealie, so a crash cannot cause a blind replay.
        await set_state("sending")
        try:
            if action == "shopping_toggle":
                # Write exactly the item that was just compared, not a fresh read.
                mealie_result = await self._client.set_shopping_item_checked(
                    payload["item_id"], payload["checked"], current
                )
            elif action == "shopping_update":
                changes = {key: payload[key] for key in ("note", "quantity") if key in payload}
                mealie_result = await self._client.update_shopping_item(
                    payload["item_id"], changes, current
                )
            elif action == "shopping_delete":
                try:
                    mealie_result = await self._client.mutate(action, payload)
                except MealieError as err:
                    if err.status != 404:
                        raise
                    # Already gone: the wish of the user is fulfilled.
                    result = {"ok": True, "already_applied": True}
                    await set_state("completed", result=result)
                    return result
            else:
                mealie_result = await self._client.mutate(action, payload)
        except Exception as exc:
            await set_state(
                "checking" if action in _RECHECKABLE else "uncertain",
                message=str(exc)[:200],
            )
            raise
        response = {"ok": True, "result": mealie_result}
        await set_state("completed", result=response)
        return response


def _same(key: str, current: Any, wanted: Any) -> bool:
    """Compare a note or quantity; an empty note equals none, a missing quantity equals zero."""
    if key == "note":
        return (current or "").strip() == (wanted or "").strip()
    return (current or 0) == (wanted or 0)


def _add_uncertain() -> AtelierError:
    return AtelierError(
        "offline_add_uncertain",
        "An earlier add may have reached Mealie. Check the list to avoid a duplicate",
    )


def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_offline_replay)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "mealie_atelier/offline_replay",
        probatio.Optional("entry_id"): str,
        probatio.Required("operation"): dict,
    }
)
@websocket_api.async_response
async def ws_offline_replay(hass, connection, msg):
    async def run(entry) -> dict[str, Any]:
        result = await entry.runtime_data.offline.process(msg["operation"])
        if msg["operation"]["action"] == "shopping_add" and "replayed" not in result:
            # Same hook as `mutate`: a confirmed add may feed a shopping sync rule.
            try:
                entry.runtime_data.sync.notify_source_changed(
                    msg["operation"]["payload"]["list_id"], origin="after_atelier_add"
                )
            except Exception:
                _LOGGER.exception("Shopping sync could not be notified")
        return result

    await _run(hass, connection, msg, "write", run)

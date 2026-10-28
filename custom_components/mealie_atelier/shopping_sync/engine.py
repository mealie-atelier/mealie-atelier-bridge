"""Sync manager: one serialized outbox per rule from a Mealie list to a Home Assistant To-do list.

The guarantees from docs/SMART_SHOPPING_SYNC.md live here: a Mealie item is only checked off after
the destination accepted it (or a matching item was confirmed there), an uncertain write is
reconciled against the destination before it is sent again, and each rule runs one pass at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Coroutine
from copy import deepcopy
from datetime import datetime, timedelta
from functools import partial
from typing import Any
from uuid import uuid4

import probatio
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from ..api import MealieClient, MealieError
from ..const import (
    EVENT_SYNC_FINISHED,
    SYNC_BATCH_SIZE,
    SYNC_MAX_LINE_FAILURES,
    SYNC_MAX_RULES,
    SYNC_SERVICE_TIMEOUT,
    SYNC_STARTUP_DELAY,
    SYNC_STOP_TIMEOUT,
)
from .mapping import fingerprint, map_item, normalize, plan_batch
from .model import STATES, RecordState, SyncError, new_record, validate_draft
from .store import SyncStore, prune

_LOGGER = logging.getLogger(__name__)

# Bits of homeassistant.components.todo.TodoListEntityFeature.
FEATURE_CREATE = 1
FEATURE_UPDATE = 4
FEATURE_DESCRIPTION = 64

AUTOMATIC_TRIGGERS = frozenset({"after_atelier_add", "watch", "startup"})
_RESULT_COUNTS = (
    "sent",
    "skipped_duplicates",
    "merged",
    "failed",
    "needs_reconciliation",
    "completed_in_source",
)
_PUBLIC_HIDDEN = ("fingerprint", "sent_fingerprint")


def _now() -> str:
    return dt_util.utcnow().isoformat()


def _snapshot(items: list[dict[str, Any]]) -> str:
    pairs = sorted(
        (item["id"], fingerprint(item)) for item in items if isinstance(item.get("id"), str)
    )
    return hashlib.sha256(repr(pairs).encode()).hexdigest()


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in _PUBLIC_HIDDEN}


class SyncManager:
    """Owns rules, the outbox and every trigger of one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: MealieClient) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self.store = SyncStore(hass, entry_id)
        self._locks: dict[str, asyncio.Lock] = {}
        # Serializes rule saves so checks and the write cannot interleave across an await.
        self._save_lock = asyncio.Lock()
        self._rerun: dict[str, str] = {}
        self._scheduled: dict[str, tuple[CALLBACK_TYPE, datetime]] = {}
        self._watchers: dict[str, Any] = {}
        self._baseline: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._active: set[asyncio.Task] = set()
        self._unsub_started: CALLBACK_TYPE | None = None
        self._stopped = False

    # State access

    @property
    def rules(self) -> dict[str, dict[str, Any]]:
        return self.store.data["rules"]

    @property
    def records(self) -> dict[str, dict[str, Any]]:
        return self.store.data["records"]

    def _rule(self, rule_id: str) -> dict[str, Any]:
        rule = self.rules.get(rule_id) if isinstance(rule_id, str) else None
        if rule is None:
            raise SyncError("sync_rule_not_found", "Unknown sync rule")
        return rule

    def _rule_records(self, rule_id: str) -> list[dict[str, Any]]:
        return [record for record in self.records.values() if record["rule_id"] == rule_id]

    def _members(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Records merged into this line that share its state."""
        return [
            other
            for other in self.records.values()
            if other["merged_into"] == record["key"]
            and other["key"] != record["key"]
            and other["state"] == record["state"]
        ]

    def _is_member(self, record: dict[str, Any]) -> bool:
        lead = self.records.get(record["merged_into"] or "")
        return lead is not None and lead is not record and lead["state"] == record["state"]

    @staticmethod
    def _set(record: dict[str, Any], state: RecordState, **changes: Any) -> None:
        record.update(changes, state=state.value, updated_at=_now())

    def counts(self) -> dict[str, Any]:
        """Content-free numbers for diagnostics."""
        states = dict.fromkeys(STATES, 0)
        for record in self.records.values():
            states[record["state"]] += 1
        return {
            "rules": len(self.rules),
            "activated": sum(1 for rule in self.rules.values() if rule.get("activated")),
            "paused": sum(1 for rule in self.rules.values() if rule.get("paused")),
            "records": states,
            "audit": len(self.store.data["audit"]),
            "watchers": len(self._watchers),
        }

    # Lifecycle

    async def async_start(self) -> None:
        await self.store.async_load()
        interrupted = [
            record for record in self.records.values() if record["state"] == RecordState.SENDING
        ]
        for record in interrupted:
            # The process stopped between "about to send" and "result recorded": the destination
            # may or may not have the item, so it is looked up before anything is sent again.
            self._set(record, RecordState.NEEDS_RECONCILIATION, last_error="destination_timeout")
        if interrupted:
            await self.store.async_save()
        self._unsub_started = async_at_started(self.hass, self._on_started)

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._unsub_started = None
        if self._stopped:
            return
        for rule in self.rules.values():
            if not rule.get("activated") or rule.get("paused"):
                continue
            if any(
                record["state"] == RecordState.NEEDS_RECONCILIATION or record["completion_pending"]
                for record in self._rule_records(rule["id"])
            ):
                self._schedule_run(rule["id"], "startup", SYNC_STARTUP_DELAY)
        self._refresh_watchers()

    async def async_stop(self) -> None:
        """Cancel timers and background work, let running passes finish briefly, flush."""
        self._stopped = True
        if self._unsub_started:
            self._unsub_started()
            self._unsub_started = None
        for cancel, _due in self._scheduled.values():
            cancel()
        self._scheduled.clear()
        for rule_id in list(self._watchers):
            self._stop_watcher(rule_id)
        current = asyncio.current_task()
        running = [task for task in self._active | self._tasks if task is not current]
        if running:
            _done, pending = await asyncio.wait(running, timeout=SYNC_STOP_TIMEOUT)
            # A pass cancelled while a line is `sending` keeps that state on disk and is
            # reconciled after the next start.
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        await self.store.async_save()

    def _spawn(self, coro: Coroutine[Any, Any, Any], name: str) -> None:
        task = self.hass.async_create_background_task(coro, name, eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # Triggers

    @callback
    def notify_source_changed(self, list_id: str, origin: str) -> None:
        """A source list changed; (re)start the debounce of every rule that listens to it."""
        if self._stopped:
            return
        for rule in self.rules.values():
            if (
                rule["source_list_id"] != list_id
                or not rule.get("activated")
                or rule.get("paused")
                or not rule["triggers"].get(origin, False)
            ):
                continue
            self._schedule_run(rule["id"], origin, rule["debounce_seconds"])

    @callback
    def _schedule_run(self, rule_id: str, trigger: str, delay: float) -> None:
        self._cancel_scheduled(rule_id)
        cancel = async_call_later(
            self.hass,
            delay,
            HassJob(partial(self._scheduled_due, rule_id, trigger), cancel_on_shutdown=True),
        )
        self._scheduled[rule_id] = (cancel, dt_util.utcnow() + timedelta(seconds=delay))

    @callback
    def _cancel_scheduled(self, rule_id: str) -> None:
        if scheduled := self._scheduled.pop(rule_id, None):
            scheduled[0]()

    @callback
    def _scheduled_due(self, rule_id: str, trigger: str, _now: datetime) -> None:
        self._scheduled.pop(rule_id, None)
        if not self._stopped and rule_id in self.rules:
            self._spawn(self._run_quietly(rule_id, trigger), f"mealie_atelier_sync_{trigger}")

    async def _run_quietly(self, rule_id: str, trigger: str) -> None:
        try:
            await self.async_run(rule_id, trigger)
        except SyncError:
            pass
        except Exception:
            _LOGGER.exception("Shopping sync run failed")

    def _wants_watch(self, rule: dict[str, Any]) -> bool:
        return bool(rule.get("activated") and not rule.get("paused") and rule["triggers"]["watch"])

    @callback
    def _refresh_watchers(self) -> None:
        """Poll exactly the activated, unpaused rules that ask for it."""
        if self._stopped:
            return
        wanted = {rule_id for rule_id, rule in self.rules.items() if self._wants_watch(rule)}
        for rule_id in set(self._watchers) - wanted:
            self._stop_watcher(rule_id)
        for rule_id in wanted - set(self._watchers):
            self._schedule_poll(rule_id)

    @callback
    def _stop_watcher(self, rule_id: str) -> None:
        handle = self._watchers.pop(rule_id, None)
        if callable(handle):
            handle()
        self._baseline.pop(rule_id, None)

    @callback
    def _schedule_poll(self, rule_id: str) -> None:
        self._watchers[rule_id] = async_call_later(
            self.hass,
            self.rules[rule_id]["poll_interval_seconds"],
            HassJob(partial(self._poll_due, rule_id), cancel_on_shutdown=True),
        )

    @callback
    def _poll_due(self, rule_id: str, _now: datetime) -> None:
        token = object()
        self._watchers[rule_id] = token
        self._spawn(self._poll(rule_id, token), "mealie_atelier_sync_watch")

    async def _poll(self, rule_id: str, token: object) -> None:
        rule = self.rules.get(rule_id)
        try:
            if rule is not None:
                items = await self.client.shopping_items(rule["source_list_id"])
                snapshot = _snapshot(items)
                previous = self._baseline.get(rule_id)
                self._baseline[rule_id] = snapshot
                untracked = previous is None and any(
                    isinstance(item.get("id"), str)
                    and not item.get("checked")
                    and f"{rule_id}:{item['id']}" not in self.records
                    for item in items
                )
                if untracked or (previous is not None and previous != snapshot):
                    self.notify_source_changed(rule["source_list_id"], "watch")
        except (MealieError, ValueError):
            _LOGGER.debug("Shopping sync watcher could not read the source list")
        finally:
            # Reschedule only when nobody paused, deleted or restarted this watcher meanwhile.
            if self._watchers.get(rule_id) is token:
                del self._watchers[rule_id]
                self._refresh_watchers()

    # Destination

    def destination(self, entity_id: str) -> dict[str, Any]:
        state = self.hass.states.get(entity_id)
        entry = er.async_get(self.hass).async_get(entity_id)
        features = int(state.attributes.get("supported_features", 0) or 0) if state else 0
        return {
            "entity_id": entity_id,
            "name": state.name if state else entity_id,
            "platform": entry.platform if entry else None,
            "available": state is not None and state.state != STATE_UNAVAILABLE,
            "supports": {
                "create": bool(features & FEATURE_CREATE),
                "description": bool(features & FEATURE_DESCRIPTION),
                "update": bool(features & FEATURE_UPDATE),
            },
        }

    @staticmethod
    def _usable(destination: dict[str, Any]) -> bool:
        # The core Mealie integration's lists would feed the sync back into Mealie.
        return destination["supports"]["create"] and destination["platform"] != "mealie"

    def destinations(self) -> list[dict[str, Any]]:
        found = [self.destination(state.entity_id) for state in self.hass.states.async_all("todo")]
        return sorted(
            (row for row in found if self._usable(row)),
            key=lambda row: (row["name"].casefold(), row["entity_id"]),
        )

    async def _destination_items(self, entity_id: str) -> list[dict[str, Any]] | None:
        """All destination items, or None when they cannot be read."""
        try:
            async with asyncio.timeout(SYNC_SERVICE_TIMEOUT):
                response = await self.hass.services.async_call(
                    "todo",
                    "get_items",
                    {"status": ["needs_action", "completed"]},
                    target={"entity_id": entity_id},
                    blocking=True,
                    return_response=True,
                )
            items = response[entity_id]["items"]
        except Exception:  # noqa: BLE001
            return None
        return (
            [item for item in items if isinstance(item, dict)] if isinstance(items, list) else None
        )

    async def _add_item(self, entity_id: str, title: str, description: str | None) -> str | None:
        """Add one line; return None on success or the error code of the failure."""
        data: dict[str, Any] = {"item": title}
        if description:
            data["description"] = description
        try:
            async with asyncio.timeout(SYNC_SERVICE_TIMEOUT):
                await self.hass.services.async_call(
                    "todo", "add_item", data, target={"entity_id": entity_id}, blocking=True
                )
        except TimeoutError:
            return "destination_timeout"
        except (HomeAssistantError, probatio.Invalid):
            return "destination_rejected"
        except Exception:  # noqa: BLE001
            # Unknown outcome: the destination may have stored the item.
            return "destination_timeout"
        return None

    # Run

    async def async_run(
        self, rule_id: str, trigger: str, *, confirm_first_batch: bool = False
    ) -> dict[str, Any]:
        """Run one pass for a rule; a trigger during a running pass queues exactly one more."""
        if self._stopped:
            raise SyncError("not_configured", "Shopping sync is stopping")
        self._rule(rule_id)
        lock = self._locks.setdefault(rule_id, asyncio.Lock())
        if lock.locked() and not confirm_first_batch:
            self._rerun[rule_id] = trigger
            return {"queued": True}
        task = asyncio.current_task()
        if task is not None:
            self._active.add(task)
        try:
            async with lock:
                result = await self._pass(rule_id, trigger, confirm_first_batch)
        finally:
            if task is not None:
                self._active.discard(task)
        again = self._rerun.pop(rule_id, None)
        if again is not None and rule_id in self.rules and not self._stopped:
            self._spawn(self._run_quietly(rule_id, again), f"mealie_atelier_sync_{again}")
        return result

    def _result(self, rule_id: str, batch_id: str, outcome: str, counts: dict) -> dict[str, Any]:
        pending = sum(
            1 for record in self._rule_records(rule_id) if record["state"] == RecordState.PENDING
        )
        return {
            "rule_id": rule_id,
            "batch_id": batch_id,
            "outcome": outcome,
            **counts,
            "pending_left": pending,
        }

    def _audit(self, rule_id: str, trigger: str, count: int, outcome: str) -> None:
        # Minimal by design: no titles, no notes, no tokens.
        self.store.data["audit"].append(
            {
                "at": _now(),
                "rule_id": rule_id,
                "trigger": trigger,
                "count": count,
                "outcome": outcome,
            }
        )

    def _finish(
        self,
        rule: dict[str, Any],
        trigger: str,
        batch_id: str,
        outcome: str,
        counts: dict[str, int],
        *,
        confirm: bool = False,
        present: set[str] | None = None,
    ) -> dict[str, Any]:
        rule_id = rule["id"]
        result = self._result(rule_id, batch_id, outcome, counts)
        items = sum(counts[key] for key in _RESULT_COUNTS if key != "completed_in_source")
        count = (
            items
            or counts["completed_in_source"]
            or (result["pending_left"] if outcome == "destination_unavailable" else 0)
        )
        if rule_id in self.rules:
            rule["last_run"] = {
                "at": _now(),
                "trigger": trigger,
                "outcome": outcome,
                "count": count,
            }
            self._audit(rule_id, trigger, count, outcome)
            if confirm and outcome in ("sent", "partial") and not rule.get("activated"):
                rule["activated"] = True
                rule["updated_at"] = _now()
                self._refresh_watchers()
        prune(self.store.data, present={rule_id: present} if present is not None else None)
        self.store.async_delay_save()
        self.hass.bus.async_fire(
            EVENT_SYNC_FINISHED,
            {"entry_id": self.entry_id, "rule_id": rule_id, "outcome": outcome, "count": count},
        )
        return result

    async def _pass(self, rule_id: str, trigger: str, confirm: bool) -> dict[str, Any]:
        rule = self._rule(rule_id)
        counts = dict.fromkeys(_RESULT_COUNTS, 0)
        batch_id = uuid4().hex
        if trigger in AUTOMATIC_TRIGGERS and (rule.get("paused") or not rule.get("activated")):
            outcome = "paused" if rule.get("paused") else "not_activated"
            return self._result(rule_id, batch_id, outcome, counts)
        entity_id = rule["destination_entity_id"]
        destination = self.destination(entity_id)
        if not destination["available"] or not self._usable(destination):
            return self._finish(rule, trigger, batch_id, "destination_unavailable", counts)
        try:
            items = await self.client.shopping_items(rule["source_list_id"])
        except (MealieError, ValueError):
            return self._finish(rule, trigger, batch_id, "source_unavailable", counts)
        by_id = {item["id"]: item for item in items if isinstance(item.get("id"), str)}
        language = (self.hass.config.language or "en").lower()
        options = {
            "decimal_comma": language.startswith("de"),
            "supports_description": destination["supports"]["description"],
        }
        destination_items = await self._destination_items(entity_id)
        if destination_items is not None:
            settled, confirmed = self._reconcile(rule_id, destination_items)
            if settled:
                self._audit(rule_id, trigger, settled, "reconciled")
            # Lines found at the destination were delivered by an earlier, uncertain call.
            counts["sent"] += confirmed
        self._detect_changes(rule_id, by_id, options)
        self._enqueue(rule_id, items, options)
        if destination_items is None and rule["duplicate_policy"] != "none":
            # Without the destination list neither duplicates nor earlier uncertain writes can be
            # checked, so nothing new is sent.
            return self._finish(
                rule, trigger, batch_id, "destination_unavailable", counts, present=set(by_id)
            )
        lost = await self._send_batch(
            rule, batch_id, by_id, destination_items or [], options, counts
        )
        completion_failed = 0
        if rule["mode"] == "move":
            completion_failed = await self._complete_sources(rule_id, by_id, counts)
        self._baseline[rule_id] = _snapshot(list(by_id.values()))
        delivered = counts["sent"] + counts["skipped_duplicates"]
        problems = counts["failed"] + counts["needs_reconciliation"]
        if lost and not delivered and not problems:
            outcome = "destination_unavailable"
        elif not delivered and not problems:
            outcome = "partial" if completion_failed else "nothing_to_send"
        elif not problems and not lost and not completion_failed:
            outcome = "sent"
        elif delivered:
            outcome = "partial"
        else:
            outcome = "failed"
        return self._finish(
            rule, trigger, batch_id, outcome, counts, confirm=confirm, present=set(by_id)
        )

    def _reconcile(self, rule_id: str, destination_items: list[dict[str, Any]]) -> tuple[int, int]:
        """Settle uncertain writes against what the destination really holds.

        Returns how many records were settled and how many of them were found at the destination.
        """
        titles: dict[str, list[str | None]] = {}
        uids: set[str] = set()
        for item in destination_items:
            titles.setdefault(normalize(item.get("summary")), []).append(item.get("uid"))
            if isinstance(item.get("uid"), str):
                uids.add(item["uid"])
        settled = confirmed = 0
        for record in self._rule_records(rule_id):
            if record["state"] != RecordState.NEEDS_RECONCILIATION or self._is_member(record):
                continue
            group = [record, *self._members(record)]
            matches = titles.get(normalize(record["title"]), [])
            if record["destination_uid"] and record["destination_uid"] in uids:
                uid = record["destination_uid"]
            elif len(matches) == 1:
                uid = matches[0]
            elif not matches:
                for member in group:
                    self._set(
                        member,
                        RecordState.PENDING,
                        last_error=None,
                        merged_into=None,
                        duplicate_of=None,
                    )
                settled += len(group)
                continue
            else:
                # Several equal lines and no stored uid: only the user can tell which is ours.
                record["last_error"] = "ambiguous"
                continue
            for member in group:
                self._set(
                    member,
                    RecordState.SENT,
                    destination_uid=uid,
                    sent_at=member["sent_at"] or _now(),
                    sent_fingerprint=member["fingerprint"],
                    last_error=None,
                )
            settled += len(group)
            confirmed += 1
        return settled, confirmed

    def _detect_changes(
        self, rule_id: str, by_id: dict[str, dict[str, Any]], options: dict[str, bool]
    ) -> None:
        """Flag edits after sending; refresh lines that were not sent yet. Never touches the
        destination."""
        for record in self._rule_records(rule_id):
            item = by_id.get(record["source_item_id"])
            state = record["state"]
            if state == RecordState.SENT:
                if item is None:
                    self._set(record, RecordState.REMOVED_AFTER_SEND)
                elif (current := fingerprint(item)) != record["sent_fingerprint"]:
                    self._set(record, RecordState.CHANGED_AFTER_SEND, fingerprint=current)
            elif state == RecordState.SOURCE_COMPLETED:
                # A deleted completed item is normal; the record becomes prunable.
                if item is not None and (current := fingerprint(item)) != record["fingerprint"]:
                    self._set(record, RecordState.CHANGED_AFTER_SEND, fingerprint=current)
            elif state in (RecordState.PENDING, RecordState.FAILED):
                mapped = map_item(item, **options) if item and not item.get("checked") else None
                if mapped is None:
                    # Never sent, and the item is gone, checked or empty: nothing to protect.
                    del self.records[record["key"]]
                    continue
                record.update(
                    title=mapped["title"],
                    description=mapped["description"],
                    fingerprint=fingerprint(item),
                )

    def _enqueue(self, rule_id: str, items: list[dict[str, Any]], options: dict[str, bool]) -> None:
        if rule_id not in self.rules:
            return
        now = _now()
        for item in items:
            item_id = item.get("id")
            if not isinstance(item_id, str) or item.get("checked"):
                continue
            key = f"{rule_id}:{item_id}"
            if key in self.records:
                continue
            mapped = map_item(item, **options)
            if mapped is None:
                continue
            record = new_record(rule_id, item_id, now)
            record.update(
                title=mapped["title"],
                description=mapped["description"],
                fingerprint=fingerprint(item),
            )
            self.records[key] = record

    async def _send_batch(
        self,
        rule: dict[str, Any],
        batch_id: str,
        by_id: dict[str, dict[str, Any]],
        destination_items: list[dict[str, Any]],
        options: dict[str, bool],
        counts: dict[str, int],
    ) -> bool:
        """Plan and send up to one batch. Returns True when the destination went away."""
        rule_id = rule["id"]
        entity_id = rule["destination_entity_id"]
        pending = sorted(
            (
                record
                for record in self._rule_records(rule_id)
                if record["state"] == RecordState.PENDING
            ),
            key=lambda record: record["enqueued_at"],
        )[:SYNC_BATCH_SIZE]
        open_titles: dict[str, str | None] = {}
        for item in destination_items:
            if item.get("status") != "needs_action":
                continue
            title = normalize(item.get("summary"))
            open_titles[title] = None if title in open_titles else item.get("uid")
        in_flight = {
            normalize(record["title"]): record["key"]
            for record in self._rule_records(rule_id)
            if record["state"] == RecordState.SENDING
        }
        lines = plan_batch(
            [(record["key"], by_id[record["source_item_id"]]) for record in pending],
            policy=rule["duplicate_policy"],
            destination_open=open_titles,
            in_flight=in_flight,
            **options,
        )
        now = _now()
        for line in lines:
            record = self.records[line["key"]]
            if line["merged_into"]:
                # Merged or duplicate of an earlier line in this batch: it stays pending and
                # shares the lead's result, so it is never confirmed before the lead is.
                record.update(merged_into=line["merged_into"], duplicate_of=line["duplicate_of"])
                continue
            record.update(
                title=line["title"],
                description=line["description"],
                merged_into=None,
                duplicate_of=None,
            )
            if line["action"] == "skip_duplicate":
                for member in [record, *(self.records[key] for key in line["members"])]:
                    self._set(
                        member,
                        RecordState.SENT,
                        duplicate_of=line["duplicate_of"] or "destination",
                        sent_at=now,
                        sent_fingerprint=member["fingerprint"],
                        batch_id=batch_id,
                    )
                counts["skipped_duplicates"] += 1 + len(line["members"])
        failures = 0
        for line in lines:
            if line["action"] != "send":
                continue
            if failures >= SYNC_MAX_LINE_FAILURES or self._stopped or rule_id not in self.rules:
                break
            state = self.hass.states.get(entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                # Home Assistant silently skips service calls to unavailable entities, so a call
                # now would look like a success. Leave the rest pending.
                return True
            group = [self.records[line["key"]], *(self.records[key] for key in line["members"])]
            for record in group:
                self._set(
                    record,
                    RecordState.SENDING,
                    batch_id=batch_id,
                    attempts=record["attempts"] + 1,
                    last_error=None,
                )
            # Persist "sending" before the call so a crash cannot hide an uncertain write.
            await self.store.async_save()
            error = await self._add_item(entity_id, line["title"], line["description"])
            for record in group:
                if error is None:
                    self._set(
                        record,
                        RecordState.SENT,
                        sent_at=_now(),
                        sent_fingerprint=record["fingerprint"],
                    )
                elif error == "destination_rejected":
                    self._set(record, RecordState.FAILED, last_error=error)
                else:
                    self._set(record, RecordState.NEEDS_RECONCILIATION, last_error=error)
            await self.store.async_save()
            if error is None:
                duplicates = sum(1 for record in group[1:] if record["duplicate_of"])
                counts["sent"] += 1
                counts["merged"] += len(group) - 1 - duplicates
                counts["skipped_duplicates"] += duplicates
                failures = 0
            else:
                counts["failed" if error == "destination_rejected" else "needs_reconciliation"] += (
                    len(group)
                )
                failures += 1
        return False

    async def _complete_sources(
        self, rule_id: str, by_id: dict[str, dict[str, Any]], counts: dict[str, int]
    ) -> int:
        """Move mode: check off items in Mealie that the destination confirmed. Returns failures."""
        failed = 0
        for record in self._rule_records(rule_id):
            if record["state"] != RecordState.SENT or rule_id not in self.rules:
                continue
            item_id = record["source_item_id"]
            try:
                current = await self.client.shopping_item(item_id)
            except (MealieError, ValueError) as exc:
                if isinstance(exc, MealieError) and exc.status == 404:
                    self._set(record, RecordState.REMOVED_AFTER_SEND, completion_pending=False)
                    by_id.pop(item_id, None)
                    continue
                self._set(
                    record,
                    RecordState.SENT,
                    completion_pending=True,
                    last_error="source_complete_failed",
                )
                failed += 1
                continue
            if (current_fp := fingerprint(current)) != record["sent_fingerprint"]:
                # Edited between send and completion: the destination holds the old version.
                self._set(
                    record,
                    RecordState.CHANGED_AFTER_SEND,
                    fingerprint=current_fp,
                    completion_pending=False,
                )
                by_id[item_id] = current
                continue
            try:
                await self.client.set_shopping_item_checked(item_id, True, current)
            except (MealieError, ValueError):
                self._set(
                    record,
                    RecordState.SENT,
                    completion_pending=True,
                    last_error="source_complete_failed",
                )
                failed += 1
                continue
            self._set(
                record,
                RecordState.SOURCE_COMPLETED,
                fingerprint=fingerprint(current, checked=True),
                completion_pending=False,
                completed_at=_now(),
                last_error=None,
            )
            by_id[item_id] = {**current, "checked": True}
            counts["completed_in_source"] += 1
        self.store.async_delay_save()
        return failed

    # Rule management

    def rule_status(self, rule: dict[str, Any]) -> dict[str, Any]:
        counts = dict.fromkeys(STATES, 0)
        for record in self._rule_records(rule["id"]):
            counts[record["state"]] += 1
        scheduled = self._scheduled.get(rule["id"])
        lock = self._locks.get(rule["id"])
        return {
            **deepcopy(rule),
            "counts": counts,
            "running": bool(lock and lock.locked()),
            "next_run_at": scheduled[1].isoformat() if scheduled else None,
            "destination": self.destination(rule["destination_entity_id"]),
        }

    def state(self) -> dict[str, Any]:
        return {
            "supported": True,
            "rules": [self.rule_status(rule) for rule in self.rules.values()],
            "audit": deepcopy(self.store.data["audit"][-20:]),
        }

    async def async_save_rule(
        self,
        draft: Any,
        *,
        rule_id: str | None,
        expected_revision: int | None,
        reset_records: bool,
        user_id: str | None,
    ) -> dict[str, Any]:
        async with self._save_lock:
            return await self._async_save_rule_locked(
                draft,
                rule_id=rule_id,
                expected_revision=expected_revision,
                reset_records=reset_records,
                user_id=user_id,
            )

    async def _async_save_rule_locked(
        self,
        draft: Any,
        *,
        rule_id: str | None,
        expected_revision: int | None,
        reset_records: bool,
        user_id: str | None,
    ) -> dict[str, Any]:
        fields = validate_draft(draft)
        existing = self._rule(rule_id) if rule_id is not None else None
        if existing is not None and expected_revision not in (None, existing["revision"]):
            raise SyncError("conflict", "The sync rule changed elsewhere. Refresh first")
        destination = self.destination(fields["destination_entity_id"])
        if self.hass.states.get(fields["destination_entity_id"]) is None or not self._usable(
            destination
        ):
            raise SyncError(
                "sync_destination_unsupported",
                "Choose a To-do list that can add items and is not a Mealie list",
            )
        if any(
            rule["source_list_id"] == fields["source_list_id"] and rule["id"] != rule_id
            for rule in self.rules.values()
        ):
            raise SyncError("sync_source_taken", "Another sync rule already uses this list")
        if existing is None and len(self.rules) >= SYNC_MAX_RULES:
            raise SyncError("sync_too_many_rules", f"At most {SYNC_MAX_RULES} sync rules")
        retarget = existing is not None and (
            existing["source_list_id"] != fields["source_list_id"]
            or existing["destination_entity_id"] != fields["destination_entity_id"]
        )
        if retarget and not reset_records:
            raise ValueError("Changing source or destination drops the sync history; confirm it")
        source = next(
            (
                row
                for row in await self.client.shopping_lists()
                if row.get("id") == fields["source_list_id"]
            ),
            None,
        )
        if source is None:
            raise ValueError("Unknown source shopping list")
        source_name = str(source.get("name") or "")[:100]
        now = _now()
        name = fields.pop("name") or f"{source_name} → {destination['name']}"[:60].strip()
        if existing is None:
            rule_id = str(uuid4())
            rule = {
                "id": rule_id,
                "revision": 1,
                "name": name,
                "source_list_name": source_name,
                **fields,
                "activated": False,
                "paused": False,
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
                "last_run": None,
            }
            self.rules[rule_id] = rule
        else:
            rule = existing
            if retarget:
                for record in self._rule_records(rule["id"]):
                    del self.records[record["key"]]
                rule["activated"] = False
                self._cancel_scheduled(rule["id"])
            rule.update(
                fields,
                name=name,
                source_list_name=source_name,
                revision=rule["revision"] + 1,
                updated_at=now,
            )
        await self.store.async_save()
        self._stop_watcher(rule["id"])
        self._refresh_watchers()
        return self.rule_status(rule)

    async def async_delete_rule(self, rule_id: str) -> None:
        self._rule(rule_id)
        del self.rules[rule_id]
        for record in self._rule_records(rule_id):
            del self.records[record["key"]]
        self._cancel_scheduled(rule_id)
        self._stop_watcher(rule_id)
        self._rerun.pop(rule_id, None)
        await self.store.async_save()

    async def async_set_paused(self, rule_id: str, paused: bool) -> dict[str, Any]:
        rule = self._rule(rule_id)
        rule.update(paused=paused, revision=rule["revision"] + 1, updated_at=_now())
        if paused:
            self._cancel_scheduled(rule_id)
        await self.store.async_save()
        self._refresh_watchers()
        return self.rule_status(rule)

    async def async_retry(self, rule_id: str, record_keys: list[str] | None) -> dict[str, Any]:
        rule = self._rule(rule_id)
        if not rule.get("activated"):
            raise SyncError("sync_not_activated", "Send the first batch before retrying")
        wanted = set(record_keys) if record_keys is not None else None
        for record in self._rule_records(rule_id):
            if record["state"] == RecordState.FAILED and (
                wanted is None or record["key"] in wanted
            ):
                self._set(
                    record,
                    RecordState.PENDING,
                    last_error=None,
                    merged_into=None,
                    duplicate_of=None,
                )
        await self.store.async_save()
        return await self.async_run(rule_id, "retry")

    async def async_resolve(self, rule_id: str, record_key: str, action: str) -> dict[str, Any]:
        self._rule(rule_id)
        record = self.records.get(record_key)
        if record is None or record["rule_id"] != rule_id:
            raise ValueError("Unknown sync record")
        if record["state"] not in (
            RecordState.CHANGED_AFTER_SEND,
            RecordState.REMOVED_AFTER_SEND,
            RecordState.NEEDS_RECONCILIATION,
        ):
            raise ValueError("Only conflicts and uncertain items can be resolved")
        group = [record, *self._members(record)]
        deleted = False
        for member in group:
            if action == "resend":
                self._set(
                    member,
                    RecordState.PENDING,
                    sent_fingerprint=None,
                    merged_into=None,
                    duplicate_of=None,
                    destination_uid=None,
                    last_error=None,
                    completion_pending=False,
                    sent_at=None,
                    completed_at=None,
                )
            elif member["state"] == RecordState.REMOVED_AFTER_SEND:
                del self.records[member["key"]]
                deleted = True
            elif member["completed_at"]:
                # Deliberate deviation from the contract (which says `sent`): the item was already
                # checked off in Mealie once, so `sent` would make a move rule check it off again.
                # Accept the current Mealie state as the new baseline instead.
                self._set(member, RecordState.SOURCE_COMPLETED, last_error=None)
            else:
                self._set(
                    member,
                    RecordState.SENT,
                    sent_fingerprint=member["fingerprint"],
                    sent_at=member["sent_at"] or _now(),
                    last_error=None,
                )
        await self.store.async_save()
        result: dict[str, Any] = {"record": public_record(record)}
        if deleted:
            result["deleted"] = True
        return result

    def list_records(
        self, rule_id: str, states: list[str] | None, limit: int
    ) -> list[dict[str, Any]]:
        self._rule(rule_id)
        rows = [
            record
            for record in self._rule_records(rule_id)
            if states is None or record["state"] in states
        ]
        rows.sort(key=lambda record: record["updated_at"], reverse=True)
        return [public_record(record) for record in rows[:limit]]

    async def async_preview(
        self, *, rule_id: str | None = None, draft: Any = None
    ) -> dict[str, Any]:
        """Show the first batch of a rule or draft without writing anything."""
        if rule_id is not None:
            fields = self._rule(rule_id)
        else:
            fields = validate_draft(draft)
        entity_id = fields["destination_entity_id"]
        destination = self.destination(entity_id)
        if self.hass.states.get(entity_id) is None or not self._usable(destination):
            raise SyncError(
                "sync_destination_unsupported",
                "Choose a To-do list that can add items and is not a Mealie list",
            )
        language = (self.hass.config.language or "en").lower()
        options = {
            "decimal_comma": language.startswith("de"),
            "supports_description": destination["supports"]["description"],
        }
        items = await self.client.shopping_items(fields["source_list_id"])
        candidates = []
        for item in items:
            item_id = item.get("id")
            if not isinstance(item_id, str) or item.get("checked"):
                continue
            record = self.records.get(f"{rule_id}:{item_id}") if rule_id else None
            if record is None or record["state"] == RecordState.PENDING:
                candidates.append(item)
        open_titles: dict[str, str | None] = {}
        for item in await self._destination_items(entity_id) or []:
            if item.get("status") == "needs_action":
                open_titles[normalize(item.get("summary"))] = item.get("uid")
        lines = plan_batch(
            [(item["id"], item) for item in candidates[:SYNC_BATCH_SIZE]],
            policy=fields["duplicate_policy"],
            destination_open=open_titles,
            in_flight={},
            **options,
        )
        sample_line = next((line for line in lines if line["action"] != "skip_empty"), None)
        if sample_line is not None:
            first = next(item for item in candidates if item["id"] == sample_line["key"])
            sample = map_item(first, **options)
        else:
            german = options["decimal_comma"]
            sample = map_item(
                {
                    "quantity": 300,
                    "unit": {"name": "g", "abbreviation": "g", "useAbbreviation": True},
                    "food": {"name": "Kartoffeln" if german else "potatoes"},
                    "note": "festkochend" if german else "waxy",
                },
                **options,
            )
        return {
            "capabilities": destination["supports"],
            "sample": sample,
            "lines": [
                {
                    "source_item_id": line["key"],
                    "title": line["title"],
                    "description": line["description"],
                    "action": line["action"],
                    **({"merged_into": line["merged_into"]} if line["merged_into"] else {}),
                }
                for line in lines
            ],
            "total_candidates": len(candidates),
        }

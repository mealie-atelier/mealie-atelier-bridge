"""Shapes of sync rules, outbox records and audit entries, plus rule draft validation."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from ..api import safe_id


class RecordState(StrEnum):
    """Lifecycle of one source item in the outbox."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    SOURCE_COMPLETED = "source_completed"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    CHANGED_AFTER_SEND = "changed_after_send"
    REMOVED_AFTER_SEND = "removed_after_send"


STATES = tuple(state.value for state in RecordState)
CONFLICT_STATES = frozenset({RecordState.CHANGED_AFTER_SEND, RecordState.REMOVED_AFTER_SEND})
# Records the user must look at; pruning never drops them.
PROTECTED_STATES = frozenset(
    {
        RecordState.PENDING,
        RecordState.SENDING,
        RecordState.FAILED,
        RecordState.NEEDS_RECONCILIATION,
        *CONFLICT_STATES,
    }
)
TERMINAL_STATES = frozenset({RecordState.SENT, RecordState.SOURCE_COMPLETED})

MODES = ("copy", "move")
DUPLICATE_POLICIES = ("skip_exact", "skip_exact_merge_same_unit", "none")
TRIGGERS = ("manual", "after_atelier_add", "watch", "retry", "startup")
OUTCOMES = (
    "sent",
    "partial",
    "failed",
    "destination_unavailable",
    "source_unavailable",
    "source_missing",
    "nothing_to_send",
    "reconciled",
)

DEBOUNCE_RANGE = (5, 600)
POLL_RANGE = (60, 3600)
NAME_MAX = 60
_TODO_ENTITY = re.compile(r"todo\.[a-z0-9_]{1,250}", re.ASCII)


class SyncError(Exception):
    """A sync failure with a stable error code from the shared error table."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _int_in_range(value: Any, low: int, high: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{field} must be a whole number from {low} to {high}")
    return value


def validate_draft(draft: Any) -> dict[str, Any]:
    """Return the normalised rule fields of a draft or raise ValueError."""
    if not isinstance(draft, dict):
        raise ValueError("rule must be an object")  # noqa: TRY004
    allowed = {
        "name",
        "source_list_id",
        "destination_entity_id",
        "mode",
        "triggers",
        "debounce_seconds",
        "poll_interval_seconds",
        "duplicate_policy",
    }
    if unknown := set(draft) - allowed:
        raise ValueError(f"Unknown rule fields: {', '.join(sorted(unknown))}"[:200])
    source = safe_id(draft.get("source_list_id"))
    entity_id = draft.get("destination_entity_id")
    if not isinstance(entity_id, str) or not _TODO_ENTITY.fullmatch(entity_id):
        raise ValueError("destination_entity_id must be a todo entity")
    name = draft.get("name")
    if name is not None:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_MAX:
            raise ValueError(f"name must have 1 to {NAME_MAX} characters")
        name = name.strip()
    mode = draft.get("mode", "copy")
    if mode not in MODES:
        raise ValueError("mode must be copy or move")
    policy = draft.get("duplicate_policy", "skip_exact")
    if policy not in DUPLICATE_POLICIES:
        raise ValueError("Unknown duplicate_policy")
    triggers = draft.get("triggers", {})
    if not isinstance(triggers, dict) or set(triggers) - {"after_atelier_add", "watch"}:
        raise ValueError("triggers accepts after_atelier_add and watch")
    after_add = triggers.get("after_atelier_add", True)
    watch = triggers.get("watch", False)
    if not isinstance(after_add, bool) or not isinstance(watch, bool):
        raise ValueError("triggers must be booleans")
    return {
        "name": name,
        "source_list_id": source,
        "destination_entity_id": entity_id,
        "mode": mode,
        "triggers": {"after_atelier_add": after_add, "watch": watch},
        "debounce_seconds": _int_in_range(
            draft.get("debounce_seconds", 30), *DEBOUNCE_RANGE, "debounce_seconds"
        ),
        "poll_interval_seconds": _int_in_range(
            draft.get("poll_interval_seconds", 120), *POLL_RANGE, "poll_interval_seconds"
        ),
        "duplicate_policy": policy,
    }


def new_record(rule_id: str, item_id: str, now: str) -> dict[str, Any]:
    """A fresh pending outbox record; title and fingerprint are filled by the caller."""
    return {
        "key": f"{rule_id}:{item_id}",
        "rule_id": rule_id,
        "source_item_id": item_id,
        "state": RecordState.PENDING.value,
        "title": "",
        "description": None,
        "fingerprint": "",
        "sent_fingerprint": None,
        "merged_into": None,
        "duplicate_of": None,
        "destination_uid": None,
        "batch_id": None,
        "attempts": 0,
        "last_error": None,
        "completion_pending": False,
        "enqueued_at": now,
        "sent_at": None,
        "completed_at": None,
        "updated_at": now,
    }

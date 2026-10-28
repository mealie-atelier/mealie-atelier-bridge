"""Pure mapping of Mealie shopping items to destination To-do lines.

No Home Assistant imports and no I/O, so every rule here is unit tested directly.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

TITLE_MAX = 255
NOTE_IN_TITLE_MAX = 60
DESCRIPTION_MAX = 1000
_FRACTIONS = ((1 / 4, "1/4"), (1 / 3, "1/3"), (1 / 2, "1/2"), (2 / 3, "2/3"), (3 / 4, "3/4"))
_SPACES = re.compile(r"\s+")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def _text(value: Any) -> str:
    return _SPACES.sub(" ", value).strip() if isinstance(value, str) else ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def quantity_of(item: dict[str, Any]) -> float | None:
    """Numeric quantity, or None when absent, zero or not a number."""
    value = item.get("quantity")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if value > 0 else None


def format_quantity(value: float, *, fraction: bool, decimal_comma: bool) -> str:
    """Render a quantity without unit conversion: `1 1/2`, `0,25` or `300`."""
    if fraction:
        whole = math.floor(value)
        rest = value - whole
        for target, text in _FRACTIONS:
            if abs(rest - target) <= 0.01:
                return f"{whole} {text}" if whole else text
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if decimal_comma else text


def _food(item: dict[str, Any]) -> dict[str, Any] | None:
    food = item.get("food")
    return food if isinstance(food, dict) and _text(food.get("name")) else None


def _unit(item: dict[str, Any]) -> dict[str, Any] | None:
    unit = item.get("unit")
    return unit if isinstance(unit, dict) else None


def unit_text(unit: dict[str, Any] | None, quantity: float | None) -> str:
    if not unit:
        return ""
    if unit.get("useAbbreviation") and _text(unit.get("abbreviation")):
        return _text(unit["abbreviation"])
    if quantity is not None and quantity > 1 and _text(unit.get("pluralName")):
        return _text(unit["pluralName"])
    return _text(unit.get("name"))


def map_item(
    item: dict[str, Any],
    *,
    decimal_comma: bool,
    supports_description: bool,
    quantity: float | None | bool = False,
) -> dict[str, Any] | None:
    """Return `{"title", "description"}` for one source item, or None when it has no text.

    `quantity` overrides the item's own amount (used for merged lines); `False` means no override.
    """
    amount = quantity_of(item) if quantity is False else quantity
    food = _food(item)
    note = _text(item.get("note"))
    extra = ""
    if food:
        plural = _text(food.get("pluralName"))
        name = plural if amount is not None and amount > 1 and plural else _text(food["name"])
        extra = note
    elif note:
        name = note
    else:
        # Mealie's display text already carries quantity and unit, so it is used as is
        # instead of prefixing them a second time.
        display = _text(item.get("display"))
        if not display:
            return None
        return {"title": _truncate(display, TITLE_MAX), "description": None}
    unit = _unit(item)
    parts = [
        format_quantity(
            amount,
            fraction=bool(unit and unit.get("fraction")),
            decimal_comma=decimal_comma,
        )
        if amount is not None
        else "",
        unit_text(unit, amount),
        name,
    ]
    title = " ".join(part for part in parts if part)
    description = None
    if extra:
        if supports_description:
            description = _truncate(extra, DESCRIPTION_MAX)
        else:
            # The note must survive even when the destination has no description field.
            title = f"{title} ({_truncate(extra, NOTE_IN_TITLE_MAX)})"
    return {"title": _truncate(title, TITLE_MAX), "description": description}


def normalize(text: Any) -> str:
    """Comparison form of a title: NFKC, casefolded, single spaces, `1,5` equals `1.5`."""
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _DECIMAL_COMMA.sub(".", _SPACES.sub(" ", folded).strip())


def _ref_id(item: dict[str, Any], key: str, nested: str) -> str | None:
    value = item.get(key)
    if isinstance(value, str) and value:
        return value
    obj = item.get(nested)
    return obj.get("id") if isinstance(obj, dict) and isinstance(obj.get("id"), str) else None


def fingerprint(item: dict[str, Any], *, checked: bool | None = None) -> str:
    """SHA-256 over the fields that define what was sent; independent of key order."""
    food = item.get("food") if isinstance(item.get("food"), dict) else {}
    unit = _unit(item) or {}
    quantity = item.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        quantity = None
    canonical = {
        "food_id": _ref_id(item, "foodId", "food"),
        "food_name": _text(food.get("name")) or None,
        "note": _text(item.get("note")) or None,
        "quantity": float(quantity) if quantity is not None else None,
        "unit_id": _ref_id(item, "unitId", "unit"),
        "unit_name": _text(unit.get("name")) or None,
        "checked": bool(item.get("checked")) if checked is None else checked,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def merge_key(item: dict[str, Any]) -> tuple[str, str, str | None] | None:
    """Lines merge only with the same food (or note), the same note and the same unit.

    Requiring an equal note too keeps a note such as "festkochend" from vanishing in a merge.
    """
    if quantity_of(item) is None:
        return None
    note = normalize(item.get("note"))
    food_id = _ref_id(item, "foodId", "food") if _food(item) else None
    if food_id:
        return (f"food:{food_id}", note, _ref_id(item, "unitId", "unit"))
    if note:
        return ("note", note, _ref_id(item, "unitId", "unit"))
    return None


def plan_batch(
    entries: list[tuple[str, dict[str, Any]]],
    *,
    policy: str,
    decimal_comma: bool,
    supports_description: bool,
    destination_open: dict[str, str | None],
    in_flight: dict[str, str],
) -> list[dict[str, Any]]:
    """Plan one batch in order: what to send, merge, skip as duplicate or skip as empty.

    `destination_open` maps normalised titles of open destination items to their uid (None when
    unknown or ambiguous); `in_flight` maps normalised titles of other records of the rule that are
    being sent to their record key. A duplicate of an earlier line in the same batch joins that
    line as a member (`merged_into`), so it shares the lead's result instead of counting as
    delivered before the lead was accepted.
    """
    lines: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    groups: dict[tuple, dict[str, Any]] = {}
    for key, item in entries:
        mapped = map_item(
            item, decimal_comma=decimal_comma, supports_description=supports_description
        )
        line = {
            "key": key,
            "title": mapped["title"] if mapped else "",
            "description": mapped["description"] if mapped else None,
            "action": "send" if mapped else "skip_empty",
            "merged_into": None,
            "members": [],
            "duplicate_of": None,
        }
        lines.append(line)
        by_key[key] = line
        if not mapped or policy != "skip_exact_merge_same_unit":
            continue
        group_key = merge_key(item)
        if group_key is None:
            continue
        lead = groups.get(group_key)
        if lead is None:
            groups[group_key] = {"key": key, "item": item, "total": quantity_of(item)}
            continue
        lead["total"] += quantity_of(item) or 0
        line["action"] = "merge"
        line["merged_into"] = lead["key"]
        by_key[lead["key"]]["members"].append(key)
        merged = map_item(
            lead["item"],
            decimal_comma=decimal_comma,
            supports_description=supports_description,
            quantity=round(lead["total"], 6),
        )
        by_key[lead["key"]]["title"] = merged["title"]
    if policy == "none":
        return lines
    planned: dict[str, str] = {}
    for line in lines:
        if line["action"] != "send":
            continue
        title = normalize(line["title"])
        if title in destination_open:
            line["action"] = "skip_duplicate"
            line["duplicate_of"] = destination_open[title]
        elif title in in_flight:
            line["action"] = "skip_duplicate"
            line["duplicate_of"] = in_flight[title]
        elif title in planned:
            lead = by_key[planned[title]]
            line["action"] = "skip_duplicate"
            line["duplicate_of"] = lead["key"]
            line["merged_into"] = lead["key"]
            lead["members"].extend([line["key"], *line["members"]])
            for member in line["members"]:
                by_key[member]["merged_into"] = lead["key"]
            line["members"] = []
        else:
            planned[title] = line["key"]
    return lines

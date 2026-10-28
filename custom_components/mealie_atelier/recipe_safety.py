"""Conservative recipe ingredient transactions with optimistic change detection."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from uuid import UUID, uuid4

_ID = re.compile(r"^[a-zA-Z0-9_-]{1,150}$")


def fingerprint(recipe: dict) -> str:
    rows = recipe.get("recipeIngredient")
    if not isinstance(rows, list):
        raise ValueError("Invalid recipe response")  # noqa: TRY004
    payload = json.dumps(
        {"slug": recipe.get("slug"), "ingredients": rows}, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preview(recipe: dict) -> dict:
    return {
        "slug": recipe.get("slug"),
        "expected_hash": fingerprint(recipe),
        "rows": [
            {
                "referenceId": r.get("referenceId"),
                "quantity": r.get("quantity"),
                "food": {k: r["food"].get(k) for k in ("id", "name")} if r.get("food") else None,
                "unit": {k: r["unit"].get(k) for k in ("id", "name")} if r.get("unit") else None,
                "note": r.get("note") or "",
                "originalText": r.get("originalText") or "",
                "display": r.get("display") or "",
                "title": r.get("title") or "",
            }
            for r in recipe["recipeIngredient"]
        ],
    }


def _uuid(v):
    try:
        return bool(UUID(v)) and isinstance(v, str)
    except (TypeError, ValueError, AttributeError):
        return False


def _text(v, limit):
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError("Invalid ingredient text")
    return v.strip()


async def merge(recipe: dict, draft: list, resolver):
    if not isinstance(draft, list) or not 1 <= len(draft) <= 100:
        raise ValueError("Expected 1-100 ingredient rows")
    old = {}
    for row in recipe.get("recipeIngredient", []):
        ref = row.get("referenceId")
        if not _uuid(ref) or ref in old:
            raise ValueError("Corrupted existing referenceId; edit in Mealie")
        old[ref] = row
    result = []
    seen = set()
    for input_row in draft:
        if not isinstance(input_row, dict):
            raise ValueError("Invalid ingredient row")  # noqa: TRY004
        ref = input_row.get("referenceId") or str(uuid4())
        if not _uuid(ref) or ref in seen or input_row.get("referenceId") and ref not in old:
            raise ValueError("Invalid or duplicate referenceId")
        seen.add(ref)
        row = copy.deepcopy(
            old.get(
                ref,
                {
                    "referenceId": ref,
                    "quantity": None,
                    "food": None,
                    "unit": None,
                    "originalText": "",
                    "display": "",
                    "note": "",
                },
            )
        )
        if "quantity" in input_row:
            value = input_row["quantity"]
            if value is None or value == "":
                row["quantity"] = None
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ValueError("Invalid quantity")
                if not 0 <= value <= 1_000_000 or not math.isfinite(value):
                    raise ValueError("Invalid quantity")
                row["quantity"] = value
        for key in ("note", "originalText", "display", "title"):
            if key in input_row:
                row[key] = _text(
                    input_row[key], 1000 if key in ("originalText", "display") else 300
                )
        for input_key, kind in (("food_id", "food"), ("unit_id", "unit")):
            if input_key in input_row:
                key = input_row[input_key]
                if key is None or key == "":
                    row[kind] = None
                else:
                    if not isinstance(key, str) or not _ID.fullmatch(key):
                        raise ValueError("Invalid linked item id")
                    linked = await resolver(kind, key)
                    if not isinstance(linked, dict) or linked.get("id") != key:
                        raise ValueError("Unknown linked item")
                    row[kind] = linked
        if (
            not row.get("food")
            and not str(
                row.get("originalText") or row.get("display") or row.get("note") or ""
            ).strip()
        ):
            raise ValueError("An unlinked ingredient needs descriptive text")
        result.append(row)
    return result


def verified(expected: list, actual: dict) -> bool:
    rows = actual.get("recipeIngredient")
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    for a, b in zip(expected, rows):
        if a.get("referenceId") != b.get("referenceId") or a.get("quantity") != b.get("quantity"):
            return False
        for key in ("food", "unit"):
            if (a.get(key) or {}).get("id") != (b.get(key) or {}).get("id"):
                return False
    return True

"""Pure unit tests for guarded structured ingredients (no live Mealie writes)."""

import asyncio
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest

path = Path(__file__).parents[1] / "custom_components/mealie_atelier/recipe_safety.py"
spec = spec_from_file_location("atelier_recipe_safety", path)
mod = module_from_spec(spec)
spec.loader.exec_module(mod)
fingerprint, preview, merge, verified = mod.fingerprint, mod.preview, mod.merge, mod.verified

REFERENCE = str(uuid4())
RECIPE = {
    "slug": "test",
    "recipeIngredient": [
        {
            "referenceId": REFERENCE,
            "quantity": 250.0,
            "food": {"id": "food-1", "name": "Tomaten"},
            "unit": {"id": "unit-1", "name": "g"},
            "note": "",
            "originalText": "250 g Tomaten",
            "customData": {"kept": True},
        }
    ],
}


async def resolver(kind, key):
    return {"id": key, "name": kind}


def test_preview_hash_is_stable_and_detects_external_changes():
    current = deepcopy(RECIPE)
    p = preview(current)
    assert p["expected_hash"] == fingerprint(current)
    assert p["rows"][0]["food"]["id"] == "food-1"
    current["recipeIngredient"][0]["quantity"] = 300.0
    assert p["expected_hash"] != fingerprint(current)


def test_merge_preserves_unrelated_fields_and_existing_reference():
    new = asyncio.run(
        merge(RECIPE, [{"referenceId": REFERENCE, "quantity": 180.0, "note": "weniger"}], resolver)
    )
    assert new[0]["referenceId"] == REFERENCE
    assert new[0]["customData"] == {"kept": True}
    assert new[0]["food"]["id"] == "food-1"
    assert RECIPE["recipeIngredient"][0]["quantity"] == 250.0
    assert verified(new, {"recipeIngredient": new})
    different = deepcopy(new)
    different[0]["unit"]["id"] = "other"
    assert not verified(new, {"recipeIngredient": different})


def test_invalid_ids_and_missing_descriptions_fail_closed():
    with pytest.raises(ValueError):
        asyncio.run(merge(RECIPE, [{"referenceId": str(uuid4()), "quantity": 5}], resolver))
    with pytest.raises(ValueError):
        asyncio.run(merge(RECIPE, [{"referenceId": REFERENCE, "quantity": float("nan")}], resolver))
    with pytest.raises(ValueError):
        asyncio.run(merge({"slug": "empty", "recipeIngredient": []}, [{"quantity": 5}], resolver))

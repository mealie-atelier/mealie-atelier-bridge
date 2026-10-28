"""Pure mapping rules: Mealie shopping item to destination line, fingerprints, duplicates, merging."""

from __future__ import annotations

import pytest

from custom_components.mealie_atelier.shopping_sync.mapping import (
    fingerprint,
    format_quantity,
    map_item,
    normalize,
    plan_batch,
)

from .sync_support import food, mealie_item, unit

GRAM = unit("gram", abbreviation="g")
CUP = unit("cup", plural="cups", fraction=True)
PIECE = unit("piece", plural="pieces")
POTATOES = food("Kartoffeln")
EGG = food("Egg", plural="Eggs")


def title(item, *, de=False, description=True):
    return map_item(item, decimal_comma=de, supports_description=description)


def test_linked_food_with_abbreviated_unit():
    item = mealie_item("1", food=POTATOES, unit=GRAM, quantity=300, note="festkochend")
    assert title(item) == {"title": "300 g Kartoffeln", "description": "festkochend"}


def test_note_is_appended_when_destination_has_no_description():
    item = mealie_item("1", food=POTATOES, unit=GRAM, quantity=300, note="festkochend " * 10)
    mapped = title(item, description=False)
    assert mapped["description"] is None
    assert mapped["title"].startswith("300 g Kartoffeln (festkochend")
    assert mapped["title"].endswith("…)")
    assert len(mapped["title"]) <= 300 + 60


def test_note_only_item_uses_note_as_name():
    assert title(mealie_item("1", note="Backpapier", quantity=1))["title"] == "1 Backpapier"
    assert title(mealie_item("1", note="Backpapier", quantity=None))["title"] == "Backpapier"
    assert title(mealie_item("1", note="Backpapier", quantity=0))["title"] == "Backpapier"


def test_display_fallback_and_empty_items():
    item = mealie_item("1", quantity=2, display="2 Dosen Tomaten")
    assert title(item) == {"title": "2 Dosen Tomaten", "description": None}
    assert title(mealie_item("1", quantity=2)) is None


@pytest.mark.parametrize(
    ("quantity", "fraction", "de", "expected"),
    [
        (1.5, True, False, "1 1/2"),
        (0.25, True, False, "1/4"),
        (2.334, True, False, "2 1/3"),
        (0.75, True, True, "3/4"),
        (1.5, False, False, "1.5"),
        (1.5, False, True, "1,5"),
        (0.125, False, True, "0,125"),
        (2.0, False, False, "2"),
        (1.23456, False, False, "1.235"),
        (1.1, True, False, "1.1"),
    ],
)
def test_quantity_formatting(quantity, fraction, de, expected):
    assert format_quantity(quantity, fraction=fraction, decimal_comma=de) == expected


def test_unit_and_food_plurals_follow_quantity():
    assert title(mealie_item("1", food=EGG, unit=PIECE, quantity=1))["title"] == "1 piece Egg"
    assert title(mealie_item("1", food=EGG, unit=PIECE, quantity=3))["title"] == "3 pieces Eggs"
    assert title(mealie_item("1", food=EGG, unit=CUP, quantity=1.5))["title"] == "1 1/2 cups Eggs"
    assert title(mealie_item("1", food=EGG, unit=GRAM, quantity=500))["title"] == "500 g Eggs"


def test_title_is_capped():
    item = mealie_item("1", note="x" * 400, quantity=None)
    assert len(title(item)["title"]) == 255


def test_fingerprint_is_stable_and_sensitive_to_every_field():
    item = mealie_item("1", food=POTATOES, unit=GRAM, quantity=300, note="festkochend")
    reordered = dict(reversed(list(item.items())))
    assert fingerprint(item) == fingerprint(reordered)
    variants = [
        {"food": food("Möhren"), "foodId": "food-möhren"},
        {"food": {**POTATOES, "name": "Kartoffel"}},
        {"note": "mehlig"},
        {"quantity": 400},
        {"unit": unit("kilogram", abbreviation="kg"), "unitId": "unit-kilogram"},
        {"unit": {**GRAM, "name": "gramm"}},
        {"checked": True},
    ]
    for change in variants:
        assert fingerprint({**item, **change}) != fingerprint(item), change
    assert fingerprint(item, checked=True) == fingerprint({**item, "checked": True})


def test_normalize():
    assert normalize("  1,5  KG\tÄpfel ") == "1.5 kg äpfel"
    assert normalize("Ｍｉｌｃｈ") == "milch"
    assert normalize("Salz, Pfeffer") == "salz, pfeffer"


def plan(entries, policy="skip_exact", destination=None, in_flight=None):
    return plan_batch(
        entries,
        policy=policy,
        decimal_comma=False,
        supports_description=True,
        destination_open=destination or {},
        in_flight=in_flight or {},
    )


def test_exact_duplicates_are_skipped_but_similar_lines_are_sent():
    entries = [
        ("a", mealie_item("a", food=POTATOES, unit=GRAM, quantity=300)),
        ("b", mealie_item("b", food=POTATOES, unit=GRAM, quantity=301)),
        ("c", mealie_item("c", note="Milch")),
        ("d", mealie_item("d", note="milch")),
        ("e", mealie_item("e")),
    ]
    lines = plan(entries, destination={"300 g kartoffeln": "uid-1"})
    assert [line["action"] for line in lines] == [
        "skip_duplicate",
        "send",
        "send",
        "skip_duplicate",
        "skip_empty",
    ]
    assert lines[0]["duplicate_of"] == "uid-1"
    assert lines[3]["duplicate_of"] == "c"
    # An in-batch duplicate shares the lead's result instead of counting as delivered.
    assert lines[3]["merged_into"] == "c"
    assert lines[2]["members"] == ["d"]
    assert lines[0]["merged_into"] is None


def test_in_flight_record_counts_as_duplicate_and_none_policy_sends_everything():
    entries = [("a", mealie_item("a", note="Milch")), ("b", mealie_item("b", note="Milch"))]
    lines = plan(entries, in_flight={"1 milch": "rule:x"})
    assert [line["duplicate_of"] for line in lines] == ["rule:x", "rule:x"]
    lines = plan(entries, policy="none", destination={"1 milch": None})
    assert [line["action"] for line in lines] == ["send", "send"]


def test_merge_only_same_food_same_unit_with_quantities():
    entries = [
        ("a", mealie_item("a", food=POTATOES, unit=GRAM, quantity=300)),
        ("b", mealie_item("b", food=POTATOES, unit=GRAM, quantity=200)),
        ("c", mealie_item("c", food=POTATOES, unit=PIECE, quantity=2)),
        ("d", mealie_item("d", food=POTATOES, unit=GRAM, quantity=None)),
        ("e", mealie_item("e", food=POTATOES, unit=GRAM, quantity=50, note="mehlig")),
    ]
    lines = plan(entries, policy="skip_exact_merge_same_unit")
    by_key = {line["key"]: line for line in lines}
    assert by_key["a"]["title"] == "500 g Kartoffeln"
    assert by_key["a"]["members"] == ["b"]
    assert by_key["b"]["action"] == "merge"
    assert by_key["b"]["merged_into"] == "a"
    assert by_key["c"]["action"] == "send"
    assert by_key["d"]["action"] == "send"
    assert by_key["d"]["title"] == "g Kartoffeln"
    assert by_key["e"]["action"] == "send"
    assert by_key["e"]["description"] == "mehlig"


def test_merge_never_happens_under_plain_skip_exact():
    entries = [
        ("a", mealie_item("a", food=POTATOES, unit=GRAM, quantity=300)),
        ("b", mealie_item("b", food=POTATOES, unit=GRAM, quantity=200)),
    ]
    assert [line["action"] for line in plan(entries)] == ["send", "send"]

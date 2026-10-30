"""Wording rules for the Bridge translations."""

from __future__ import annotations

import json
import re
from pathlib import Path

DE = Path(__file__).resolve().parents[1] / "custom_components/mealie_atelier/translations/de.json"


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)


def test_german_has_no_comma_next_to_und() -> None:
    texts = _strings(json.loads(DE.read_text(encoding="utf-8")))
    assert [t for t in texts if re.search(r",\s+und\b|\bund,", t)] == []


def test_german_sync_errors_use_the_card_terms() -> None:
    exceptions = json.loads(DE.read_text(encoding="utf-8"))["exceptions"]
    assert "ersten Stapel" in exceptions["sync_not_activated"]["message"]
    assert "Einkaufs-Sync" in exceptions["sync_rule_not_found"]["message"]

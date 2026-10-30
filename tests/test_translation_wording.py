"""Wording rules for the Bridge translations."""

from __future__ import annotations

import json
import re
from pathlib import Path

DE = Path(__file__).resolve().parents[1] / "custom_components/mealie_atelier/translations/de.json"
EN = DE.with_name("en.json")


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


def test_german_calls_a_credential_a_schluessel_everywhere() -> None:
    texts = _strings(json.loads(DE.read_text(encoding="utf-8")))
    assert [t for t in texts if re.search(r"(?<![/\w])(?:[\w-]+-)?Keys?\b", t)] == []


def test_both_languages_describe_the_mealie_integration_as_built_in() -> None:
    en = json.loads(EN.read_text(encoding="utf-8"))["config"]["step"]["user"]["description"]
    de = json.loads(DE.read_text(encoding="utf-8"))["config"]["step"]["user"]["description"]
    assert "built-in Mealie integration" in en
    assert "eingebaute Mealie-Integration" in de


def test_read_only_option_text_names_timers_and_journal() -> None:
    for path, words in ((EN, ("timers", "journal")), (DE, ("Timern", "Journal"))):
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data["options"]["step"]["init"]["data_description"]["read_only"]
        assert all(word in text for word in words), path.name

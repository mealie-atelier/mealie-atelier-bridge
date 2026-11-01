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
    for path, words in ((EN, ("timers", "journal")), (DE, ("timern", "journal"))):
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data["options"]["step"]["init"]["data_description"]["read_only"]
        assert all(word in text.lower() for word in words), path.name


def test_german_read_only_help_keeps_the_english_limitation():
    de = json.loads(DE.read_text())["options"]["step"]["init"]["data_description"]["read_only"]
    assert "aber die Timer" in de
    assert "denn die Timer" not in de


def test_german_setup_description_says_it_does_not_replace_the_core_integration():
    de = json.loads(DE.read_text())["config"]["step"]["user"]["description"]
    assert de.endswith("Mealie-Integration und ersetzt sie nicht.")


def test_dialog_titles_are_short_enough_for_one_line() -> None:
    expected = {
        EN: {"reconfigure": "Reconfigure Bridge", "reauth_confirm": "Re-authenticate Bridge"},
        DE: {"reconfigure": "Bridge neu konfigurieren", "reauth_confirm": "Bridge neu anmelden"},
    }
    for path, titles in expected.items():
        steps = json.loads(path.read_text(encoding="utf-8"))["config"]["step"]
        assert {key: steps[key]["title"] for key in titles} == titles
    strings = EN.parent.parent / "strings.json"
    assert strings.read_bytes() == EN.read_bytes()


def test_german_key_removal_says_entfernen_like_its_help_text() -> None:
    """The key help says "Zum Entfernen", EN says "Remove": the checkbox and its hint must not switch to "löschen"."""
    steps = json.loads(DE.read_text(encoding="utf-8"))

    def clear_texts(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("clear_fdc_key", "clear_ai_key") and isinstance(value, str):
                    yield value
                else:
                    yield from clear_texts(value)

    texts = list(clear_texts(steps))
    assert len(texts) >= 16
    assert [t for t in texts if "löschen" in t] == []
    assert all("entfernen" in t for t in texts)

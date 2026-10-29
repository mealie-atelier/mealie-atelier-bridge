"""The German terms of the Bridge and of the cards must not drift apart.

The test needs the cards repository next to this one (or `MEALIE_ATELIER_CARDS_DIR`). Without it
the test is skipped, so a lone checkout still passes.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARDS = Path(os.environ.get("MEALIE_ATELIER_CARDS_DIR", ROOT.parent / "mealie-atelier-cards"))
BRIDGE_DE = ROOT / "custom_components" / "mealie_atelier" / "translations" / "de.json"
CARDS_DE = CARDS / "src" / "i18n" / "de.json"

# One German term per concept in both repositories: the forbidden variant and the term to use.
TERMS = [
    (r"\bSicherung", "Backup"),
    (
        r"\b(?i:(der|den|dem|des|einen|einem|keinen|dein|deinen|deinem|diesen|diesem|jeden|jeder|"
        r"seinen|ihren|unseren|welchen|mein|meinen)) (API-)?Token(s)?\b",
        "das Token",
    ),
    (r"\bAPI-Tokens\b", "API-Token"),
]


def _texts(node: object) -> list[str]:
    if isinstance(node, dict):
        return [text for value in node.values() for text in _texts(value)]
    return [node] if isinstance(node, str) else []


@pytest.mark.skipif(not CARDS_DE.is_file(), reason="cards repository not found next to the Bridge")
@pytest.mark.parametrize(("pattern", "term"), TERMS)
def test_german_terms_agree_between_bridge_and_cards(pattern: str, term: str) -> None:
    for name, path in (("Bridge", BRIDGE_DE), ("cards", CARDS_DE)):
        for text in _texts(json.loads(path.read_text())):
            assert not re.search(pattern, text), f"{name} must say {term!r}: {text}"


@pytest.mark.skipif(not CARDS_DE.is_file(), reason="cards repository not found next to the Bridge")
def test_both_repositories_use_the_backup_term() -> None:
    for path in (BRIDGE_DE, CARDS_DE):
        assert any("Backup" in text for text in _texts(json.loads(path.read_text())))


BRIDGE_DIR = ROOT / "custom_components" / "mealie_atelier" / "translations"
CARDS_DIR = CARDS / "src" / "i18n"
# Shopping codes the Bridge sends and the cards show: both repositories tell the same first sentence.
SHARED_CODES = [
    ("offline_conflict", "errOfflineConflict"),
    ("shopping_item_not_found", "errShoppingItemNotFound"),
]


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


@pytest.mark.skipif(not CARDS_DE.is_file(), reason="cards repository not found next to the Bridge")
@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize(("code", "key"), SHARED_CODES)
def test_shopping_error_codes_use_the_same_wording_in_both_repositories(
    language: str, code: str, key: str
) -> None:
    bridge = json.loads((BRIDGE_DIR / f"{language}.json").read_text())["exceptions"][code][
        "message"
    ]
    cards = json.loads((CARDS_DIR / f"{language}.json").read_text())[key]
    assert _first_sentence(bridge) == _first_sentence(cards)


@pytest.mark.parametrize(
    "text",
    [
        "Der Token ist abgelaufen",
        "Prüfe deinen Token",
        "Jeden API-Token prüfen",
        "Mit dem Token geht es",
        "Der API-Token fehlt",
    ],
)
def test_masculine_token_pattern_catches_every_case(text: str) -> None:
    pattern = next(p for p, term in TERMS if term == "das Token")
    assert re.search(pattern, text) or re.search(pattern, text)


def test_neuter_token_is_not_flagged() -> None:
    pattern = next(p for p, term in TERMS if term == "das Token")
    assert not re.search(pattern, "Das Token ist abgelaufen. Prüfe das Token.")

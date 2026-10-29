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
    (r"\b(den|einen|dein|diesen|keinen) (API-)?Token\b", "das Token"),
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

"""The German terms of the Bridge and of the cards must not drift apart.

The test needs the cards repository next to this one (or `MEALIE_ATELIER_CARDS_DIR`). Without it
the test is skipped, so a lone checkout still passes.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARDS = Path(os.environ.get("MEALIE_ATELIER_CARDS_DIR", ROOT.parent / "mealie-atelier-cards"))
BRIDGE_DE = ROOT / "custom_components" / "mealie_atelier" / "translations" / "de.json"
CARDS_DE = CARDS / "src" / "i18n" / "de.json"

# "Token" is neuter in German. Every masculine form is flagged, with or without adjectives and
# with a compound ("Mealie-Token", "Mealie API-Token"):
#  - accusative determiners (den, einen, deinen, diesen, jeden, welchen ...) plus adjectives in -en
#    or brand words in between,
#  - an adjective in -en after a preposition that takes the accusative ("ohne gültigen Token"),
#  - "ein", "kein", "dein" ... only with an adjective in -er ("ein neuer Token"), because
#    "ein Token" and "dein Token" are valid neuter forms,
#  - "jeder" always,
#  - "der", "dieser", "welcher" anywhere except where the word before is a noun or a quantifier
#    that governs the genitive plural ("die Liste der Token", "viele der Token"). A capitalised
#    word before is a noun unless it opens a question or a clause ("Ist der Token gültig?").
_WORD = r"[^\W\d_]+"
_TOKEN = rf"(?:{_WORD}-)*Token\b"
_ADVERB = "neu|sehr|bereits|schon|noch|frisch|zuletzt|gerade|ganz|vollständig|erst|nun|nur|auch"
_BRAND = rf"(?: (?:{_ADVERB})| [a-zäöüß]+en| [A-ZÄÖÜ]{_WORD}| [A-ZÄÖÜ]{_WORD}-{_WORD})*"
_PHRASE = rf" (?:in|im|aus|von|vom|bei|für|per|über|auf|an|unter|mit|nach)(?: [a-zäöüß]+)? [A-ZÄÖÜ]{_WORD}"
_LEAD = rf"(?:{_PHRASE}| (?:{_ADVERB})| [a-zäöüß]+en| [A-ZÄÖÜ]{_WORD}| [A-ZÄÖÜ]{_WORD}-{_WORD})*"
# A capitalised adjective in -er that opens a sentence, a label or a list item can only be the
# masculine nominative ("Ungültiger Token", "Fehler: Neuer API-Token"), never a genitive plural.
_NOUNS_IN_ER = {"server", "fehler", "nutzer", "benutzer", "zähler", "zeiger", "player"}
_OPENING = re.compile(
    rf"(?:^|[.!?:;\"\u201c\u201e(\-]\s*)(?P<word>[A-ZÄÖÜ]{_WORD}er)(?:{_BRAND})? {_TOKEN}"
)
_ACCUSATIVE = re.compile(
    r"\b(?i:den|einen|keinen|deinen|meinen|seinen|ihren|unseren|diesen|jeden|welchen)"
    rf"{_LEAD} {_TOKEN}"
    r"|\b(?i:ohne|für|gegen|durch|um|bis|pro|wider)"
    + rf"(?:{_PHRASE})?(?: [a-zäöüß]+en)+ "
    + _TOKEN
    + r"|\b(?i:ein|kein|dein|mein|sein|ihr|unser)"
    + rf"(?:{_PHRASE})?(?: [a-zäöüß]+er)+ "
    + _TOKEN
    + r"|\b(?i:jeder)(?: [a-zäöüß]+e)* "
    + _TOKEN
)
_NOMINATIVE = re.compile(
    rf"(?P<before>{_WORD})?(?P<gap>[^\w]*?)\b(?i:der|dieser|welcher)(?: [a-zäöüß]+e| [A-ZÄÖÜ]{_WORD}|{_PHRASE})* "
    + _TOKEN
)
_OPENERS = {
    word
    for word in (
        "ist sind war waren hat hatte wird wurde kann muss darf soll sei gilt gibt fehlt bleibt "
        "sobald seit wenn weil falls ob dass solange bevor nachdem da wo wie warum wann was "
        "und oder aber denn als bis sofern prüfe prüfen dann hier dort jetzt nun bitte danach zuerst"
    ).split()
}
_GENITIVE_LOWER = {
    "alle", "viele", "einige", "manche", "mehrere", "beide", "keiner", "eines", "jedes",
    "wegen", "trotz", "statt", "anstelle", "innerhalb", "außerhalb", "aufgrund", "seitens",
    "zugunsten", "angesichts", "mithilfe", "dank",
}  # fmt: skip


def masculine_token(text: str) -> bool:
    """True when the text uses "Token" with a masculine article or adjective."""
    if _ACCUSATIVE.search(text):
        return True
    for found in _OPENING.finditer(text):
        if found.group("word").lower() not in _NOUNS_IN_ER:
            return True
    for found in _NOMINATIVE.finditer(text):
        before = found.group("before")
        if before and not found.group("gap").strip():
            word = before.lower()
            noun = before[0].isupper() and word not in _OPENERS
            if noun or word in _GENITIVE_LOWER:
                continue
        return True
    return False


# One German term per concept in both repositories: the forbidden variant and the term to use.
TERMS = [
    (re.compile(r"\bSicherung").search, "Backup"),
    (masculine_token, "das Token"),
    (re.compile(r"\bAPI-Tokens\b").search, "API-Token"),
]


def _texts(node: object) -> list[str]:
    if isinstance(node, dict):
        return [text for value in node.values() for text in _texts(value)]
    return [node] if isinstance(node, str) else []


@pytest.mark.skipif(not CARDS_DE.is_file(), reason="cards repository not found next to the Bridge")
@pytest.mark.parametrize(("matches", "term"), TERMS)
def test_german_terms_agree_between_bridge_and_cards(
    matches: Callable[[str], object], term: str
) -> None:
    for name, path in (("Bridge", BRIDGE_DE), ("cards", CARDS_DE)):
        for text in _texts(json.loads(path.read_text())):
            assert not matches(text), f"{name} must say {term!r}: {text}"


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
        "Dieser Token ist abgelaufen",
        "Welcher Token?",
        "Prüfe, ob der Token gültig ist",
        "Prüfe deinen Token",
        "Jeden API-Token prüfen",
        "Der API-Token fehlt",
        "Erstelle einen neuen Token",
        "Prüfe den alten API-Token",
        "Ein neuer Token",
        "Mealie lehnt jeder Token ab",
        "Der neue Token fehlt",
        "Du brauchst deinen neuen langen API-Token",
        "Ist der Token gültig?",
        "Hat der Token Schreibrechte?",
        "Sobald der Token gilt",
        "Seit der Token abgelaufen ist",
        "Der Server meldet es (der Token fehlt)",
        "- Der Token fehlt",
        "\u201eDer Token\u201c fehlt",
        "Ohne gültigen Token geht es nicht",
        "Prüfe deinen Mealie API-Token",
        "Der Mealie-Token fehlt",
        "Den Mealie-Token prüfen",
        "Ungültiger Token.",
        "Ungültiger API-Token",
        "Fehler: Ungültiger Token",
        "Neuer Token gespeichert",
        "Speichere den neu erzeugten Token",
        "Der in Mealie erzeugte Token fehlt",
        "Hier der Token für Mealie",
        "Prüfe den in Mealie erstellten Token.",
        "Kopiere den von Mealie erzeugten Token",
        "Erstelle einen in Mealie gültigen Token",
        "Ohne in Mealie erzeugten Token",
        "Ein in Mealie erzeugter Token fehlt",
        "Den unter Profil erstellten API-Token einfügen",
    ],
)
def test_masculine_token_pattern_catches_the_masculine_forms(text: str) -> None:
    assert masculine_token(text), text


def test_neuter_token_is_not_flagged() -> None:
    for text in (
        "Das Token ist abgelaufen. Prüfe das Token.",
        "Mealie erlaubt diesem Token diese Aktion nicht.",
        "Mit dem Token kannst du das tun.",
        "Die Gültigkeit des Tokens endet.",
        "Mit deinem API-Token geht es.",
        "Die Rechte der Tokens und mit den Tokens.",
        "Die Liste der Token ist leer.",
        "Die Rechte dieser Token enden.",
        "Ein Token fehlt. Dein Token läuft ab.",
        "Erstelle ein neues Token und prüfe das alte API-Token.",
        "Mit dem neuen Token geht es.",
        "Prüfe viele der Token und alle der Token.",
        "Wir erstellen Token für alle Benutzer.",
        "Prüfe das Mealie-Token. Ohne gültiges Token geht es nicht.",
        "Mit deinem Mealie API-Token geht es.",
        "Server Token ungültig",
        "Fehler: Token ungültig",
        "Prüfe die Liste der gespeicherten Token. Neue Token werden ergänzt.",
        "Die Liste der in Mealie erzeugten Token ist leer.",
        "Gültiges Token gespeichert",
    ):
        assert not masculine_token(text), text

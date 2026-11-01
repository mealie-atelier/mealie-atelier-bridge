"""The options key help states the saved state once and only tells a saved key to be kept."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1] / "custom_components/mealie_atelier"
FILES = {
    "strings": BASE / "strings.json",
    "en": BASE / "translations/en.json",
    "de": BASE / "translations/de.json",
}
KEEP = {
    "strings": "leave it empty to keep it.",
    "en": "leave it empty to keep it.",
    "de": "Leer lassen behält ihn.",
}
STATE = {
    "strings": ("No key is saved.", "A key is saved."),
    "en": ("No key is saved.", "A key is saved."),
    "de": ("Es ist kein Schlüssel gespeichert.", "Ein Schlüssel ist gespeichert."),
}
# step -> (usda key saved, ai key saved)
STEPS = {
    "init": (False, False),
    "init_fdc": (True, False),
    "init_ai": (False, True),
    "init_both": (True, True),
}


@pytest.mark.parametrize("lang", FILES)
@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("index,field", [(0, "usda_fdc_api_key"), (1, "openai_api_key")])
def test_keep_sentence_only_when_a_key_is_saved(lang, step, index, field):
    data = json.loads(FILES[lang].read_text(encoding="utf-8"))
    text = data["options"]["step"][step]["data_description"][field]
    saved = STEPS[step][index]
    none_text, saved_text = STATE[lang]
    assert (KEEP[lang] in text) is saved
    assert (saved_text in text) is saved
    assert (none_text in text) is (not saved)


def test_strings_and_english_stay_identical():
    assert FILES["strings"].read_text(encoding="utf-8") == FILES["en"].read_text(encoding="utf-8")

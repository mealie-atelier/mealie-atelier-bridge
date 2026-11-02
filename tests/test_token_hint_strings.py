"""Token hints in the config flow point to Mealie's real token location."""

from __future__ import annotations

import json
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "mealie_atelier"
FILES = ["strings.json", "translations/en.json", "translations/de.json"]


def test_no_token_hint_uses_the_old_profile_path():
    for name in FILES:
        text = (COMPONENT / name).read_text(encoding="utf-8")
        assert "User profile > API Tokens" not in text, name
        assert "Benutzerprofil > API-Token" not in text, name


def test_token_hints_name_the_real_mealie_labels():
    for name, label in [
        ("strings.json", "Manage Your API Tokens"),
        ("translations/en.json", "Manage Your API Tokens"),
        ("translations/de.json", "Verwalte Deine API-Token"),
    ]:
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        hint = data["config"]["step"]["user"]["data_description"]["api_token"]
        assert label in hint, name

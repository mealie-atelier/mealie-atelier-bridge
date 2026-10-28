"""Keep the Home Assistant first-run connection form self-explanatory."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "mealie_atelier"


def test_connection_fields_have_german_and_english_help() -> None:
    for locale in ("de", "en"):
        strings = json.loads((INTEGRATION / "translations" / f"{locale}.json").read_text())
        step = strings["config"]["step"]["user"]

        assert set(step["data_description"]) == set(step["data"])
        assert "9000" in step["data_description"]["url"]
        assert "9090" in step["data_description"]["url"]
        assert "yaml" in step["data_description"]["api_token"].lower()
        assert (
            "self-signed" in step["data_description"]["verify_ssl"].lower()
            or "selbst signiert" in step["data_description"]["verify_ssl"].lower()
        )


def test_home_assistant_setup_guide_offers_both_no_yaml_paths() -> None:
    guide = (ROOT / "docs" / "INSTALLATION.md").read_text()

    assert "Home Assistant" in guide
    assert "Built-in Mealie integration" in guide
    assert "Mealie Atelier Bridge" in guide
    assert "No YAML is needed" in guide
    assert "JavaScript module" in guide

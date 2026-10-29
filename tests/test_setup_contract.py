"""Keep the Home Assistant first-run connection form self-explanatory."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "mealie_atelier"


def test_connection_fields_have_german_and_english_help() -> None:
    for locale in ("de", "en"):
        strings = json.loads((INTEGRATION / "translations" / f"{locale}.json").read_text())
        step = strings["config"]["step"]["user"]

        assert set(step["data_description"]) == set(step["data"])
        assert "9925" in step["data_description"]["url"]
        reconfigure = strings["config"]["step"]["reconfigure"]["data_description"]["url"]
        assert "9090" in reconfigure
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


def test_manifest_follows_hassfest_conventions() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    keys = list(manifest)

    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert {"http", "websocket_api"} <= set(manifest["dependencies"])
    assert manifest["documentation"].startswith("https://github.com/mealie-atelier/")
    assert manifest["version"]


def test_version_is_the_same_everywhere() -> None:
    from custom_components.mealie_atelier.const import VERSION

    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    project = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert manifest["version"] == VERSION
    assert project and project.group(1) == VERSION


def test_config_schema_is_config_entry_only(caplog: pytest.LogCaptureFixture) -> None:
    from custom_components.mealie_atelier import CONFIG_SCHEMA

    assert CONFIG_SCHEMA({}) == {}
    CONFIG_SCHEMA({"mealie_atelier": {}})
    assert "does not support YAML setup" in caplog.text


# Contract 0.3: every code the Bridge sends; sync_not_supported only exists in the web app.
CONTRACT_ERROR_CODES = {
    "not_configured",
    "invalid_request",
    "mealie_auth",
    "mealie_error",
    "ai_error",
    "ai_not_configured",
    "usda_key_invalid",
    "usda_rate_limited",
    "invalid_timer",
    "timer_exists",
    "too_many_timers",
    "timer_not_found",
    "timer_not_permitted",
    "existing_values_protected",
    "read_only",
    "unauthorized",
    "write_not_permitted",
    "conflict",
    "recipe_changed",
    "disabled",
    "use_structured_editor",
    "write_unverified",
    "backup_failed",
    "backup_not_found",
    "upload_invalid",
    "upload_expired",
    "upload_busy",
    "step_not_found",
    "sync_rule_not_found",
    "sync_destination_unsupported",
    "sync_source_taken",
    "sync_too_many_rules",
    "sync_not_activated",
    "offline_op_reused",
    "offline_conflict",
    "offline_add_uncertain",
}


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_translation_key_used_in_code_has_an_exception_message(name: str) -> None:
    used: set[str] = set()
    for source in INTEGRATION.rglob("*.py"):
        text = source.read_text()
        used |= set(re.findall(r'translation_key="(\w+)"', text))
        used |= set(re.findall(r'(?:_translated|_invalid)\("(\w+)"\)', text))
    exceptions = json.loads((INTEGRATION / name).read_text())["exceptions"]

    assert used
    # Helpers pass the key as a variable, so the shared contract codes are listed explicitly.
    used |= CONTRACT_ERROR_CODES
    assert used <= set(exceptions)
    assert all(exceptions[key]["message"] for key in used)


def _strings(node: object) -> list[str]:
    if isinstance(node, dict):
        return [text for value in node.values() for text in _strings(value)]
    return [node] if isinstance(node, str) else []


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/de.json"])
def test_translations_carry_no_urls(name: str) -> None:
    # Hassfest rejects links to sites in translations; they come in as description placeholders.
    # Example addresses such as http://192.168.1.20:9000 are accepted and stay.
    texts = _strings(json.loads((INTEGRATION / name).read_text()))
    assert texts
    assert not [text for text in texts if re.search(r"https?://[a-z][\w-]*(\.[\w-]+)+", text)]


def test_german_copy_uses_one_term_per_concept() -> None:
    texts = _strings(json.loads((INTEGRATION / "translations/de.json").read_text()))
    joined = "\n".join(texts)

    assert "API-Tokens" not in joined  # Mealie DE names the page "API-Token"
    assert not re.search(r"\b(den|einen) (API-)?Token\b", joined)  # das Token
    assert "Backup" not in joined  # one word: Sicherung
    assert "Notify-Dienst" not in joined
    assert "Richte zuerst die Mealie Atelier Bridge ein." in joined


def test_english_copy_is_consistent() -> None:
    for name in ("strings.json", "translations/en.json"):
        joined = "\n".join(_strings(json.loads((INTEGRATION / name).read_text())))
        assert "To-do list" not in joined
        assert "reauthenticate" not in joined

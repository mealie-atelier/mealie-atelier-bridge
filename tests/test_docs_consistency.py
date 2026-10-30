"""Guards the prose of the English docs against drift between files."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = sorted((ROOT / "tests" / "live").glob("*.py"))
DOCS = [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_bring_is_always_written_with_exclamation_mark() -> None:
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not re.search(r"\bBring\b(?!!)", line), f"{path.name}:{number}"


def test_readme_table_does_not_claim_builtin_cannot_write() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| Shopping list and meal plan writes | | " not in readme


def test_uninstall_steps_name_bridge_and_cards_everywhere() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Uninstall", 1)[1].split("## Security", 1)[0]
    assert "Bridge and the cards" in section
    assert "resource" in section


def test_live_scripts_write_bring_with_exclamation_mark() -> None:
    """The capture scripts type rule names into screenshots, so they must not drop the mark."""
    assert LIVE
    for path in LIVE:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not re.search(r"\bBring\b(?!!)", line), f"{path.name}:{number}"


def test_sync_docs_record_the_bring_live_result() -> None:
    """The destinations table and the changelog must agree on the Bring! live test."""
    table = (ROOT / "docs" / "SMART_SHOPPING_SYNC.md").read_text(encoding="utf-8")
    assert "not yet tested" not in table
    row = next(line for line in table.splitlines() if line.startswith("| Bring!"))
    assert "Passed" in row
    assert "Mealie v" in row


def test_brand_readme_names_artwork_source_and_font_license() -> None:
    text = (ROOT / "brand" / "README.md").read_text(encoding="utf-8")
    assert "SIL Open Font License" in text
    assert "brand/source/" in text
    assert "photograph of" not in text


def test_bridge_capture_pins_the_card_language_and_uses_a_throwaway_list() -> None:
    """Mixed-language or stale-bundle screenshots came from an unpinned capture."""
    text = (ROOT / "tests" / "live" / "capture_bridge.py").read_text(encoding="utf-8")
    assert "mealie-atelier:design" in text
    assert "CARDS_BUNDLE" in text
    assert "/api/households/shopping/lists" in text
    assert 'f"/api/households/shopping/lists/{list_id}"' in text


def test_sync_screenshots_keep_their_size() -> None:
    """The editor shot is taller than the viewport so the whole dialog, down to Save, fits."""
    for name in ("sync-setup.webp", "sync-status.webp"):
        data = (ROOT / "assets" / "screens" / name).read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        assert data[12:16] == b"VP8 "
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        assert width == 1440, name
        if name == "sync-status.webp":
            assert height == 960, name
        else:
            assert height > 1800, name


def test_websocket_doc_does_not_claim_fallback_codes_never_carry_a_translation_key() -> None:
    doc = (ROOT / "docs" / "WEBSOCKET_API.md").read_text(encoding="utf-8")
    assert "carry no `translation_key`" not in doc
    assert "never by `translation_key`" in doc
    for action in ("shopping_update", "shopping_delete", "shopping_clear_checked", "expected_ids"):
        assert action in doc


def test_changelog_releases_the_manifest_version_and_links_unreleased() -> None:
    """The manifest version has its own section, and Unreleased compares against that tag."""
    import json

    manifest = json.loads(
        (ROOT / "custom_components" / "mealie_atelier" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = manifest["version"]
    assert f"## [{version}] - " in changelog
    assert "## [Unreleased]" in changelog
    assert (
        f"[Unreleased]: https://github.com/mealie-atelier/mealie-atelier-bridge/compare/v{version}...HEAD"
        in changelog
    )


def test_websocket_doc_lists_every_registered_command() -> None:
    doc = (ROOT / "docs" / "WEBSOCKET_API.md").read_text(encoding="utf-8")
    registered: set[str] = set()
    for source in (ROOT / "custom_components" / "mealie_atelier").rglob("*.py"):
        registered |= set(re.findall(r'"mealie_atelier/([a-z_]+)"', source.read_text("utf-8")))
    documented = set(re.findall(r"`mealie_atelier/([a-z_]+)`", doc))
    assert registered - documented == set()


def test_docs_name_the_marketplace_location_and_route() -> None:
    for name in ("README.md", "docs/INSTALLATION.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "Settings → Marketplace" in text, name
        assert "/marketplace/browse" in text, name


def test_websocket_doc_says_cards_localize_the_backup_summary() -> None:
    doc = (ROOT / "docs" / "WEBSOCKET_API.md").read_text(encoding="utf-8")
    assert "ingredient_count" in doc
    assert "English fallback" in doc

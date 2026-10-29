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

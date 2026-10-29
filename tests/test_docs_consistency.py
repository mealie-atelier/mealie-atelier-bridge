"""Guards the prose of the English docs against drift between files."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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

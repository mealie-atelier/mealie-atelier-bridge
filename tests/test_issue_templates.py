"""GitHub only reports an issue_template in the community profile for Markdown templates."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"


@pytest.mark.parametrize("name", ["bug_report.md", "feature_request.md"])
def test_markdown_issue_template_has_front_matter(name: str) -> None:
    text = (DIR / name).read_text(encoding="utf-8")
    assert re.match(r"---\nname: .+\nabout: .+\nlabels: .+\n---\n", text)


def test_no_form_duplicates_of_markdown_templates() -> None:
    forms = [p.name for p in DIR.glob("*.y*ml") if p.stem in {"bug_report", "feature_request"}]
    assert forms == []


REQUIRED = {
    "bug_report.md": [
        "Package",
        "Mealie Atelier version",
        "Relevant versions",
        "Steps to reproduce",
        "Expected behavior",
        "Actual behavior",
    ],
    "feature_request.md": [
        "What is difficult today?",
        "What would improve it?",
        "Where should it work?",
    ],
}


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_required_sections_are_marked_because_markdown_cannot_enforce_them(name: str) -> None:
    text = (DIR / name).read_text(encoding="utf-8")
    for heading in REQUIRED[name]:
        assert f"\n## {heading} (required)\n" in text, heading


def test_feature_request_keeps_the_compound_hyphen() -> None:
    text = (DIR / "feature_request.md").read_text(encoding="utf-8")
    assert "Home Assistant or Mealie-specific capabilities" in text


def test_faq_does_not_mention_issue_forms() -> None:
    faq = (DIR.parents[1] / "docs" / "FAQ.md").read_text(encoding="utf-8")
    assert "Issue forms" not in faq
    assert "issue forms" not in faq.lower()


def test_bridge_bug_template_asks_for_redacted_logs() -> None:
    text = (DIR / "bug_report.md").read_text(encoding="utf-8")
    assert "## Relevant logs (redacted)" in text
    assert "Settings > System > Logs" in text

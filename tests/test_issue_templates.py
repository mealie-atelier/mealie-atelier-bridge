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

"""The repository carries every file the GitHub community profile counts.

Own copies make the score independent of whether the organization defaults are inherited.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_counted_file_exists() -> None:
    for name in (
        "README.md",
        "CONTRIBUTING.md",
        "LICENSE",
        ".github/CODE_OF_CONDUCT.md",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
    ):
        assert len((ROOT / name).read_text(encoding="utf-8").strip()) > 100, name
    code = (ROOT / ".github" / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    assert code.startswith("# Contributor Covenant Code of Conduct")

"""Guard the repo-wide American spelling convention in user-facing docs."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRITISH = re.compile(r"\b(localis\w*|behaviour\w*|colour\w*|normalis\w*)\b", re.IGNORECASE)


def test_docs_use_american_spelling():
    offenders = []
    for path in [ROOT / "CHANGELOG.md", ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BRITISH.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders

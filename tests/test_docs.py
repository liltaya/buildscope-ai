"""Documentation renders on GitHub: links resolve and diagrams parse"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCUMENTS = sorted([*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md")])

LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")

MERMAID = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)


def relative_targets(text: str) -> list[str]:
    return [
        target
        for target in LINK.findall(text)
        if not target.startswith(("http://", "https://", "mailto:", "#"))
    ]


@pytest.fixture(params=DOCUMENTS, ids=lambda path: str(path.relative_to(ROOT)))
def document(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path


def test_documents_are_found() -> None:
    names = {str(path.relative_to(ROOT)) for path in DOCUMENTS}

    assert {"README.md", "SECURITY.md", "CHANGELOG.md", "docs/architecture.md"} <= names


def test_relative_links_and_images_resolve(document: Path) -> None:
    missing = [
        target
        for target in relative_targets(document.read_text())
        if not (document.parent / target.split("#")[0]).exists()
    ]

    assert not missing, f"{document.name} links to missing files: {missing}"


def test_code_fences_are_closed(document: Path) -> None:
    fences = re.findall(r"^```", document.read_text(), re.MULTILINE)

    assert len(fences) % 2 == 0


def test_sequence_diagrams_have_no_statement_separators(document: Path) -> None:
    # Mermaid ends a sequence-diagram statement at ";", so a semicolon in a
    # message label splits it and GitHub shows a parse error instead.
    for block in MERMAID.findall(document.read_text()):
        if block.lstrip().startswith("sequenceDiagram"):
            offending = [line.strip() for line in block.splitlines() if ";" in line]

            assert not offending, f"{document.name}: {offending}"


def test_readme_stays_short() -> None:
    assert len((ROOT / "README.md").read_text().splitlines()) <= 400

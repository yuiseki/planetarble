"""docs/sources/ is the reference for every source; the registry must agree."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from planetarble.overlay.sources import SOURCE_REGISTRY

DOCS = Path(__file__).resolve().parents[3] / "docs" / "sources"
REQUIRED = ("id", "title", "status", "coverage", "resolution", "max_zoom", "license", "attribution", "access")


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no front matter"
    return yaml.safe_load(text.split("---\n", 2)[1])


PAGES = {p: _front_matter(p) for p in sorted(DOCS.glob("*/*.md"))}
BY_ID = {fm["id"]: (p, fm) for p, fm in PAGES.items()}


@pytest.mark.parametrize("path", list(PAGES), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_page_has_required_fields(path: Path) -> None:
    missing = [k for k in REQUIRED if PAGES[path].get(k) in (None, "")]
    assert missing == [], f"{path}: missing {missing}"


def test_ids_are_unique() -> None:
    ids = [fm["id"] for fm in PAGES.values()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("source", sorted(SOURCE_REGISTRY))
def test_every_registry_source_has_a_page_with_the_same_ceiling(source: str) -> None:
    assert source in BY_ID, f"no docs/sources page with id: {source}"
    path, fm = BY_ID[source]
    assert fm["max_zoom"] == SOURCE_REGISTRY[source].native_max_zoom, path


def test_index_links_every_page() -> None:
    for name in ("README.md", "README.ja.md"):
        index = (DOCS / name).read_text(encoding="utf-8")
        for path in PAGES:
            assert f"({path.parent.name}/{path.name})" in index, f"{name} does not link {path}"


@pytest.mark.parametrize("source", sorted(SOURCE_REGISTRY))
def test_registry_attribution_matches_the_page(source: str) -> None:
    path, fm = BY_ID[source]
    assert SOURCE_REGISTRY[source].attribution == fm["attribution"], path

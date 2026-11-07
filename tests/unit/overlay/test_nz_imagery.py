"""New Zealand imagery (LINZ static STAC) as an overlay source (ADR 0003 step 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay.adapters import NZImageryAdapter, get_adapter
from planetarble.overlay.sources import SOURCE_REGISTRY

ROOT = "https://nz-imagery.s3.ap-southeast-2.amazonaws.com/catalog.json"
B = "https://nz-imagery.s3.ap-southeast-2.amazonaws.com"
AOI = type("R", (), {"bbox": (174.77, -41.29, 174.79, -41.27)})()


def _collection(slug: str, title: str, bbox, year: str, category: str = "urban-aerial-photos",
                licence: str = "CC-BY-4.0", lifecycle: str = "completed", items=("A",)) -> dict:
    return {
        "title": title,
        "license": licence,
        "extent": {"spatial": {"bbox": [list(bbox)]}, "temporal": {"interval": [[f"{year}-01-01T00:00:00Z", f"{year}-02-01T00:00:00Z"]]}},
        "providers": [{"name": "Toitū Te Whenua Land Information New Zealand", "roles": ["host"]},
                      {"name": "Wellington City Council", "roles": ["licensor"]}],
        "linz:geospatial_category": category,
        "linz:lifecycle": lifecycle,
        "linz:slug": slug,
        "links": [{"rel": "item", "href": f"./{i}.json", "file:checksum": f"c-{slug}-{i}"} for i in items],
    }


def _item(bbox) -> dict:
    return {"bbox": list(bbox), "assets": {"visual": {"href": "./tile.tiff"}}}


WGTN = (174.70, -41.37, 174.87, -41.15)
DOCS = {
    ROOT: {"links": [
        {"rel": "child", "href": "./wellington/wellington_2021_0.075m/rgb/2193/collection.json", "file:checksum": "a"},
        {"rel": "child", "href": "./wellington/wellington_2017_0.075m/rgb/2193/collection.json", "file:checksum": "b"},
        {"rel": "child", "href": "./wellington/wellington_2021_0.3m/rgb/2193/collection.json", "file:checksum": "c"},
        {"rel": "child", "href": "./wellington/wellington_2024_0.075m/rgb/2193/collection.json", "file:checksum": "d"},
        {"rel": "child", "href": "./wellington/wellington_2021_0.075m/rgbnir/2193/collection.json", "file:checksum": "e"},
        {"rel": "child", "href": "./auckland/auckland_2023_0.06m/rgb/2193/collection.json", "file:checksum": "f"},
        {"rel": "child", "href": "./wellington/wellington_sn5_1978_0.375m/rgb/2193/collection.json", "file:checksum": "g"},
    ]},
    f"{B}/wellington/wellington_2021_0.075m/rgb/2193/collection.json":
        _collection("wellington_2021_0.075m", "Wellington 0.075m Urban Aerial Photos (2021)", WGTN, "2021", items=("T1", "T2")),
    f"{B}/wellington/wellington_2017_0.075m/rgb/2193/collection.json":
        _collection("wellington_2017_0.075m", "Wellington 0.075m (2017)", WGTN, "2017"),
    f"{B}/wellington/wellington_2021_0.3m/rgb/2193/collection.json":
        _collection("wellington_2021_0.3m", "Wellington 0.3m Rural (2021)", WGTN, "2021", category="rural-aerial-photos"),
    f"{B}/wellington/wellington_2024_0.075m/rgb/2193/collection.json":
        _collection("wellington_2024_0.075m", "Wellington Draft (2024)", WGTN, "2024", lifecycle="preview"),
    f"{B}/auckland/auckland_2023_0.06m/rgb/2193/collection.json":
        _collection("auckland_2023_0.06m", "Auckland (2023)", (174.4, -37.1, 175.3, -36.6), "2023"),
    f"{B}/wellington/wellington_sn5_1978_0.375m/rgb/2193/collection.json":
        _collection("wellington_sn5_1978_0.375m", "Wellington SN5 (1978)", WGTN, "1978", category="scanned-aerial-photos"),
    f"{B}/wellington/wellington_2021_0.075m/rgb/2193/T1.json": _item((174.775, -41.285, 174.78, -41.28)),
    f"{B}/wellington/wellington_2021_0.075m/rgb/2193/T2.json": _item((174.80, -41.20, 174.81, -41.19)),
    f"{B}/wellington/wellington_2017_0.075m/rgb/2193/A.json": _item((174.775, -41.285, 174.78, -41.28)),
    f"{B}/wellington/wellington_2026_0.2m/rgb/2193/A.json": _item((174.775, -41.285, 174.78, -41.28)),
}


def _adapter(tmp_path: Path, **kw) -> NZImageryAdapter:
    return NZImageryAdapter(fetch_json=lambda url: DOCS[url], cache_dir=tmp_path / "cache", **kw)


def test_picks_the_finest_recent_completed_current_rgb_collection(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    urls = adapter.plan(AOI, (14, 21))
    assert urls == [f"{B}/wellington/wellington_2021_0.075m/rgb/2193/tile.tiff"]  # T2 is outside the AOI
    assert adapter.credit == (
        "Imagery: Wellington 0.075m Urban Aerial Photos (2021), Wellington City Council, "
        "via Toitū Te Whenua Land Information New Zealand (CC BY 4.0)"
    )


def test_collection_can_be_pinned(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, collection="wellington_2017_0.075m")
    assert adapter.plan(AOI, (14, 21)) == [f"{B}/wellington/wellington_2017_0.075m/rgb/2193/tile.tiff"]
    with pytest.raises(ValueError, match="no New Zealand imagery collection named nope"):
        _adapter(tmp_path, collection="nope").plan(AOI, (14, 21))


def test_outside_new_zealand_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no New Zealand imagery"):
        _adapter(tmp_path).plan(type("R", (), {"bbox": (139.0, 35.0, 139.1, 35.1)})(), (14, 21))


def test_non_cc_by_collections_are_refused(tmp_path: Path) -> None:
    docs = dict(DOCS)
    url = f"{B}/wellington/wellington_2017_0.075m/rgb/2193/collection.json"
    docs[url] = {**docs[url], "license": "proprietary"}
    adapter = NZImageryAdapter(fetch_json=lambda u: docs[u], cache_dir=tmp_path / "c", collection="wellington_2017_0.075m")
    with pytest.raises(ValueError, match="licence proprietary"):
        adapter.plan(AOI, (14, 21))


def test_documents_are_cached_by_checksum(tmp_path: Path) -> None:
    calls: list[str] = []
    fetch = lambda url: calls.append(url) or DOCS[url]  # noqa: E731
    NZImageryAdapter(fetch_json=fetch, cache_dir=tmp_path / "cache").plan(AOI, (14, 21))
    first = len(calls)
    NZImageryAdapter(fetch_json=fetch, cache_dir=tmp_path / "cache").plan(AOI, (14, 21))
    assert len(calls) - first == 1  # only the root catalog is fetched again


def test_build_raster_keeps_rgb(tmp_path: Path) -> None:
    ran: list[list[str]] = []
    adapter = NZImageryAdapter(fetch_json=lambda u: {}, cache_dir=tmp_path, run=ran.append)
    adapter.build_raster(["https://x/t.tiff"], tmp_path / "nz.tif", aoi_bbox=AOI.bbox)
    assert [c[0] for c in ran] == ["gdalwarp"]


def test_registry_and_factory(tmp_path: Path) -> None:
    assert SOURCE_REGISTRY["nz-imagery"].native_max_zoom == 21
    assert isinstance(get_adapter("nz-imagery", fetch_json=lambda u: {}, cache_dir=tmp_path), NZImageryAdapter)


def test_a_finer_survey_wins_over_a_slightly_newer_coarser_one(tmp_path: Path) -> None:
    docs = dict(DOCS)
    docs[ROOT] = {"links": DOCS[ROOT]["links"] + [
        {"rel": "child", "href": "./wellington/wellington_2026_0.2m/rgb/2193/collection.json", "file:checksum": "h"}]}
    docs[f"{B}/wellington/wellington_2026_0.2m/rgb/2193/collection.json"] = _collection(
        "wellington_2026_0.2m", "Wellington 0.2m Rural (2026)", WGTN, "2026", category="rural-aerial-photos")
    urls = NZImageryAdapter(fetch_json=lambda u: docs[u], cache_dir=tmp_path / "c").plan(AOI, (14, 21))
    assert urls == [f"{B}/wellington/wellington_2021_0.075m/rgb/2193/tile.tiff"]
    old_only = NZImageryAdapter(fetch_json=lambda u: docs[u], cache_dir=tmp_path / "c", max_age_years=2)
    assert old_only.plan(AOI, (14, 21))[0].startswith(f"{B}/wellington/wellington_2026_0.2m/")

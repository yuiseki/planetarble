"""HLS / Sentinel-2 asset caching: hits, downloads, quarantine, SAS re-signing.

urlopen and the SAS token endpoint are faked, and raster validity is decided
by a content marker so the tests run without GDAL. The GDAL validity checks
themselves are covered by test_asset_validity_resilient.py.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError

import pytest

from planetarble.core.models import Sentinel2Config
from planetarble.processing import assets

VALID = b"RASTER" + b"x" * 100
INVALID = b"garbage"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def getheader(self, name: str):
        return str(len(self.getvalue())) if name == "Content-Length" else None


@pytest.fixture
def remote(monkeypatch) -> Dict[str, Any]:
    """URL -> bytes (or an exception) served by a fake urlopen."""
    served: Dict[str, Any] = {}
    requested: List[str] = []

    def fake_urlopen(url, timeout):
        requested.append(url)
        body = served.get(url.split("?")[0])
        if isinstance(body, Exception):
            raise body
        if body is None:
            raise HTTPError(url, 404, "not found", {}, None)  # type: ignore[arg-type]
        return FakeResponse(body)

    def valid(path: Path) -> bool:
        return path.exists() and path.read_bytes().startswith(b"RASTER")

    monkeypatch.setattr(assets, "urlopen", fake_urlopen)
    monkeypatch.setattr(assets, "_is_valid_hls_asset", valid)
    monkeypatch.setattr(assets, "_is_valid_sentinel2_asset", valid)
    monkeypatch.setattr(assets, "_aria2c_available", lambda: False)
    served["requested"] = requested
    return served


def _quarantined(directory: Path) -> List[str]:
    return sorted(p.name.split(".corrupt-")[0] for p in directory.glob("*.corrupt-*"))


# --- HLS single asset ---------------------------------------------------------


def test_hls_asset_is_downloaded_then_served_from_cache(tmp_path: Path, remote) -> None:
    remote["https://h/B04.tif"] = VALID
    first = assets._cache_hls_asset("https://h/B04.tif", cache_dir=tmp_path, timeout=5)
    second = assets._cache_hls_asset("https://h/B04.tif", cache_dir=tmp_path, timeout=5)

    assert first == (tmp_path / "B04.tif", "download")
    assert second == (tmp_path / "B04.tif", "hit")
    assert remote["requested"] == ["https://h/B04.tif"]
    assert not (tmp_path / "B04.tif.part").exists()


def test_invalid_cached_hls_asset_is_quarantined_and_refetched(tmp_path: Path, remote) -> None:
    (tmp_path / "B04.tif").write_bytes(INVALID)
    remote["https://h/B04.tif"] = VALID

    path, status = assets._cache_hls_asset("https://h/B04.tif", cache_dir=tmp_path, timeout=5)

    assert status == "redownload"
    assert path.read_bytes() == VALID
    assert _quarantined(tmp_path) == ["B04.tif"]


def test_invalid_hls_download_is_quarantined_and_raises(tmp_path: Path, remote) -> None:
    remote["https://h/B04.tif"] = INVALID
    with pytest.raises(RuntimeError, match="invalid"):
        assets._cache_hls_asset("https://h/B04.tif", cache_dir=tmp_path, timeout=5)
    assert not (tmp_path / "B04.tif").exists()
    assert _quarantined(tmp_path) == ["B04.tif.part"]


def test_hls_scenes_get_local_band_and_fmask_paths(tmp_path: Path, remote) -> None:
    for name in ("B04", "B03", "Fmask"):
        remote[f"https://h/{name}.tif"] = VALID
    scenes: List[Dict[str, object]] = [
        {"collection_id": "HLSL30.v2.0", "item_id": "T54SUE", "qa_asset": "https://h/Fmask.tif",
         "bands": {"red": "https://h/B04.tif", "green": "https://h/B03.tif", "blue": ""}},
        {"collection_id": "HLSL30.v2.0", "bands": {"red": "https://h/B04.tif"}},  # no item id
        {"collection_id": "HLSL30.v2.0", "item_id": "X", "bands": None},
    ]

    updated = assets._cache_hls_assets(scenes, cache_dir=tmp_path, timeout=5)

    (scene,) = updated
    item_dir = tmp_path / "HLSL30.v2.0" / "T54SUE"
    assert scene["bands"] == {"red": str(item_dir / "B04.tif"), "green": str(item_dir / "B03.tif")}
    assert scene["qa_local"] == str(item_dir / "Fmask.tif")


# --- Sentinel-2 single asset --------------------------------------------------


def test_sentinel2_asset_download_and_hit(tmp_path: Path, remote) -> None:
    remote["https://s/visual.tif"] = VALID
    first = assets._cache_sentinel2_asset("https://s/visual.tif?sig=1", cache_dir=tmp_path, timeout=5, asset_name="visual")
    second = assets._cache_sentinel2_asset("https://s/visual.tif?sig=2", cache_dir=tmp_path, timeout=5, asset_name="visual")
    assert first == (tmp_path / "visual.tif", "download")
    assert second == (tmp_path / "visual.tif", "hit")


def test_sentinel2_http_error_fails_softly_and_leaves_no_part(tmp_path: Path, remote) -> None:
    result = assets._cache_sentinel2_asset("https://s/missing.tif", cache_dir=tmp_path, timeout=5, asset_name="B04")
    assert result == (None, "failed")
    assert list(tmp_path.iterdir()) == []


def test_sentinel2_os_error_fails_softly(tmp_path: Path, remote) -> None:
    remote["https://s/B04.tif"] = OSError("connection reset")
    assert assets._cache_sentinel2_asset("https://s/B04.tif", cache_dir=tmp_path, timeout=5, asset_name="B04") == (
        None, "failed",
    )


def test_invalid_sentinel2_download_is_quarantined(tmp_path: Path, remote) -> None:
    remote["https://s/B04.tif"] = INVALID
    assert assets._cache_sentinel2_asset("https://s/B04.tif", cache_dir=tmp_path, timeout=5, asset_name="B04") == (
        None, "failed",
    )
    assert _quarantined(tmp_path) == ["B04.tif.part"]


def test_partial_aria2_download_is_resumed_not_quarantined(tmp_path: Path, remote, monkeypatch) -> None:
    partial = tmp_path / "B04.tif"
    partial.write_bytes(INVALID)
    (tmp_path / "B04.tif.aria2").write_bytes(b"control")
    resumed: List[str] = []

    def fake_aria2(url, destination, *, timeout):
        resumed.append(url)
        destination.write_bytes(VALID)

    monkeypatch.setattr(assets, "_aria2c_available", lambda: True)
    monkeypatch.setattr(assets, "_download_with_aria2", fake_aria2)

    path, status = assets._cache_sentinel2_asset("https://s/B04.tif?sig", cache_dir=tmp_path, timeout=5, asset_name="B04")

    assert (path, status) == (partial, "download")
    assert resumed == ["https://s/B04.tif?sig"]
    assert _quarantined(tmp_path) == []


def test_aria2_failure_is_a_soft_failure(tmp_path: Path, remote, monkeypatch) -> None:
    def failing(url, destination, *, timeout):
        raise RuntimeError("aria2c failed")

    monkeypatch.setattr(assets, "_aria2c_available", lambda: True)
    monkeypatch.setattr(assets, "_download_with_aria2", failing)
    assert assets._cache_sentinel2_asset("https://s/B04.tif", cache_dir=tmp_path, timeout=5, asset_name="B04") == (
        None, "failed",
    )


# --- Sentinel-2 scenes and SAS tokens -----------------------------------------


@pytest.fixture
def tokens(monkeypatch) -> List[str]:
    issued: List[str] = []

    def fake_token(collection, timeout):
        issued.append(collection)
        return f"sig={len(issued)}"

    monkeypatch.setattr(assets, "fetch_sas_token", fake_token)
    return issued


def test_sentinel2_scenes_share_one_token_per_collection_and_resign_hrefs(tmp_path: Path, remote, tokens) -> None:
    remote["https://s/a/B04.tif"] = VALID
    remote["https://s/b/B04.tif"] = VALID
    scenes: List[Dict[str, object]] = [
        {"item_id": "A", "assets": {"B04": "https://s/a/B04.tif?st=old&sig=expired"}},
        {"item_id": "B", "assets": {"B04": "https://s/b/B04.tif"}},
    ]

    updated = assets._cache_sentinel2_assets(scenes, cache_dir=tmp_path, timeout=5, config=Sentinel2Config())

    assert tokens == ["sentinel-2-l2a"]  # collection falls back to the config's
    assert remote["requested"] == ["https://s/a/B04.tif?sig=1", "https://s/b/B04.tif?sig=1"]
    assert [s["assets"] for s in updated] == [
        {"B04": str(tmp_path / "sentinel-2-l2a" / "A" / "B04.tif")},
        {"B04": str(tmp_path / "sentinel-2-l2a" / "B" / "B04.tif")},
    ]


def test_a_failed_asset_is_retried_once_with_a_fresh_token(tmp_path: Path, remote, tokens, monkeypatch) -> None:
    calls: List[str] = []
    real = assets._cache_sentinel2_asset

    def flaky(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return None, "failed"  # e.g. the cached token expired
        return real(url, **kwargs)

    monkeypatch.setattr(assets, "_cache_sentinel2_asset", flaky)
    remote["https://s/a/B04.tif"] = VALID
    scenes: List[Dict[str, object]] = [{"item_id": "A", "assets": {"B04": "https://s/a/B04.tif"}}]

    updated = assets._cache_sentinel2_assets(scenes, cache_dir=tmp_path, timeout=5, config=Sentinel2Config())

    assert calls == ["https://s/a/B04.tif?sig=1", "https://s/a/B04.tif?sig=2"]
    assert len(updated) == 1


def test_scenes_without_any_cached_asset_are_dropped(tmp_path: Path, remote, tokens) -> None:
    scenes: List[Dict[str, object]] = [{"item_id": "A", "assets": {"B04": "https://s/a/missing.tif"}}]
    assert assets._cache_sentinel2_assets(scenes, cache_dir=tmp_path, timeout=5, config=Sentinel2Config()) == []


# --- small helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "total", "bar"),
    [(0, 10, "[----------]"), (5, 10, "[#####-----]"), (10, 10, "[##########]"), (15, 10, "[##########]"),
     (1, 0, "[----------]")],
)
def test_progress_bar(current: int, total: int, bar: str) -> None:
    assert assets._format_progress_bar(current, total, width=10) == bar


def test_strip_query_drops_query_and_fragment() -> None:
    assert assets._strip_query("https://a/b.tif?sig=1#x") == "https://a/b.tif"


def test_quarantine_of_a_missing_file_returns_the_path(tmp_path: Path) -> None:
    assert assets._quarantine_hls_asset(tmp_path / "none.tif", reason="x") == tmp_path / "none.tif"


def test_aria2_command_resumes_and_keeps_the_control_file(tmp_path: Path, monkeypatch) -> None:
    runs: List[List[str]] = []
    monkeypatch.setattr(assets.subprocess, "run", lambda cmd, check: runs.append(cmd))
    assets._download_with_aria2("https://s/B04.tif?sig", tmp_path / "d" / "B04.tif", timeout=30)
    (cmd,) = runs
    assert "--continue=true" in cmd and "--remove-control-file=false" in cmd and "--timeout=30" in cmd
    assert cmd[cmd.index("--dir") + 1] == str(tmp_path / "d")
    assert cmd[-1] == "https://s/B04.tif?sig"

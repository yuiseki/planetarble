"""Tests for ETOPO acquisition source selection."""

from __future__ import annotations

from pathlib import Path

from planetarble.acquisition import AcquisitionManager


class _StubDownloader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def download(self, asset_id: str, force: bool = False):
        self.calls.append((asset_id, force))

        class _Result:
            path = Path("data/etopo/ETOPO_2022_15s_bed.tif")

        return _Result()


def _make_manager() -> tuple[AcquisitionManager, _StubDownloader]:
    manager = object.__new__(AcquisitionManager)
    downloader = _StubDownloader()
    manager._downloader = downloader
    return manager, downloader


def test_download_etopo_defaults_to_catalog_asset() -> None:
    manager, downloader = _make_manager()

    manager.download_etopo(force=True)

    assert downloader.calls == [("etopo_2022_15s_bedrock_cog", True)]


def test_download_etopo_honors_configured_source_id() -> None:
    # ocean.source_id is documented as the way to point at a custom ETOPO
    # asset; it must actually reach the downloader.
    manager, downloader = _make_manager()

    manager.download_etopo(source_id="my_custom_etopo", force=False)

    assert downloader.calls == [("my_custom_etopo", False)]

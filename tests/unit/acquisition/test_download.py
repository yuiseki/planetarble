"""DownloadManager: cache reuse, URL fallback, retries and checksums.

Uses ``file://`` URLs through urllib, so nothing leaves the machine; aria2c is
exercised only through the command it would be given.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import List

import pytest

from planetarble.acquisition import download as download_module
from planetarble.acquisition.catalog import AssetCatalog
from planetarble.acquisition.download import (
    DownloadError,
    DownloadManager,
    calculate_sha256,
)

PAYLOAD = b"planetarble" * 1000
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "remote" / "asset.bin"
    path.parent.mkdir()
    path.write_bytes(PAYLOAD)
    return path


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch) -> List[float]:
    sleeps: List[float] = []
    monkeypatch.setattr(download_module.time, "sleep", sleeps.append)
    return sleeps


def _manager(tmp_path: Path, urls: List[str], checksum: str | None = None, **kwargs) -> DownloadManager:
    catalog = AssetCatalog.from_mapping(
        {"a": {"name": "A", "urls": urls, "destination": "raw/a.bin", "checksum": checksum}}
    )
    return DownloadManager(tmp_path / "data", catalog, use_aria2=False, **kwargs)


def test_calculate_sha256_reads_in_chunks(source: Path) -> None:
    assert calculate_sha256(source, chunk_size=7) == PAYLOAD_SHA


def test_download_fetches_and_records_the_result(tmp_path: Path, source: Path) -> None:
    manager = _manager(tmp_path, [source.as_uri()], checksum=PAYLOAD_SHA)
    result = manager.download("a")

    target = (tmp_path / "data" / "raw" / "a.bin").resolve()
    assert result.path == target
    assert target.read_bytes() == PAYLOAD
    assert (result.sha256, result.size_bytes, result.url) == (PAYLOAD_SHA, len(PAYLOAD), source.as_uri())
    assert not target.with_suffix(".bin.part").exists()
    assert manager.results == {"a": result}


def test_cached_asset_is_not_downloaded_again(tmp_path: Path, source: Path) -> None:
    manager = _manager(tmp_path, [source.as_uri()])
    manager.download("a")
    source.write_bytes(b"changed upstream")

    result = manager.download("a")

    assert result.sha256 == PAYLOAD_SHA


def test_force_downloads_again(tmp_path: Path, source: Path) -> None:
    manager = _manager(tmp_path, [source.as_uri()])
    manager.download("a")
    source.write_bytes(b"changed upstream")

    result = manager.download("a", force=True)

    assert result.size_bytes == len(b"changed upstream")


def test_cached_asset_with_wrong_checksum_is_rejected(tmp_path: Path, source: Path) -> None:
    target = tmp_path / "data" / "raw" / "a.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")
    with pytest.raises(DownloadError, match="Checksum mismatch"):
        _manager(tmp_path, [source.as_uri()], checksum=PAYLOAD_SHA).download("a")


def test_failing_url_is_retried_with_backoff_then_the_next_url_is_used(
    tmp_path: Path, source: Path, no_sleep: List[float]
) -> None:
    missing = (tmp_path / "remote" / "missing.bin").as_uri()
    manager = _manager(tmp_path, [missing, source.as_uri()], retries=3, backoff_seconds=2.0)

    result = manager.download("a")

    assert result.url == source.as_uri()
    assert no_sleep == [2.0, 4.0]  # between the three attempts on the first URL, none after the last


def test_exhausting_every_url_raises_with_the_last_error(tmp_path: Path) -> None:
    missing = (tmp_path / "missing.bin").as_uri()
    with pytest.raises(DownloadError, match="Unable to download asset a") as info:
        _manager(tmp_path, [missing], retries=2).download("a")
    assert info.value.__cause__ is not None


def test_downloaded_file_with_wrong_checksum_is_not_accepted(tmp_path: Path, source: Path) -> None:
    manager = _manager(tmp_path, [source.as_uri()], checksum="0" * 64, retries=1)
    with pytest.raises(DownloadError):
        manager.download("a")
    assert manager.results == {}


def test_download_many_returns_every_result(tmp_path: Path, source: Path) -> None:
    catalog = AssetCatalog.from_mapping(
        {
            "a": {"name": "A", "urls": [source.as_uri()], "destination": "a.bin"},
            "b": {"name": "B", "urls": [source.as_uri()], "destination": "b.bin"},
        }
    )
    results = DownloadManager(tmp_path, catalog, use_aria2=False).download_many(["a", "b"])
    assert sorted(results) == ["a", "b"]
    assert all(r.sha256 == PAYLOAD_SHA for r in results.values())


def test_aria2_is_not_used_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(download_module.shutil, "which", lambda name: None)
    manager = DownloadManager(tmp_path, AssetCatalog({}), use_aria2=True)
    assert manager._use_aria2 is False


def test_aria2_command_resumes_into_the_target(tmp_path: Path, monkeypatch) -> None:
    runs: List[List[str]] = []

    def fake_run(cmd, check):
        runs.append(cmd)
        (tmp_path / "out" / "a.bin").write_bytes(PAYLOAD)

    monkeypatch.setattr(download_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    monkeypatch.setattr(download_module.subprocess, "run", fake_run)
    manager = DownloadManager(tmp_path, AssetCatalog({}), retries=4, backoff_seconds=3.0)

    sha, size = manager._fetch("https://example.invalid/a.bin", tmp_path / "out" / "a.bin")

    (cmd,) = runs
    assert cmd[0] == "aria2c"
    assert "--continue=true" in cmd and "--max-tries=4" in cmd and "--retry-wait=3" in cmd
    assert cmd[cmd.index("--dir") + 1] == str(tmp_path / "out")
    assert cmd[cmd.index("--out") + 1] == "a.bin"
    assert (sha, size) == (PAYLOAD_SHA, len(PAYLOAD))


def test_aria2_failure_becomes_a_download_error(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, check):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(download_module.shutil, "which", lambda name: "/usr/bin/aria2c")
    monkeypatch.setattr(download_module.subprocess, "run", fake_run)
    manager = DownloadManager(tmp_path, AssetCatalog({}))
    with pytest.raises(DownloadError, match="aria2c failed"):
        manager._fetch("https://example.invalid/a.bin", tmp_path / "a.bin")

"""Download helpers with retry and checksum support."""

from __future__ import annotations

import hashlib
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from planetarble.logging import get_logger

from .catalog import AssetCatalog, AssetRecord

LOGGER = get_logger(__name__)


@dataclass
class DownloadResult:
    """Outcome of fetching an asset."""

    asset: AssetRecord
    path: Path
    url: str
    sha256: str
    size_bytes: int


class DownloadError(RuntimeError):
    """Raised when an asset cannot be downloaded after retries."""


def calculate_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA256 of a file using buffered reads."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadManager:
    """Coordinate dataset downloads according to the asset catalog."""

    def __init__(
        self,
        data_directory: Path,
        catalog: AssetCatalog,
        *,
        retries: int = 3,
        backoff_seconds: float = 2.0,
        timeout: int = 120,
    ) -> None:
        self._data_directory = data_directory
        self._catalog = catalog
        self._retries = retries
        self._backoff = backoff_seconds
        self._timeout = timeout
        self._results: Dict[str, DownloadResult] = {}

    @property
    def results(self) -> Dict[str, DownloadResult]:
        return dict(self._results)

    def download(self, asset_id: str, *, force: bool = False) -> DownloadResult:
        asset = self._catalog.get(asset_id)
        target = asset.target_path(self._data_directory)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and not force:
            sha256 = calculate_sha256(target)
            size_bytes = target.stat().st_size
            LOGGER.info(
                "asset already present", extra={"asset_id": asset_id, "path": str(target)}
            )
            cached_url = asset.urls[0] if asset.urls else "cached"
            result = DownloadResult(
                asset=asset,
                path=target,
                url=cached_url,
                sha256=sha256,
                size_bytes=size_bytes,
            )
            self._results[asset_id] = result
            self._validate_expected_checksum(asset, result)
            return result

        last_error: Optional[Exception] = None
        for url in asset.urls:
            for attempt in range(1, self._retries + 1):
                try:
                    LOGGER.info(
                        "downloading asset",
                        extra={"asset_id": asset_id, "url": url, "attempt": attempt},
                    )
                    sha256, size_bytes = self._fetch(url, target)
                    result = DownloadResult(
                        asset=asset,
                        path=target,
                        url=url,
                        sha256=sha256,
                        size_bytes=size_bytes,
                    )
                    self._validate_expected_checksum(asset, result)
                    self._results[asset_id] = result
                    return result
                except Exception as exc:  # pragma: no cover - network failure path
                    last_error = exc
                    LOGGER.warning(
                        "download attempt failed",
                        extra={
                            "asset_id": asset_id,
                            "url": url,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                    )
                    if attempt < self._retries:
                        time.sleep(self._backoff * attempt)
            LOGGER.info(
                "moving to next URL",
                extra={"asset_id": asset_id, "url": url},
            )
        raise DownloadError(f"Unable to download asset {asset_id}") from last_error

    def download_many(self, asset_ids: Iterable[str], *, force: bool = False) -> Dict[str, DownloadResult]:
        return {asset_id: self.download(asset_id, force=force) for asset_id in asset_ids}

    def _fetch(self, url: str, destination: Path) -> tuple[str, int]:
        request = urllib.request.Request(url, headers={"User-Agent": "Planetarble/0.1"})
        temp_path = destination.with_suffix(destination.suffix + ".part")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # nosec B310
            sha256 = hashlib.sha256()
            size_bytes = 0
            with temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    sha256.update(chunk)
                    size_bytes += len(chunk)
        temp_path.replace(destination)
        computed = sha256.hexdigest()
        return computed, size_bytes

    def _validate_expected_checksum(self, asset: AssetRecord, result: DownloadResult) -> None:
        if asset.expected_sha256 and asset.expected_sha256 != result.sha256:
            raise DownloadError(
                f"Checksum mismatch for {asset.asset_id}: expected {asset.expected_sha256}, got {result.sha256}"
            )

"""Download, cache and validate HLS / Sentinel-2 source assets."""

from __future__ import annotations

import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.error import HTTPError
from urllib.request import urlopen

from planetarble.acquisition.mpc import append_sas_token, fetch_sas_token
from planetarble.core.models import Sentinel2Config
from planetarble.logging import get_logger, log_progress

LOGGER = get_logger(__name__)


def _strip_query(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _cache_hls_assets(
    scenes: Sequence[Dict[str, object]],
    *,
    cache_dir: Path,
    timeout: int,
) -> List[Dict[str, object]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    total_assets = 0
    for scene in scenes:
        bands = scene.get("bands")
        if isinstance(bands, dict):
            total_assets += len([href for href in bands.values() if isinstance(href, str) and href])
    start_time = time.monotonic()
    completed = 0
    progress_interval = 10
    cache_hits = 0
    downloads = 0
    redownloads = 0
    # collection -> a token fetched during this run, replacing the manifest's
    tokens: Dict[str, str] = {}
    updated: List[Dict[str, object]] = []
    for scene in scenes:
        collection = scene.get("collection_id")
        item_id = scene.get("item_id")
        if not isinstance(collection, str) or not isinstance(item_id, str):
            continue
        bands = scene.get("bands")
        if not isinstance(bands, dict):
            continue
        local_bands: Dict[str, str] = {}
        for band, href in bands.items():
            if not isinstance(href, str) or not href:
                continue
            local_path, status = _cache_signed_hls_asset(
                href,
                collection=collection,
                tokens=tokens,
                cache_dir=cache_dir / collection / item_id,
                timeout=timeout,
            )
            local_bands[band] = str(local_path)
            completed += 1
            if status == "hit":
                cache_hits += 1
            elif status == "download":
                downloads += 1
            elif status == "redownload":
                redownloads += 1
            if total_assets and (completed % progress_interval == 0 or completed == total_assets):
                elapsed = max(time.monotonic() - start_time, 0.001)
                rate = completed / elapsed
                remaining = max(total_assets - completed, 0)
                eta = remaining / rate if rate > 0 else 0.0
                percent = (completed / total_assets) * 100.0
                log_progress(
                    LOGGER,
                    phase="process",
                    step="hls asset cache",
                    current=completed,
                    total=total_assets,
                    percent=round(percent, 1),
                    elapsed=_format_duration(elapsed),
                    eta=_format_duration(eta),
                    extra={"progress_bar": _format_progress_bar(completed, total_assets)},
                )
        scene["bands"] = local_bands
        qa_href = scene.get("qa_asset")
        if isinstance(qa_href, str) and qa_href:
            try:
                qa_path, _ = _cache_signed_hls_asset(
                    qa_href,
                    collection=collection,
                    tokens=tokens,
                    cache_dir=cache_dir / collection / item_id,
                    timeout=timeout,
                )
                scene["qa_local"] = str(qa_path)
            except Exception as exc:  # pragma: no cover - network/IO failure
                LOGGER.warning("hls Fmask cache failed", extra={"item": item_id, "error": str(exc)})
        updated.append(scene)
    if total_assets:
        hit_rate = cache_hits / total_assets
        redownload_rate = redownloads / total_assets
        LOGGER.info(
            "hls asset cache summary",
            extra={
                "assets": total_assets,
                "hits": cache_hits,
                "downloads": downloads,
                "redownloads": redownloads,
                "hit_rate": round(hit_rate, 4),
                "redownload_rate": round(redownload_rate, 4),
            },
        )
    return updated


def _cache_signed_hls_asset(
    href: str,
    *,
    collection: str,
    tokens: Dict[str, str],
    cache_dir: Path,
    timeout: int,
) -> tuple[Path, str]:
    """Cache one HLS asset, renewing the SAS token once if it was refused.

    The manifest's hrefs carry the token issued when it was built, which MPC
    may hand out close to its expiry; caching a region takes hours, longer
    than one token lasts. On a 403 a fresh token is fetched for the
    collection and used for this asset and every later one in the run, so a
    long run renews as often as it needs. A 403 with the fresh token is raised.
    """
    token = tokens.get(collection)
    url = append_sas_token(_strip_query(href), token) if token else href
    try:
        return _cache_hls_asset(url, cache_dir=cache_dir, timeout=timeout)
    except HTTPError as exc:
        if exc.code != 403:
            raise
    tokens[collection] = fetch_sas_token(collection, timeout=timeout)
    LOGGER.info("hls sas token refused; renewed", extra={"collection": collection})
    url = append_sas_token(_strip_query(href), tokens[collection])
    return _cache_hls_asset(url, cache_dir=cache_dir, timeout=timeout)


def _cache_sentinel2_assets(
    scenes: Sequence[Dict[str, object]],
    *,
    cache_dir: Path,
    timeout: int,
    config: Sentinel2Config,
) -> List[Dict[str, object]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    total_assets = 0
    for scene in scenes:
        assets = scene.get("assets")
        if isinstance(assets, dict):
            total_assets += len([href for href in assets.values() if isinstance(href, str) and href])
    start_time = time.monotonic()
    completed = 0
    progress_interval = 10
    cache_hits = 0
    downloads = 0
    redownloads = 0
    failures = 0
    tokens: Dict[str, str] = {}
    updated: List[Dict[str, object]] = []
    for scene in scenes:
        collection = scene.get("collection_id") or config.collection
        item_id = scene.get("item_id")
        if not isinstance(collection, str) or not isinstance(item_id, str):
            continue
        assets = scene.get("assets")
        if not isinstance(assets, dict):
            continue
        token = tokens.get(collection)
        if token is None:
            token = fetch_sas_token(collection, timeout=timeout)
            tokens[collection] = token
        local_assets: Dict[str, str] = {}
        for asset_name, href in assets.items():
            if not isinstance(href, str) or not href:
                continue
            unsigned = _strip_query(href)
            signed = append_sas_token(unsigned, token)
            local_path, status = _cache_sentinel2_asset(
                signed,
                cache_dir=cache_dir / collection / item_id,
                timeout=timeout,
                asset_name=str(asset_name),
            )
            if status == "failed":
                token = fetch_sas_token(collection, timeout=timeout)
                tokens[collection] = token
                signed = append_sas_token(unsigned, token)
                local_path, status = _cache_sentinel2_asset(
                    signed,
                    cache_dir=cache_dir / collection / item_id,
                    timeout=timeout,
                    asset_name=str(asset_name),
                )
            completed += 1
            if status == "hit":
                cache_hits += 1
            elif status == "download":
                downloads += 1
            elif status == "redownload":
                redownloads += 1
            elif status == "failed":
                failures += 1
            if local_path is not None:
                local_assets[asset_name] = str(local_path)
            if total_assets and (completed % progress_interval == 0 or completed == total_assets):
                elapsed = max(time.monotonic() - start_time, 0.001)
                rate = completed / elapsed
                remaining = max(total_assets - completed, 0)
                eta = remaining / rate if rate > 0 else 0.0
                percent = (completed / total_assets) * 100.0
                log_progress(
                    LOGGER,
                    phase="process",
                    step="sentinel2 asset cache",
                    current=completed,
                    total=total_assets,
                    percent=round(percent, 1),
                    elapsed=_format_duration(elapsed),
                    eta=_format_duration(eta),
                    extra={"progress_bar": _format_progress_bar(completed, total_assets)},
                )
        if local_assets:
            scene["assets"] = local_assets
            updated.append(scene)
    if total_assets:
        hit_rate = cache_hits / total_assets
        redownload_rate = redownloads / total_assets
        failure_rate = failures / total_assets
        LOGGER.info(
            "sentinel2 asset cache summary",
            extra={
                "assets": total_assets,
                "hits": cache_hits,
                "downloads": downloads,
                "redownloads": redownloads,
                "failures": failures,
                "hit_rate": round(hit_rate, 4),
                "redownload_rate": round(redownload_rate, 4),
                "failure_rate": round(failure_rate, 4),
            },
        )
    return updated


def _format_progress_bar(current: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(round(ratio * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _cache_hls_asset(url: str, *, cache_dir: Path, timeout: int) -> tuple[Path, str]:
    from urllib.parse import urlsplit

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = urlsplit(url).path
    filename = Path(path).name or "asset.tif"
    destination = cache_dir / filename
    redownloaded = False
    if destination.exists():
        if _is_valid_hls_asset(destination):
            LOGGER.info(
                "hls asset cache hit",
                extra={"url": url, "path": str(destination)},
            )
            return destination, "hit"
        quarantined = _quarantine_hls_asset(destination, reason="invalid cache")
        LOGGER.warning(
            "hls asset cache invalid; quarantined",
            extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
        )
        redownloaded = True
    LOGGER.info("downloading hls asset", extra={"url": url, "path": str(destination)})
    temp_path = destination.with_suffix(destination.suffix + ".part")
    with urlopen(url, timeout=timeout) as response, temp_path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    if not _is_valid_hls_asset(temp_path):
        quarantined = _quarantine_hls_asset(temp_path, reason="invalid download")
        LOGGER.warning(
            "hls asset download invalid; quarantined",
            extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
        )
        raise RuntimeError(f"Downloaded HLS asset is invalid: {destination}")
    temp_path.replace(destination)
    status = "redownload" if redownloaded else "download"
    return destination, status


def _cache_sentinel2_asset(
    url: str,
    *,
    cache_dir: Path,
    timeout: int,
    asset_name: str,
) -> tuple[Optional[Path], str]:
    from urllib.error import HTTPError
    from urllib.parse import urlsplit

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = urlsplit(url).path
    filename = Path(path).name or f"{asset_name}.tif"
    destination = cache_dir / filename
    redownloaded = False
    if destination.exists():
        if _is_valid_sentinel2_asset(destination):
            LOGGER.info(
                "sentinel2 asset cache hit",
                extra={"url": url, "path": str(destination)},
            )
            return destination, "hit"
        elif _aria2c_available() and destination.with_suffix(destination.suffix + ".aria2").exists():
            LOGGER.info(
                "sentinel2 asset resume pending",
                extra={"path": str(destination)},
            )
        else:
            quarantined = _quarantine_hls_asset(destination, reason="invalid cache")
            LOGGER.warning(
                "sentinel2 asset cache invalid; quarantined",
                extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
            )
            redownloaded = True
    LOGGER.info("downloading sentinel2 asset", extra={"url": url, "path": str(destination)})
    temp_path = destination.with_suffix(destination.suffix + ".part")
    temp_path.unlink(missing_ok=True)
    use_aria2c = _aria2c_available()
    try:
        if use_aria2c:
            try:
                _download_with_aria2(url, destination, timeout=timeout)
            except RuntimeError as exc:
                LOGGER.warning(
                    "sentinel2 asset download failed",
                    extra={"url": url, "path": str(destination), "error": str(exc)},
                )
                return None, "failed"
        else:
            temp_path.unlink(missing_ok=True)
            with urlopen(url, timeout=timeout) as response, temp_path.open("wb") as handle:
                total_size = None
                try:
                    header_value = response.getheader("Content-Length")
                    if header_value:
                        total_size = int(header_value)
                except (ValueError, TypeError):
                    total_size = None
                downloaded = 0
                log_threshold = 10 * 1024 * 1024
                next_log = log_threshold
                start_time = time.monotonic()
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= next_log:
                        elapsed = max(time.monotonic() - start_time, 0.001)
                        rate = downloaded / elapsed
                        percent = None
                        if total_size:
                            percent = (downloaded / total_size) * 100.0
                        log_progress(
                            LOGGER,
                            phase="process",
                            step="sentinel2 asset download",
                            current=downloaded,
                            total=total_size,
                            percent=round(percent, 1) if percent is not None else None,
                            elapsed=_format_duration(elapsed),
                            eta=_format_duration((total_size - downloaded) / rate) if total_size and rate > 0 else None,
                            extra={"path": str(destination), "bytes_per_sec": round(rate, 1)},
                        )
                        next_log += log_threshold
    except HTTPError as exc:
        LOGGER.warning(
            "sentinel2 asset download failed",
            extra={"url": url, "path": str(destination), "status": exc.code},
        )
        if temp_path.exists():
            temp_path.unlink()
        return None, "failed"
    except OSError as exc:
        LOGGER.warning(
            "sentinel2 asset download failed",
            extra={"url": url, "path": str(destination), "error": str(exc)},
        )
        if temp_path.exists():
            temp_path.unlink()
        return None, "failed"
    downloaded_path = destination if use_aria2c else temp_path
    if not _is_valid_sentinel2_asset(downloaded_path):
        quarantined = _quarantine_hls_asset(downloaded_path, reason="invalid download")
        LOGGER.warning(
            "sentinel2 asset download invalid; quarantined",
            extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
        )
        return None, "failed"
    if not use_aria2c:
        temp_path.replace(destination)
    status = "redownload" if redownloaded else "download"
    return destination, status


def _aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def _download_with_aria2(url: str, destination: Path, *, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "aria2c",
        "--continue=true",
        "--allow-overwrite=false",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--remove-control-file=false",
        "--auto-save-interval=5",
        "--summary-interval=10",
        "--console-log-level=warn",
        f"--timeout={timeout}",
        f"--connect-timeout={timeout}",
        "--dir",
        str(destination.parent),
        "--out",
        destination.name,
        url,
    ]
    LOGGER.info("sentinel2 asset download via aria2c", extra={"command": " ".join(command)})
    try:
        subprocess.run(command, check=True)  # pragma: no cover - requires aria2c
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"aria2c failed for {url}") from exc


def _quarantine_hls_asset(path: Path, *, reason: str) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    quarantined = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        path.rename(quarantined)
    except FileNotFoundError:
        return path
    LOGGER.info(
        "hls asset quarantined",
        extra={"path": str(path), "quarantine": str(quarantined), "reason": reason},
    )
    return quarantined


def _sample_raster_tiles_readable(path: Path) -> Optional[bool]:
    """Whether the raster opens and its sample tiles read without error.

    Returns None when GDAL is unavailable (caller should skip validation).
    Crucially, ANY GDAL failure returns False rather than propagating: with
    exceptions enabled (the build calls ``gdal.UseExceptions()``), ``Open`` or
    ``ReadRaster`` on a truncated/corrupt asset *raises* (e.g.
    ``TIFFReadEncodedTile() failed``) instead of returning None. Catching it and
    reporting "invalid" lets the caller resume the download (aria2c --continue
    from the kept ``.aria2`` control file) or re-fetch, instead of crashing the
    whole build.
    """
    try:
        from osgeo import gdal
    except Exception:
        return None
    try:
        gdal.ErrorReset()
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
        if dataset is None:
            return False
        band = dataset.GetRasterBand(1)
        if band is None:
            return False
        block_x, block_y = band.GetBlockSize()
        xsize = band.XSize
        ysize = band.YSize
        if block_x <= 0 or block_y <= 0 or xsize <= 0 or ysize <= 0:
            return False
        tiles_x = math.ceil(xsize / block_x)
        tiles_y = math.ceil(ysize / block_y)
        sample_tiles = {
            (0, 0),
            (tiles_x // 2, 0),
            (0, tiles_y // 2),
            (tiles_x // 2, tiles_y // 2),
            (tiles_x - 1, tiles_y - 1),
        }
        for tx, ty in sample_tiles:
            xoff = min(max(tx * block_x, 0), xsize - 1)
            yoff = min(max(ty * block_y, 0), ysize - 1)
            width = min(block_x, xsize - xoff)
            height = min(block_y, ysize - yoff)
            gdal.ErrorReset()
            sample = band.ReadRaster(xoff, yoff, width, height)
            if sample is None or gdal.GetLastErrorType() != 0:
                return False
    except Exception:
        return False
    return True


def _is_valid_hls_asset(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    result = _sample_raster_tiles_readable(path)
    return True if result is None else result


def _is_valid_sentinel2_asset(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    result = _sample_raster_tiles_readable(path)
    return True if result is None else result


def _is_valid_raster(path: Path) -> bool:
    """Whether ``path`` is a readable raster with at least one band.

    Used to decide whether an existing output can be reused. A truncated or
    corrupt file must count as invalid so it is rebuilt, and must not raise
    whether or not GDAL exceptions are enabled. Without GDAL installed the
    file is trusted if it exists and is non-empty.
    """
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        from osgeo import gdal
    except Exception:
        return True
    try:
        gdal.ErrorReset()
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
        if dataset is None:
            return False
        if dataset.RasterXSize <= 0 or dataset.RasterYSize <= 0:
            return False
        return dataset.RasterCount > 0 and dataset.GetRasterBand(1) is not None
    except Exception:
        return False

"""OpenAerialMap (OAM) acquisition helpers.

OAM publishes open, often very recent, high-resolution orthophotos as Cloud
Optimized GeoTIFFs on S3. The metadata API returns footprints with a COG URL
(``uuid``), ground sample distance (``gsd``), and license. This module queries
the API, selects the best items for an AOI, and builds the gdalwarp command that
mosaics them into an AOI COG. Parsing and command construction are pure (unit
tested); the HTTP query needs the network and gdalwarp needs GDAL.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

OAM_META_ENDPOINT = "https://api.openaerialmap.org/meta"
_EQUATOR_M_PER_PX_Z0 = 156543.03392804097
_MAX_ZOOM = 24


@dataclass(frozen=True)
class OAMItem:
    """A single OpenAerialMap image."""

    cog_url: str
    gsd: float
    bbox: Tuple[float, float, float, float]
    acquisition_start: Optional[str] = None
    license: Optional[str] = None


def gsd_to_zoom(gsd_m: float) -> int:
    """Web Mercator zoom whose equatorial tile resolution matches the GSD."""
    if gsd_m is None or gsd_m <= 0:
        return _MAX_ZOOM
    zoom = math.floor(math.log2(_EQUATOR_M_PER_PX_Z0 / gsd_m))
    return max(0, min(zoom, _MAX_ZOOM))


def parse_oam_results(payload: Mapping[str, Any]) -> List[OAMItem]:
    items: List[OAMItem] = []
    for raw in payload.get("results", []) or []:
        if not isinstance(raw, Mapping):
            continue
        url = raw.get("uuid")
        bbox = raw.get("bbox")
        gsd = raw.get("gsd")
        if not url or not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or gsd is None:
            continue
        props = raw.get("properties") or {}
        items.append(
            OAMItem(
                cog_url=str(url),
                gsd=float(gsd),
                bbox=tuple(float(v) for v in bbox),  # type: ignore[arg-type]
                acquisition_start=raw.get("acquisition_start"),
                license=props.get("license") if isinstance(props, Mapping) else None,
            )
        )
    return items


def select_items(
    items: Sequence[OAMItem],
    *,
    max_items: Optional[int] = None,
    max_gsd: Optional[float] = None,
) -> List[OAMItem]:
    """Keep the finest-resolution, most recent items (optionally capped)."""
    candidates = [i for i in items if max_gsd is None or i.gsd <= max_gsd]
    candidates.sort(key=lambda i: (i.gsd, _recency_key(i)))
    if max_items is not None and max_items > 0:
        candidates = candidates[:max_items]
    return candidates


def _recency_key(item: OAMItem) -> str:
    # Newer first: sort descending by sorting on the negated string is awkward,
    # so invert via a sentinel; ISO timestamps sort lexicographically.
    return "" if item.acquisition_start is None else _invert_iso(item.acquisition_start)


def _invert_iso(value: str) -> str:
    # Map each digit so later dates sort first under ascending order.
    return "".join(chr(ord("9") - (ord(c) - ord("0"))) if c.isdigit() else c for c in value)


def query_oam(
    bbox: Tuple[float, float, float, float],
    *,
    limit: int = 100,
    timeout: int = 60,
    session: Optional[Any] = None,
) -> List[OAMItem]:
    """Query the OAM metadata API for images intersecting bbox."""
    import requests  # local import keeps parsing usable without the dependency

    http = session or requests
    params = {"bbox": ",".join(str(v) for v in bbox), "limit": limit}
    response = http.get(OAM_META_ENDPOINT, params=params, timeout=timeout)
    response.raise_for_status()
    return parse_oam_results(response.json())


def oam_cache_path(item: OAMItem, cache_dir: Path) -> Path:
    """Deterministic local path for a cached OAM COG.

    Planetarble caches the whole COG so the same imagery is never re-fetched.
    The name is a hash of the source URL plus the original basename, so it is
    stable across runs and unique per item.
    """
    digest = hashlib.sha1(item.cog_url.encode("utf-8")).hexdigest()[:12]
    stem = Path(item.cog_url.split("?", 1)[0]).stem or "cog"
    return cache_dir / f"{digest}_{stem}.tif"


def oam_download_command(item: OAMItem, dest: Path, *, aria2c: str = "aria2c") -> List[str]:
    """aria2 command to download a whole COG sequentially (resumable, cached).

    A plain sequential download is far cheaper than warping over /vsicurl, which
    issues many random Range reads; once the file is cached, re-tiling never
    touches the network again.
    """
    dest = Path(dest)
    return [
        aria2c,
        "-c",  # continue/resume a partial file
        "-x", "4",
        "-s", "4",
        "--auto-file-renaming=false",
        "--allow-overwrite=false",
        "-d", str(dest.parent),
        "-o", dest.name,
        item.cog_url,
    ]


def build_local_warp_command(
    items: Sequence[OAMItem],
    *,
    cache_dir: Path,
    aoi_bbox: Tuple[float, float, float, float],
    output_path: str,
    gdalwarp: str = "gdalwarp",
    resampling: str = "cubic",
) -> List[str]:
    """gdalwarp the locally cached COGs into an AOI COG (no network reads)."""
    if not items:
        raise ValueError("no OAM items to warp")
    # OAM footprints are usually far smaller than the AOI; clip the warp extent
    # to AOI intersect (union of footprints) so we do not allocate an enormous
    # mostly-nodata raster at the source's fine resolution.
    u_minx = min(i.bbox[0] for i in items)
    u_miny = min(i.bbox[1] for i in items)
    u_maxx = max(i.bbox[2] for i in items)
    u_maxy = max(i.bbox[3] for i in items)
    minx = max(aoi_bbox[0], u_minx)
    miny = max(aoi_bbox[1], u_miny)
    maxx = min(aoi_bbox[2], u_maxx)
    maxy = min(aoi_bbox[3], u_maxy)
    if minx >= maxx or miny >= maxy:
        raise ValueError("AOI does not intersect any OAM item footprint")
    command: List[str] = [
        gdalwarp,
        "-overwrite",
        "-t_srs", "EPSG:3857",
        "-te_srs", "EPSG:4326",
        "-te", str(minx), str(miny), str(maxx), str(maxy),
        "-r", resampling,
        "-of", "COG",
        "-co", "COMPRESS=WEBP",
        "-co", "OVERVIEWS=AUTO",
    ]
    for item in items:
        command.append(str(oam_cache_path(item, cache_dir)))
    command.append(output_path)
    return command

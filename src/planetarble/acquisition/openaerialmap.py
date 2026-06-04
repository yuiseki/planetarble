"""OpenAerialMap (OAM) acquisition helpers.

OAM publishes open, often very recent, high-resolution orthophotos as Cloud
Optimized GeoTIFFs on S3. The metadata API returns footprints with a COG URL
(``uuid``), ground sample distance (``gsd``), and license. This module queries
the API, selects the best items for an AOI, and builds the gdalwarp command that
mosaics them into an AOI COG. Parsing and command construction are pure (unit
tested); the HTTP query needs the network and gdalwarp needs GDAL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
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


def build_oam_warp_command(
    items: Sequence[OAMItem],
    *,
    aoi_bbox: Tuple[float, float, float, float],
    output_path: str,
    gdalwarp: str = "gdalwarp",
    resampling: str = "cubic",
) -> List[str]:
    """gdalwarp the selected COGs (streamed via /vsicurl/) into an AOI COG."""
    if not items:
        raise ValueError("no OAM items to warp")
    minx, miny, maxx, maxy = aoi_bbox
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
        command.append(f"/vsicurl/{item.cog_url}")
    command.append(output_path)
    return command

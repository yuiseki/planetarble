"""Helpers for accessing Sentinel-2 imagery via Microsoft Planetary Computer."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

from planetarble.logging import get_logger


LOGGER = get_logger(__name__)

STAC_SEARCH_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS_TOKEN_ENDPOINT_TEMPLATE = (
    "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}?token=anon"
)
DEFAULT_COLLECTION = "sentinel-2-l2a"


class MPCError(RuntimeError):
    """Raised when Microsoft Planetary Computer requests fail."""


@dataclass
class MPCScene:
    """Represent a Sentinel-2 scene selected from MPC search."""

    collection: str
    item_id: str
    visual_href: str
    cloud_cover: Optional[float] = None


def fetch_true_color_tile(
    *,
    lat: float,
    lon: float,
    width_m: float,
    height_m: float,
    output_path: Path,
    max_cloud: Optional[float] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    gdal_translate: str = "gdal_translate",
    timeout: int = 60,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Download a clipped Sentinel-2 True Color tile around a point.

    The function searches MPC STAC for a low-cloud Sentinel-2 L2A scene covering
    the requested point, signs the visual (RGB) COG asset using the anonymous SAS
    token, and invokes ``gdal_translate`` to clip the requested window. GDAL will
    request only the required byte ranges from the COG, so downloaded data is
    limited to the requested footprint.
    """

    bbox = _bbox_from_point(lat=lat, lon=lon, width_m=width_m, height_m=height_m)
    LOGGER.info(
        "mpc stac search",
        extra={
            "lat": lat,
            "lon": lon,
            "width_m": width_m,
            "height_m": height_m,
            "bbox": bbox,
            "max_cloud": max_cloud,
            "start": start_datetime,
            "end": end_datetime,
        },
    )
    scene = _select_scene(
        bbox=bbox,
        max_cloud=max_cloud,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        timeout=timeout,
    )
    LOGGER.info(
        "mpc scene selected",
        extra={
            "item_id": scene.item_id,
            "collection": scene.collection,
            "cloud_cover": scene.cloud_cover,
        },
    )
    sas_token = _fetch_sas_token(scene.collection, timeout=timeout)
    LOGGER.info(
        "mpc sas token acquired",
        extra={
            "collection": scene.collection,
        },
    )
    signed_url = _append_token(scene.visual_href, sas_token)

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = _build_gdal_command(
        gdal_translate=gdal_translate,
        signed_url=signed_url,
        bbox=bbox,
        destination=output_path,
    )

    LOGGER.info(
        "mpc clipping command",
        extra={
            "command": " ".join(command),
            "bbox": bbox,
            "item": scene.item_id,
            "collection": scene.collection,
        },
    )
    if not dry_run:
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - requires GDAL runtime
            raise MPCError(f"gdal_translate failed: {exc}") from exc
        LOGGER.info(
            "mpc clip complete",
            extra={
                "output": str(output_path),
            },
        )

    return {
        "output": str(output_path),
        "bbox": bbox,
        "item_id": scene.item_id,
        "collection": scene.collection,
        "cloud_cover": scene.cloud_cover,
        "signed_url": signed_url if dry_run else None,
    }


def _select_scene(
    *,
    bbox: Iterable[float],
    max_cloud: Optional[float],
    start_datetime: Optional[str],
    end_datetime: Optional[str],
    timeout: int,
) -> MPCScene:
    body: Dict[str, object] = {
        "collections": [DEFAULT_COLLECTION],
        "bbox": list(bbox),
        "limit": 1,
        "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}],
    }
    query: Dict[str, Dict[str, object]] = {}
    if max_cloud is not None:
        query["eo:cloud_cover"] = {"lte": max_cloud}
    if query:
        body["query"] = query
    if start_datetime and end_datetime:
        body["datetime"] = f"{start_datetime}/{end_datetime}"
    elif start_datetime:
        body["datetime"] = f"{start_datetime}/.."
    elif end_datetime:
        body["datetime"] = f"../{end_datetime}"

    try:
        response = requests.post(STAC_SEARCH_ENDPOINT, json=body, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        raise MPCError(f"Failed to query MPC STAC: {exc}") from exc
    if response.status_code != 200:
        raise MPCError(
            f"MPC STAC search failed: {response.status_code} {response.text.strip()}"
        )
    payload = response.json()
    LOGGER.debug(
        "mpc stac response",
        extra={"matched": len(payload.get("features") or [])},
    )
    features = payload.get("features") or []
    if not features:
        raise MPCError("No Sentinel-2 scenes found for the requested area")

    feature = features[0]
    assets = feature.get("assets") or {}
    visual = assets.get("visual")
    if not visual or "href" not in visual:
        raise MPCError("Selected scene does not expose a visual asset")

    return MPCScene(
        collection=feature.get("collection", DEFAULT_COLLECTION),
        item_id=feature.get("id", "unknown"),
        visual_href=visual["href"],
        cloud_cover=_safe_float(feature.get("properties", {}).get("eo:cloud_cover")),
    )


def _fetch_sas_token(collection: str, *, timeout: int) -> str:
    endpoint = SAS_TOKEN_ENDPOINT_TEMPLATE.format(collection=collection)
    try:
        response = requests.get(endpoint, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        raise MPCError(f"Failed to request MPC SAS token: {exc}") from exc
    if response.status_code != 200:
        raise MPCError(
            f"SAS token request failed: {response.status_code} {response.text.strip()}"
        )
    payload = response.json()
    token = payload.get("token")
    if not token:
        raise MPCError("SAS token response missing 'token' field")
    return token


def _append_token(href: str, token: str) -> str:
    parsed = urlsplit(href)
    query = parsed.query
    token_query = token
    if token_query.startswith("?"):
        token_query = token_query[1:]
    if query:
        new_query = f"{query}&{token_query}"
    else:
        new_query = token_query
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _build_gdal_command(
    *,
    gdal_translate: str,
    signed_url: str,
    bbox: Iterable[float],
    destination: Path,
) -> list[str]:
    minx, miny, maxx, maxy = bbox
    return [
        gdal_translate,
        "-projwin",
        str(minx),
        str(maxy),
        str(maxx),
        str(miny),
        "-projwin_srs",
        "EPSG:4326",
        "-of",
        "COG",
        "-co",
        "COMPRESS=JPEG",
        "-co",
        "QUALITY=95",
        signed_url,
        str(destination),
    ]


def _bbox_from_point(*, lat: float, lon: float, width_m: float, height_m: float) -> tuple[float, float, float, float]:
    if width_m <= 0 or height_m <= 0:
        raise ValueError("width_m and height_m must be positive")
    half_height = height_m / 2.0
    half_width = width_m / 2.0
    delta_lat = half_height / 111_320.0
    cos_lat = math.cos(math.radians(lat))
    meters_per_degree_lon = max(1e-6, 111_320.0 * cos_lat)
    delta_lon = half_width / meters_per_degree_lon
    min_lat = max(-90.0, lat - delta_lat)
    max_lat = min(90.0, lat + delta_lat)
    min_lon = max(-180.0, lon - delta_lon)
    max_lon = min(180.0, lon + delta_lon)
    return (min_lon, min_lat, max_lon, max_lat)


def _safe_float(value: object) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

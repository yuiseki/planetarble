"""Utilities for authenticating with the Copernicus Data Space Ecosystem."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from planetarble.logging import get_logger
from planetarble.core.models import CopernicusConfig, CopernicusLayerConfig

LOGGER = get_logger(__name__)

TOKEN_ENDPOINT = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
WMS_ENDPOINT_TEMPLATE = "https://sh.dataspace.copernicus.eu/ogc/wms/{instance_id}"
NAMESPACES = {
    "wms": "http://www.opengis.net/wms",
}
ORIGIN_SHIFT = 20037508.342789244
FORMAT_EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


class CopernicusCredentialsMissing(RuntimeError):
    """Raised when required Copernicus credentials are not provided."""


class CopernicusAuthError(RuntimeError):
    """Raised when Copernicus authentication fails."""


class CopernicusAccessError(RuntimeError):
    """Raised when Copernicus resource access fails."""


@dataclass(frozen=True)
class CopernicusCredentials:
    """Hold the credential values required for CDSE access."""

    instance_id: str
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> "CopernicusCredentials":
        instance_id = os.getenv("COPERNICUS_INSTANCE_ID")
        client_id = os.getenv("COPERNICUS_CLIENT_ID")
        client_secret = os.getenv("COPERNICUS_CLIENT_SECRET")
        if not instance_id or not client_id or not client_secret:
            raise CopernicusCredentialsMissing(
                "COPERNICUS_INSTANCE_ID, COPERNICUS_CLIENT_ID, and COPERNICUS_CLIENT_SECRET must be set"
            )
        return cls(instance_id=instance_id, client_id=client_id, client_secret=client_secret)


def request_access_token(credentials: CopernicusCredentials, *, timeout: int = 30) -> str:
    """Return an OAuth access token using the client credentials grant."""

    payload = {
        "grant_type": "client_credentials",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
    LOGGER.debug("requesting copernicus token")
    try:
        response = requests.post(TOKEN_ENDPOINT, data=payload, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        raise CopernicusAuthError(f"Copernicus token request error: {exc}") from exc
    if response.status_code != 200:
        raise CopernicusAuthError(
            f"Copernicus token request failed: {response.status_code} {response.text.strip()}"
        )
    token = response.json().get("access_token")
    if not token:
        raise CopernicusAuthError("Copernicus token response missing access_token")
    return token


def verify_wms_access(credentials: CopernicusCredentials, *, timeout: int = 30) -> bool:
    """Perform a GetCapabilities request to confirm WMS access."""

    token = request_access_token(credentials, timeout=timeout)
    endpoint = WMS_ENDPOINT_TEMPLATE.format(instance_id=credentials.instance_id)
    params = {"service": "WMS", "request": "GetCapabilities"}
    headers = {"Authorization": f"Bearer {token}"}
    LOGGER.debug("requesting copernicus wms", extra={"endpoint": endpoint})
    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        raise CopernicusAccessError(f"Copernicus WMS request error: {exc}") from exc
    if response.status_code != 200:
        raise CopernicusAccessError(
            f"Copernicus WMS request failed: {response.status_code} {response.text.strip()}"
        )
    return True


def verify_copernicus_connection() -> bool:
    """High-level helper to validate credentials and WMS access."""

    credentials = CopernicusCredentials.from_env()
    return verify_wms_access(credentials)


def fetch_wms_capabilities(
    *,
    instance_id: str,
    token: Optional[str] = None,
    timeout: int = 60,
) -> str:
    """Fetch the WMS GetCapabilities document for the provided instance."""

    endpoint = WMS_ENDPOINT_TEMPLATE.format(instance_id=instance_id)
    params = {"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"}
    headers = {"Authorization": f"Bearer {token}"} if token else None
    LOGGER.debug(
        "fetching wms capabilities",
        extra={"endpoint": endpoint, "authorized": bool(token)},
    )
    try:
        response = requests.get(endpoint, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        raise CopernicusAccessError(f"Copernicus WMS request error: {exc}") from exc
    if response.status_code != 200:
        raise CopernicusAccessError(
            f"Copernicus WMS request failed: {response.status_code} {response.text.strip()}"
        )
    return response.text


def list_wms_layers(capabilities_xml: str) -> list[tuple[str, str]]:
    """Extract all layer (name, title) pairs from a GetCapabilities XML payload."""

    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(capabilities_xml)
    except ET.ParseError as exc:  # pragma: no cover - malformed XML path
        raise CopernicusAccessError(f"Failed to parse WMS capabilities: {exc}") from exc

    layers: list[tuple[str, str]] = []
    # Nested layers live under Capability/Layer
    capability = root.find("wms:Capability", NAMESPACES)
    if capability is None:
        return layers
    for layer in capability.iterfind(".//wms:Layer", NAMESPACES):
        name_elem = layer.find("wms:Name", NAMESPACES)
        title_elem = layer.find("wms:Title", NAMESPACES)
        if name_elem is None or not name_elem.text:
            continue
        name = name_elem.text.strip()
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else name
        layers.append((name, title))
    return layers


def get_available_layers(
    *,
    instance_id: Optional[str] = None,
    use_credentials: bool = True,
    timeout: int = 60,
) -> list[tuple[str, str]]:
    """Return a list of available layers for the configured Copernicus instance."""

    instance = instance_id or os.getenv("COPERNICUS_INSTANCE_ID")
    if not instance:
        raise CopernicusCredentialsMissing("COPERNICUS_INSTANCE_ID must be set to list layers")

    token: Optional[str] = None
    if use_credentials:
        try:
            credentials = CopernicusCredentials.from_env()
        except CopernicusCredentialsMissing:
            credentials = None
        if credentials:
            try:
                token = request_access_token(credentials, timeout=timeout)
            except CopernicusAuthError as exc:
                LOGGER.warning("copernicus token unavailable, falling back to anonymous request", extra={"error": str(exc)})
                token = None

    xml_payload = fetch_wms_capabilities(instance_id=instance, token=token, timeout=timeout)
    return list_wms_layers(xml_payload)


def download_tiles(
    credentials: CopernicusCredentials,
    config: CopernicusConfig,
    destination: Path,
    *,
    force: bool = False,
) -> List[Dict[str, object]]:
    """Download Sentinel-2 tiles for the configured bbox and zoom range."""

    if not config.layers:
        LOGGER.info("copernicus acquisition enabled but no layers configured")
        return []

    destination.mkdir(parents=True, exist_ok=True)
    timeout = max(5, config.timeout_seconds)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Planetarble/0.1",
    })

    token = request_access_token(credentials, timeout=timeout)
    session.headers.update({"Authorization": f"Bearer {token}"})

    base_url = WMS_ENDPOINT_TEMPLATE.format(instance_id=credentials.instance_id)

    summaries: List[Dict[str, object]] = []

    capabilities_path = destination / "capabilities.xml"
    try:
        if force or not capabilities_path.exists():
            capabilities_xml = fetch_wms_capabilities(
                instance_id=credentials.instance_id,
                token=token,
                timeout=timeout,
            )
            capabilities_path.write_text(capabilities_xml, encoding="utf-8")
    except CopernicusAccessError as exc:
        LOGGER.warning("copernicus capabilities fetch skipped", extra={"error": str(exc)})

    for layer in config.layers:
        summary = _download_layer_tiles(
            session=session,
            credentials=credentials,
            base_url=base_url,
            layer_config=layer,
            config=config,
            destination=destination,
            force=force,
            timeout=timeout,
        )
        summaries.append(summary)

    session.close()
    return summaries


def _download_layer_tiles(
    *,
    session: requests.Session,
    credentials: CopernicusCredentials,
    base_url: str,
    layer_config: CopernicusLayerConfig,
    config: CopernicusConfig,
    destination: Path,
    force: bool,
    timeout: int,
) -> Dict[str, object]:
    slug = _slugify(layer_config.output or layer_config.name)
    layer_dir = destination / slug
    tiles_written = 0
    tiles_skipped = 0
    tiles_failed = 0
    tiles_limit_reached = False
    tile_estimate = 0
    max_tiles = config.max_tiles_per_layer

    layer_dir.mkdir(parents=True, exist_ok=True)

    for zoom in range(config.min_zoom, config.max_zoom + 1):
        x_min, x_max, y_min, y_max = _tile_range(config.bbox, zoom)
        if x_max < x_min or y_max < y_min:
            continue
        zoom_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
        tile_estimate += zoom_tiles
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                if max_tiles is not None and tiles_written >= max_tiles:
                    tiles_limit_reached = True
                    break
                ext = _extension_for_format(layer_config.format)
                tile_path = layer_dir / str(zoom) / str(x) / f"{y}.{ext}"
                if tile_path.exists() and not force:
                    tiles_skipped += 1
                    continue
                minx, miny, maxx, maxy = _tile_bounds(x, y, zoom)
                params = {
                    "SERVICE": "WMS",
                    "REQUEST": "GetMap",
                    "VERSION": "1.3.0",
                    "FORMAT": layer_config.format,
                    "TRANSPARENT": "false",
                    "WIDTH": str(config.tile_size),
                    "HEIGHT": str(config.tile_size),
                    "CRS": "EPSG:3857",
                    "LAYERS": layer_config.name,
                    "STYLES": layer_config.style or "",
                    "BBOX": _format_bbox(minx, miny, maxx, maxy),
                }
                if layer_config.time:
                    params["TIME"] = layer_config.time

                try:
                    response = session.get(base_url, params=params, timeout=timeout)
                except requests.RequestException as exc:  # pragma: no cover - network failure path
                    LOGGER.warning(
                        "copernicus tile request failed",
                        extra={
                            "layer": layer_config.name,
                            "zoom": zoom,
                            "x": x,
                            "y": y,
                            "error": str(exc),
                        },
                    )
                    tiles_failed += 1
                    continue

                if response.status_code == 401:
                    LOGGER.info("copernicus token expired; refreshing")
                    token = request_access_token(credentials, timeout=timeout)
                    session.headers.update({"Authorization": f"Bearer {token}"})
                    response = session.get(base_url, params=params, timeout=timeout)

                if response.status_code != 200:
                    LOGGER.warning(
                        "copernicus tile request returned %s",
                        response.status_code,
                        extra={
                            "layer": layer_config.name,
                            "zoom": zoom,
                            "x": x,
                            "y": y,
                            "body": response.text[:200],
                        },
                    )
                    tiles_failed += 1
                    continue

                tile_path.parent.mkdir(parents=True, exist_ok=True)
                tile_path.write_bytes(response.content)
                tiles_written += 1
            if tiles_limit_reached:
                break
        if tiles_limit_reached:
            break

    summary: Dict[str, object] = {
        "layer": layer_config.name,
        "output": str(layer_dir),
        "tiles_written": tiles_written,
        "tiles_skipped": tiles_skipped,
        "tiles_failed": tiles_failed,
        "tile_count_estimate": tile_estimate,
        "min_zoom": config.min_zoom,
        "max_zoom": config.max_zoom,
        "bbox": list(config.bbox),
    }
    if tiles_limit_reached:
        summary["limit_reached"] = True
    return summary


def _tile_range(bbox: Tuple[float, float, float, float], zoom: int) -> Tuple[int, int, int, int]:
    min_lon, min_lat, max_lon, max_lat = bbox
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))
    min_lat = max(-85.05112878, min(85.05112878, min_lat))
    max_lat = max(-85.05112878, min(85.05112878, max_lat))
    if max_lon < min_lon:
        min_lon, max_lon = max_lon, min_lon
    if max_lat < min_lat:
        min_lat, max_lat = max_lat, min_lat

    epsilon = 1e-9
    x_min = int(math.floor(_lon_to_tile(min_lon, zoom)))
    x_max = int(math.floor(_lon_to_tile(max_lon - epsilon, zoom)))
    y_min = int(math.floor(_lat_to_tile(max_lat - epsilon, zoom)))
    y_max = int(math.floor(_lat_to_tile(min_lat, zoom)))

    n = 2**zoom
    x_min = max(0, min(x_min, n - 1))
    x_max = max(0, min(x_max, n - 1))
    y_min = max(0, min(y_min, n - 1))
    y_max = max(0, min(y_max, n - 1))
    return x_min, x_max, y_min, y_max


def _lon_to_tile(lon: float, zoom: int) -> float:
    n = 2**zoom
    return (lon + 180.0) / 360.0 * n


def _lat_to_tile(lat: float, zoom: int) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    n = 2**zoom
    return (1 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2 * n


def _tile_bounds(x: int, y: int, zoom: int) -> Tuple[float, float, float, float]:
    n = 2**zoom
    tile_size = (ORIGIN_SHIFT * 2) / n
    minx = -ORIGIN_SHIFT + x * tile_size
    maxx = minx + tile_size
    maxy = ORIGIN_SHIFT - y * tile_size
    miny = maxy - tile_size
    return minx, miny, maxx, maxy


def _extension_for_format(fmt: str) -> str:
    normalized = fmt.lower()
    if normalized in FORMAT_EXTENSION:
        return FORMAT_EXTENSION[normalized]
    suffix = normalized.split("/")[-1]
    return suffix or "bin"


def _format_bbox(minx: float, miny: float, maxx: float, maxy: float) -> str:
    return f"{minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "layer"

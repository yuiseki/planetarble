"""Download high-resolution orthophotos from GSI (Japan) XYZ tile services."""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import requests

from planetarble.logging import get_logger


LOGGER = get_logger(__name__)

DEFAULT_TILE_TEMPLATE = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
ORIGIN_SHIFT = 20037508.342789244


class GSIError(RuntimeError):
    """Raised when fetching GSI orthophotos fails."""


@dataclass(frozen=True)
class GSITile:
    z: int
    x: int
    y: int
    url: str
    path: Path
    vrt_path: Path


def fetch_gsi_ortho_clip(
    *,
    lat: float,
    lon: float,
    width_m: float,
    height_m: float,
    output_path: Path,
    zoom: int = 19,
    tile_template: str = DEFAULT_TILE_TEMPLATE,
    gdal_translate: str = "gdal_translate",
    gdal_buildvrt: str = "gdalbuildvrt",
    gdal_warp: str = "gdalwarp",
    timeout: int = 30,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Download a clipped high-resolution orthophoto from the GSI XYZ tiles."""

    bbox = _bbox_from_point(lat=lat, lon=lon, width_m=width_m, height_m=height_m)
    tile_bounds = _tiles_for_bbox(bbox, zoom)
    if not tile_bounds:
        raise GSIError("No tiles intersect the requested area")

    LOGGER.info(
        "gsi fetch request",
        extra={
            "lat": lat,
            "lon": lon,
            "width_m": width_m,
            "height_m": height_m,
            "zoom": zoom,
            "bbox": bbox,
            "tiles": len(tile_bounds),
            "template": tile_template,
        },
    )

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        urls = [tile_template.format(z=zoom, x=x, y=y) for z, x, y in tile_bounds]
        summary = {
            "bbox": bbox,
            "tiles": len(urls),
            "urls": urls,
            "output": str(output_path),
        }
        LOGGER.info("gsi dry-run", extra=summary)
        return summary

    with tempfile.TemporaryDirectory(prefix="planetarble_gsi_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        tiles = _download_tiles(
            tmp_dir=tmp_dir,
            tile_template=tile_template,
            tile_triplets=tile_bounds,
            timeout=timeout,
        )
        _georeference_tiles(tiles=tiles, gdal_translate=gdal_translate)
        mosaic_vrt = tmp_dir / "mosaic.vrt"
        _build_mosaic_vrt(tiles=tiles, mosaic_vrt=mosaic_vrt, gdal_buildvrt=gdal_buildvrt)
        _warp_to_output(
            source_vrt=mosaic_vrt,
            bbox=bbox,
            destination=output_path,
            gdal_warp=gdal_warp,
        )

    LOGGER.info(
        "gsi clip complete",
        extra={"output": str(output_path)},
    )

    return {
        "output": str(output_path),
        "bbox": bbox,
        "zoom": zoom,
        "tiles": len(tile_bounds),
    }


def _download_tiles(
    *,
    tmp_dir: Path,
    tile_template: str,
    tile_triplets: Sequence[Tuple[int, int, int]],
    timeout: int,
) -> List[GSITile]:
    session = requests.Session()
    tiles: List[GSITile] = []

    for z, x, y in tile_triplets:
        url = tile_template.format(z=z, x=x, y=y)
        suffix = url.split(".")[-1]
        tile_path = tmp_dir / f"{z}_{x}_{y}.{suffix}"
        LOGGER.debug("downloading gsi tile", extra={"url": url, "path": str(tile_path)})
        try:
            response = session.get(url, timeout=timeout)
        except requests.RequestException as exc:  # pragma: no cover - network failure path
            raise GSIError(f"Failed to download tile {url}: {exc}") from exc
        if response.status_code != 200:
            raise GSIError(f"Failed to download tile {url}: {response.status_code}")
        tile_path.write_bytes(response.content)
        vrt_path = tile_path.with_suffix(".vrt")
        tiles.append(GSITile(z=z, x=x, y=y, url=url, path=tile_path, vrt_path=vrt_path))
    return tiles


def _georeference_tiles(*, tiles: Sequence[GSITile], gdal_translate: str) -> None:
    for tile in tiles:
        minx, miny, maxx, maxy = _tile_bounds_mercator(tile.x, tile.y, tile.z)
        command = [
            gdal_translate,
            "-of",
            "VRT",
            "-a_srs",
            "EPSG:3857",
            "-a_ullr",
            str(minx),
            str(maxy),
            str(maxx),
            str(miny),
            str(tile.path),
            str(tile.vrt_path),
        ]
        LOGGER.debug("georeferencing tile", extra={"command": " ".join(command)})
        _run(command, "georeference GSI tile")


def _build_mosaic_vrt(*, tiles: Sequence[GSITile], mosaic_vrt: Path, gdal_buildvrt: str) -> None:
    list_path = mosaic_vrt.parent / "tiles.txt"
    list_path.write_text("\n".join(str(tile.vrt_path) for tile in tiles), encoding="utf-8")
    command = [
        gdal_buildvrt,
        "-input_file_list",
        str(list_path),
        str(mosaic_vrt),
    ]
    LOGGER.debug("building mosaic vrt", extra={"command": " ".join(command)})
    _run(command, "build GSI mosaic VRT")


def _warp_to_output(
    *,
    source_vrt: Path,
    bbox: Tuple[float, float, float, float],
    destination: Path,
    gdal_warp: str,
) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    command = [
        gdal_warp,
        "-overwrite",
        "-t_srs",
        "EPSG:4326",
        "-te",
        str(min_lon),
        str(min_lat),
        str(max_lon),
        str(max_lat),
        "-te_srs",
        "EPSG:4326",
        "-r",
        "cubic",
        "-of",
        "COG",
        "-co",
        "COMPRESS=JPEG",
        "-co",
        "QUALITY=95",
        "-co",
        "BLOCKSIZE=512",
        str(source_vrt),
        str(destination),
    ]
    LOGGER.info("gsi warp command", extra={"command": " ".join(command)})
    _run(command, "warp GSI mosaic")


def _tiles_for_bbox(bbox: Tuple[float, float, float, float], zoom: int) -> List[Tuple[int, int, int]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))
    min_lat = max(-85.0511, min(85.0511, min_lat))
    max_lat = max(-85.0511, min(85.0511, max_lat))
    if max_lon < min_lon or max_lat < min_lat:
        return []
    x_min = _lon_to_tile(min_lon, zoom)
    x_max = _lon_to_tile(max_lon, zoom)
    y_min = _lat_to_tile(max_lat, zoom)
    y_max = _lat_to_tile(min_lat, zoom)
    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((zoom, x, y))
    return tiles


def _lon_to_tile(lon: float, zoom: int) -> int:
    return int((lon + 180.0) / 360.0 * (1 << zoom))


def _lat_to_tile(lat: float, zoom: int) -> int:
    lat_rad = math.radians(lat)
    n = 1 << zoom
    return int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)


def _tile_bounds_mercator(x: int, y: int, zoom: int) -> Tuple[float, float, float, float]:
    n = 2**zoom
    tile_size = (ORIGIN_SHIFT * 2) / n
    minx = -ORIGIN_SHIFT + x * tile_size
    maxx = minx + tile_size
    maxy = ORIGIN_SHIFT - y * tile_size
    miny = maxy - tile_size
    return minx, miny, maxx, maxy


def _bbox_from_point(*, lat: float, lon: float, width_m: float, height_m: float) -> Tuple[float, float, float, float]:
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


def _run(command: Sequence[str], description: str) -> None:
    import subprocess

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - external deps
        raise GSIError(f"{description} failed: {exc}") from exc

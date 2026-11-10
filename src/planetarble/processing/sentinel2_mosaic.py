"""Sentinel-2 mosaic building: scene selection, SAS URL refresh, band / visual VRTs and COG output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from planetarble.acquisition.mpc import append_sas_token, fetch_sas_token
from planetarble.core.models import Sentinel2Config

from .assets import _strip_query
from .geometry import write_geometry_geojson
from .hls_mosaic import _scene_cloud_cover
from .runner import CommandRunner

if TYPE_CHECKING:  # GDAL is optional at runtime; imported lazily where used
    from osgeo import ogr


# Bump when the mosaic algorithm changes (e.g. scene ordering / nodata) so the
# bbox-keyed cache invalidates and existing mosaics regenerate. v2: cleanest-on-
# top ordering + transparent nodata to fix cloudy results.
_SENTINEL2_MOSAIC_VERSION = "v2"


def _sentinel2_mosaic_filename(region_name: Optional[str], bbox) -> str:
    """Mosaic COG filename keyed by region, bbox AND mosaic-algorithm version.

    The mosaic is cached and skipped when a valid output already exists. Keying
    only on the region name meant that widening an overlay's AOI silently reused
    the old, smaller-footprint COG (the expansion never took effect). Folding a
    short hash of the bbox into the name gives a changed AOI a fresh filename, so
    it regenerates; an unchanged AOI keeps the same name and still skips. The
    algorithm version is part of the hash so a mosaic-logic change also forces
    regeneration instead of reusing a stale (e.g. cloudy) COG.
    """
    base = f"sentinel2_mosaic_{region_name}" if region_name else "sentinel2_mosaic"
    if bbox:
        key = hashlib.sha1(
            repr((_SENTINEL2_MOSAIC_VERSION, tuple(round(float(v), 5) for v in bbox))).encode("utf-8")
        ).hexdigest()[:8]
        return f"{base}_{key}_cog.tif"
    return f"{base}_cog.tif"


def _load_sentinel2_scene_manifest(path: Path) -> List[Dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(scenes, list):
        return []
    return [scene for scene in scenes if isinstance(scene, dict)]


def _select_mosaic_scenes(
    scenes: List[Dict[str, object]],
    max_scenes: int,
) -> List[Dict[str, object]]:
    """Keep only the lowest-cloud scenes for mosaicking.

    Every scene kept here has its full asset downloaded, and a small region can
    be covered by dozens of granules, so the manifest is trimmed to the best
    candidates. A non-positive max_scenes disables the cap.
    """
    if max_scenes <= 0 or len(scenes) <= max_scenes:
        return scenes

    def sort_key(scene: Dict[str, object]):
        cloud = scene.get("cloud_cover")
        if isinstance(cloud, (int, float)):
            return (0, float(cloud), str(scene.get("item_id", "")))
        return (1, 0.0, str(scene.get("item_id", "")))

    return sorted(scenes, key=sort_key)[:max_scenes]


def _refresh_sentinel2_scene_urls(
    scenes: Sequence[Dict[str, object]],
    config: Sentinel2Config,
) -> List[Dict[str, object]]:
    refreshed: List[Dict[str, object]] = []
    tokens: Dict[str, str] = {}
    for scene in scenes:
        collection = scene.get("collection_id") or config.collection
        if not isinstance(collection, str) or not collection:
            continue
        token = tokens.get(collection)
        if token is None:
            token = fetch_sas_token(collection, timeout=config.request_timeout_seconds)
            tokens[collection] = token
        assets = scene.get("assets")
        if isinstance(assets, dict):
            updated_assets = {}
            for asset_name, href in assets.items():
                if not isinstance(href, str) or not href:
                    continue
                updated_assets[asset_name] = append_sas_token(_strip_query(href), token)
            scene["assets"] = updated_assets
        refreshed.append(scene)
    return refreshed


def _select_sentinel2_asset_mode(assets: Sequence[str]) -> str:
    normalized = [asset.strip() for asset in assets if asset]
    if not normalized:
        raise ValueError("sentinel2.assets must contain at least one asset name")
    if len(normalized) == 1 and normalized[0].lower() == "visual":
        return "visual"
    band_set = {asset.upper() for asset in normalized}
    if band_set == {"B02", "B03", "B04"}:
        return "bands"
    raise ValueError("sentinel2.assets must be ['visual'] or ['B02','B03','B04']")


def _write_sentinel2_visual_list(
    temp_dir: Path,
    scenes: Sequence[Dict[str, object]],
    asset_name: str,
) -> Path:
    urls: List[str] = []
    # gdalbuildvrt paints the last source on top, so order cloudiest-first /
    # cleanest-last: the lowest-cloud scene wins and (with nodata transparent)
    # its gaps fall through to the next-cleanest below. Without this the
    # cloudiest scene painted over everything.
    ordered = sorted(scenes, key=_scene_cloud_cover, reverse=True)
    for scene in ordered:
        assets = scene.get("assets")
        if not isinstance(assets, dict):
            continue
        url = assets.get(asset_name)
        if isinstance(url, str) and url:
            urls.append(url)
    if not urls:
        raise ValueError("No Sentinel-2 visual assets found in manifest")
    temp_dir.mkdir(parents=True, exist_ok=True)
    list_path = temp_dir / "sentinel2_visual_sources.txt"
    list_path.write_text("\n".join(urls), encoding="utf-8")
    return list_path


def _build_sentinel2_visual_vrt(
    runner: CommandRunner,
    list_path: Path,
    temp_dir: Path,
) -> Path:
    vrt_path = temp_dir / "sentinel2_visual_mosaic.vrt"
    command = [
        "gdalbuildvrt",
        "-allow_projection_difference",
        "-resolution",
        "highest",
        # treat 0 as nodata so each scene's fill / swath-edge is transparent and
        # the cleaner scene below shows through instead of black covering it
        "-srcnodata",
        "0",
        "-vrtnodata",
        "0",
        "-input_file_list",
        str(list_path),
        str(vrt_path),
    ]
    runner.run(command, description="mosaic Sentinel-2 visual assets")
    return vrt_path


def _write_sentinel2_band_lists(
    temp_dir: Path,
    scenes: Sequence[Dict[str, object]],
    assets: Sequence[str],
) -> Dict[str, Path]:
    band_keys = {"B02": "blue", "B03": "green", "B04": "red"}
    lists: Dict[str, List[str]] = {label: [] for label in band_keys.values()}
    asset_set = {asset.upper() for asset in assets}
    for scene in scenes:
        bands = scene.get("assets")
        if not isinstance(bands, dict):
            continue
        for band, label in band_keys.items():
            if band not in asset_set:
                continue
            url = bands.get(band)
            if isinstance(url, str) and url:
                lists[label].append(url)
    paths: Dict[str, Path] = {}
    temp_dir.mkdir(parents=True, exist_ok=True)
    for label, urls in lists.items():
        if not urls:
            raise ValueError(f"No Sentinel-2 sources found for {label} band")
        list_path = temp_dir / f"sentinel2_{label}_sources.txt"
        list_path.write_text("\n".join(urls), encoding="utf-8")
        paths[label] = list_path
    return paths


def _build_sentinel2_band_vrts(
    runner: CommandRunner,
    band_lists: Dict[str, Path],
    temp_dir: Path,
) -> Dict[str, Path]:
    vrts: Dict[str, Path] = {}
    for label, list_path in band_lists.items():
        vrt_path = temp_dir / f"sentinel2_{label}_mosaic.vrt"
        command = [
            "gdalbuildvrt",
            "-allow_projection_difference",
            "-resolution",
            "highest",
            "-input_file_list",
            str(list_path),
            str(vrt_path),
        ]
        runner.run(command, description=f"mosaic Sentinel-2 {label} band")
        vrts[label] = vrt_path
    return vrts


def _build_sentinel2_rgb_vrt(
    runner: CommandRunner,
    band_vrts: Dict[str, Path],
    temp_dir: Path,
) -> Path:
    rgb_vrt = temp_dir / "sentinel2_rgb.vrt"
    command = [
        "gdalbuildvrt",
        "-separate",
        str(rgb_vrt),
        str(band_vrts["red"]),
        str(band_vrts["green"]),
        str(band_vrts["blue"]),
    ]
    runner.run(command, description="combine Sentinel-2 bands into RGB VRT")
    return rgb_vrt


def _translate_sentinel2_rgb(
    runner: CommandRunner,
    rgb_vrt: Path,
    output_path: Path,
    *,
    bbox: Tuple[float, float, float, float],
    region_geometry: Optional["ogr.Geometry"] = None,
    scale_to_byte: bool,
) -> Path:
    translate_input = rgb_vrt
    if region_geometry is not None:
        cutline = output_path.with_suffix(".geojson")
        write_geometry_geojson(region_geometry, cutline)
        cropped = output_path.with_suffix(".cropped.tif")
        if cropped.exists():
            cropped.unlink()
        command = [
            "gdalwarp",
            "-cutline",
            str(cutline),
            # the GeoJSON cutline is EPSG:4326 by definition; -cutline_srs
            # is GDAL 3.9+ and Ubuntu 24.04 ships 3.8
            "-t_srs",
            "EPSG:4326",
            "-crop_to_cutline",
            "-overwrite",
            "-of",
            "GTiff",
            str(rgb_vrt),
            str(cropped),
        ]
        runner.run(command, description="crop Sentinel-2 mosaic to region")
        translate_input = cropped
    elif bbox:
        cropped = output_path.with_suffix(".cropped.tif")
        if cropped.exists():
            cropped.unlink()
        minx, miny, maxx, maxy = (str(value) for value in bbox)
        command = [
            "gdalwarp",
            "-te_srs",
            "EPSG:4326",
            "-te",
            minx,
            miny,
            maxx,
            maxy,
            "-overwrite",
            "-of",
            "GTiff",
            str(rgb_vrt),
            str(cropped),
        ]
        runner.run(command, description="crop Sentinel-2 mosaic to bbox")
        translate_input = cropped
    if output_path.exists():
        output_path.unlink()
    command = [
        "gdal_translate",
        "-of",
        "COG",
        "-co",
        # lossless so exact nodata=0 survives for transparent overlay
        # compositing; JPEG drops the nodata mask and adds block artefacts
        "COMPRESS=DEFLATE",
        "-co",
        "BLOCKSIZE=512",
        "-co",
        "NUM_THREADS=ALL_CPUS",
        "-co",
        "PHOTOMETRIC=RGB",
        # 0 is Sentinel-2 fill / outside the land cutline -> transparent
        "-a_nodata",
        "0",
    ]
    if scale_to_byte:
        command.extend(["-ot", "Byte", "-scale", "0", "10000", "0", "255"])
    command.extend([str(translate_input), str(output_path)])
    runner.run(command, description="convert Sentinel-2 mosaic to COG")
    return output_path

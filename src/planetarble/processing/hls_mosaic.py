"""HLS mosaic building: Fmask cloud masking, band lists, VRT and temporal median compositing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from planetarble.acquisition.mpc import append_sas_token, fetch_sas_token
from planetarble.core.models import HLSConfig

from .assets import _strip_query
from .geometry import write_geometry_geojson
from .runner import CommandRunner

if TYPE_CHECKING:  # GDAL is optional at runtime; imported lazily where used
    from osgeo import ogr


def _load_hls_scene_manifest(path: Path) -> List[Dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(scenes, list):
        return []
    return [scene for scene in scenes if isinstance(scene, dict)]


def _refresh_hls_scene_urls(
    scenes: Sequence[Dict[str, object]],
    config: HLSConfig,
) -> List[Dict[str, object]]:
    refreshed: List[Dict[str, object]] = []
    tokens: Dict[str, str] = {}
    for scene in scenes:
        collection = scene.get("collection_id")
        if not isinstance(collection, str) or not collection:
            continue
        token = tokens.get(collection)
        if token is None:
            token = fetch_sas_token(collection, timeout=config.request_timeout_seconds)
            tokens[collection] = token
        bands = scene.get("bands")
        if isinstance(bands, dict):
            updated_bands = {}
            for band, href in bands.items():
                if not isinstance(href, str) or not href:
                    continue
                updated_bands[band] = append_sas_token(_strip_query(href), token)
            scene["bands"] = updated_bands
        qa = scene.get("qa_asset")
        if isinstance(qa, str) and qa:
            scene["qa_asset"] = append_sas_token(_strip_query(qa), token)
        refreshed.append(scene)
    return refreshed


_QA_BIT = {
    "cirrus": 1,
    "cloud": 2,
    "adjacent_cloud": 4,
    "cloud_shadow": 8,
    "snow": 16,
    "snow_ice": 16,
    "water": 32,
}


def _qa_mask_value(flags: Sequence[str]) -> int:
    """OR together the HLS Fmask bits for the given flag names (unknown ignored)."""
    value = 0
    for flag in flags or ():
        value |= _QA_BIT.get(str(flag).strip().lower(), 0)
    return value


def _mask_band_command(
    band_path: Path,
    fmask_path: Path,
    out_path: Path,
    *,
    mask_value: int,
    gdal_calc: str = "gdal_calc.py",
) -> List[str]:
    """gdal_calc command keeping band values only where Fmask is clear.

    Pixels where ``(Fmask & mask_value) != 0`` (cloud / shadow / snow ...) become
    nodata (0), so a cleaner scene shows through when the masked bands are
    mosaicked.
    """
    calc = f"A*(numpy.bitwise_and(B.astype(numpy.uint16),{mask_value})==0)"
    return [
        gdal_calc,
        "-A", str(band_path), "--A_band=1",
        "-B", str(fmask_path), "--B_band=1",
        "--outfile", str(out_path),
        "--calc", calc,
        "--NoDataValue=0",
        "--type=UInt16",
        "--co=TILED=YES",
        "--co=COMPRESS=DEFLATE",
        "--quiet",
        "--overwrite",
    ]


def _dilate_fmask(
    qa_path: Path,
    out_path: Path,
    *,
    mask_value: int,
    iterations: int,
) -> Path:
    """Write a 0/1 cloud mask grown by ``iterations`` pixels from the Fmask.

    Buffers the cloud footprint so the semi-transparent cloud fringe Fmask
    labels "clear" is also dropped; the deep median stack fills the holes.
    """
    import numpy as np
    from osgeo import gdal

    from planetarble.processing.composite import dilate_boolean

    gdal.UseExceptions()
    src = gdal.Open(str(qa_path))
    qa = src.GetRasterBand(1).ReadAsArray()
    cloudy = np.bitwise_and(qa.astype(np.uint16), mask_value) != 0
    dilated = dilate_boolean(cloudy, iterations).astype(np.uint8)
    dst = gdal.GetDriverByName("GTiff").Create(
        str(out_path), src.RasterXSize, src.RasterYSize, 1, gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=DEFLATE"],
    )
    dst.SetGeoTransform(src.GetGeoTransform())
    dst.SetProjection(src.GetProjection())
    dst.GetRasterBand(1).WriteArray(dilated)
    dst.FlushCache()
    dst = None
    src = None
    return out_path


def _mask_hls_scene_bands(
    runner: CommandRunner,
    scenes: Sequence[Dict[str, object]],
    *,
    temp_dir: Path,
    mask_value: int,
    dilation: int = 0,
) -> List[Dict[str, object]]:
    """Replace each scene's local bands with Fmask-masked copies (clouds -> nodata)."""
    if mask_value <= 0:
        return list(scenes)
    mask_dir = temp_dir / "hls_masked"
    mask_dir.mkdir(parents=True, exist_ok=True)
    out: List[Dict[str, object]] = []
    for scene in scenes:
        bands = scene.get("bands")
        qa_local = scene.get("qa_local")
        if not isinstance(bands, dict) or not isinstance(qa_local, str) or not qa_local:
            out.append(scene)  # no QA available -> leave bands unmasked
            continue
        item_id = str(scene.get("item_id", "scene"))
        # buffer the cloud mask once per scene; mask bands against the 0/1 mask
        qa_for_mask = Path(qa_local)
        band_mask_value = mask_value
        if dilation > 0:
            dilated_path = mask_dir / f"{item_id}_clouddilate.tif"
            _dilate_fmask(Path(qa_local), dilated_path, mask_value=mask_value, iterations=dilation)
            qa_for_mask = dilated_path
            band_mask_value = 1
        masked: Dict[str, str] = {}
        for band, path in bands.items():
            if not isinstance(path, str) or not path:
                continue
            masked_path = mask_dir / f"{item_id}_{band}_masked.tif"
            runner.run(
                _mask_band_command(Path(path), qa_for_mask, masked_path, mask_value=band_mask_value),
                description="mask HLS band with Fmask",
            )
            masked[band] = str(masked_path)
        updated = dict(scene)
        updated["bands"] = masked
        out.append(updated)
    return out


def _scene_cloud_cover(scene: Dict[str, object]) -> float:
    """Cloud cover for ordering; unknown is treated as worst (most cloud)."""
    cloud = scene.get("cloud_cover")
    if isinstance(cloud, (int, float)):
        return float(cloud)
    return float("inf")


def _write_hls_band_lists(temp_dir: Path, scenes: Sequence[Dict[str, object]]) -> Dict[str, Path]:
    band_keys = {"B02": "blue", "B03": "green", "B04": "red"}
    lists: Dict[str, List[str]] = {label: [] for label in band_keys.values()}
    # gdalbuildvrt paints the last source on top (nodata lets lower ones show
    # through), so order cloudiest-first / cleanest-last: the lowest-cloud scene
    # wins over hazier scenes whose thin cloud/haze Fmask missed.
    scenes = sorted(scenes, key=_scene_cloud_cover, reverse=True)
    for scene in scenes:
        bands = scene.get("bands")
        if not isinstance(bands, dict):
            continue
        for band, label in band_keys.items():
            url = bands.get(band)
            if isinstance(url, str) and url:
                lists[label].append(url)
    paths: Dict[str, Path] = {}
    temp_dir.mkdir(parents=True, exist_ok=True)
    for label, urls in lists.items():
        if not urls:
            raise ValueError(f"No HLS sources found for {label} band")
        list_path = temp_dir / f"hls_{label}_sources.txt"
        list_path.write_text("\n".join(urls), encoding="utf-8")
        paths[label] = list_path
    return paths


def _build_hls_band_vrts(
    runner: CommandRunner,
    band_lists: Dict[str, Path],
    temp_dir: Path,
) -> Dict[str, Path]:
    vrts: Dict[str, Path] = {}
    for label, list_path in band_lists.items():
        vrt_path = temp_dir / f"hls_{label}_mosaic.vrt"
        command = [
            "gdalbuildvrt",
            "-allow_projection_difference",
            # treat 0 as nodata so Fmask-masked (cloud) pixels are skipped and a
            # cleaner scene shows through instead of covering it
            "-srcnodata",
            "0",
            "-vrtnodata",
            "0",
            "-input_file_list",
            str(list_path),
            str(vrt_path),
        ]
        runner.run(command, description=f"mosaic HLS {label} band")
        vrts[label] = vrt_path
    return vrts


def _build_hls_rgb_vrt(
    runner: CommandRunner,
    band_vrts: Dict[str, Path],
    temp_dir: Path,
) -> Path:
    rgb_vrt = temp_dir / "hls_rgb.vrt"
    command = [
        "gdalbuildvrt",
        "-separate",
        str(rgb_vrt),
        str(band_vrts["red"]),
        str(band_vrts["green"]),
        str(band_vrts["blue"]),
    ]
    runner.run(command, description="combine HLS bands into RGB VRT")
    return rgb_vrt


def _use_median_strategy(strategy: object) -> bool:
    """Whether the configured HLS mosaic strategy means temporal compositing."""
    return str(strategy).strip().lower() in {"best_pixel", "median"}


def _read_source_list(list_path: Path) -> List[str]:
    return [line for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_hls_median_rgb(
    band_lists: Dict[str, Path],
    temp_dir: Path,
    *,
    nodata: int = 0,
    block_rows: int = 512,
) -> Path:
    """Composite per-scene band rasters into one RGB GeoTIFF by temporal median.

    For each band the scenes are warped onto a single reference grid (each warp
    is independent, so scenes from different UTM zones co-register fine) and
    streamed through ``median_composite`` in row blocks to bound memory. The
    median rejects residual cloud/haze/shadow that Fmask missed and fills gaps
    where at least one scene saw the ground. Output band order is R, G, B to
    match the legacy RGB VRT.
    """
    import numpy as np
    from osgeo import gdal

    from planetarble.processing.composite import median_composite

    gdal.UseExceptions()
    order = ("red", "green", "blue")
    sources = {label: _read_source_list(band_lists[label]) for label in order}

    # reference grid from the red band (all bands share the scene footprints)
    ref = gdal.BuildVRT(
        "",
        sources["red"],
        options=gdal.BuildVRTOptions(
            srcNodata=nodata, VRTNodata=nodata, allowProjectionDifference=True
        ),
    )
    gt = ref.GetGeoTransform()
    proj = ref.GetProjection()
    xs, ys = ref.RasterXSize, ref.RasterYSize
    minx, maxy = gt[0], gt[3]
    maxx, miny = minx + gt[1] * xs, maxy + gt[5] * ys
    bounds = [minx, miny, maxx, maxy]
    ref = None

    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = temp_dir / "hls_median_rgb.tif"
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(
        str(out_path), xs, ys, 3, gdal.GDT_UInt16,
        options=["TILED=YES", "COMPRESS=DEFLATE"],
    )
    out.SetGeoTransform(gt)
    out.SetProjection(proj)

    warp_opts = gdal.WarpOptions(
        format="VRT", outputBounds=bounds, width=xs, height=ys,
        dstSRS=proj, srcNodata=nodata, dstNodata=nodata, resampleAlg="near",
    )
    for band_index, label in enumerate(order, start=1):
        aligned = [gdal.Warp("", src, options=warp_opts) for src in sources[label]]
        out_band = out.GetRasterBand(band_index)
        out_band.SetNoDataValue(nodata)
        for y in range(0, ys, block_rows):
            rows = min(block_rows, ys - y)
            stack = np.stack(
                [ds.GetRasterBand(1).ReadAsArray(0, y, xs, rows) for ds in aligned]
            )
            out_band.WriteArray(median_composite(stack, nodata=nodata), 0, y)
        del aligned  # release the warped GDAL datasets before the next band
    out.FlushCache()
    out = None
    return out_path


def _translate_hls_rgb(
    runner: CommandRunner,
    rgb_vrt: Path,
    output_path: Path,
    *,
    region_geometry: Optional["ogr.Geometry"] = None,
    scale_max: int = 3000,
    gamma: float = 0.8,
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
            "-cutline_srs",
            "EPSG:4326",
            "-t_srs",
            "EPSG:4326",
            "-crop_to_cutline",
            "-overwrite",
            "-of",
            "GTiff",
            str(rgb_vrt),
            str(cropped),
        ]
        runner.run(command, description="crop HLS mosaic to region")
        translate_input = cropped
    if output_path.exists():
        output_path.unlink()
    command = [
        "gdal_translate",
        "-of",
        "GTiff",
        "-co",
        "TILED=YES",
        "-co",
        "COMPRESS=DEFLATE",
        "-co",
        "PHOTOMETRIC=RGB",
        "-ot",
        "Byte",
        "-scale",
        "0",
        str(scale_max),
        "0",
        "255",
    ]
    if gamma and gamma != 1.0:
        command.extend(["-exponent", str(gamma)])
    command.extend([
        "-a_nodata",
        "0",
        str(translate_input),
        str(output_path),
    ])
    runner.run(command, description="convert HLS RGB mosaic to GeoTIFF")
    return output_path

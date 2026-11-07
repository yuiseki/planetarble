"""Clip remote COGs to an AOI with HTTP range reads (ADR 0003, step 2).

Overlay sources that publish Cloud Optimized GeoTIFFs only differ in how they
find the COG URLs for an AOI. This module turns a list of URLs into the GDAL
commands that read just the AOI window over ``/vsicurl/`` (no whole-file
download), reproject it to EPSG:3857, keep the RGB bands, optionally stretch
them to 8 bit, and add an alpha band so areas outside the imagery stay
transparent when the overlay is composited onto the planet.

Inputs may be in different CRSs (e.g. NAIP quarter quads in several UTM
zones); gdalwarp reprojects each one. ``-srcband`` needs GDAL 3.7 or newer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]

# Avoid listing remote "directories" on open and keep GDAL from probing
# sidecar files over HTTP for every input.
_VSICURL_CONFIG = [
    "--config", "GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR",
    "--config", "GDAL_HTTP_MULTIRANGE", "YES",
    "--config", "GDAL_HTTP_MAX_RETRY", "5",
    "--config", "GDAL_HTTP_RETRY_DELAY", "2",
]


@dataclass(frozen=True)
class Stretch:
    """Linear stretch of source values [low, high] to 0..255, then gamma."""

    low: float
    high: float
    gamma: float = 1.0


def vsicurl(url: str) -> str:
    """GDAL path for a remote file; local paths and /vsi* paths pass through."""
    if url.startswith(("http://", "https://")):
        return "/vsicurl/" + url
    return url


def cog_warp_commands(
    urls: Sequence[str],
    *,
    aoi_bbox: BBox,
    output_path: Path,
    bands: Sequence[int] = (1, 2, 3),
    stretch: Optional[Stretch] = None,
    src_nodata: Optional[float] = None,
    resampling: str = "cubic",
) -> List[List[str]]:
    """Commands that turn ``urls`` into an RGBA GeoTIFF of the AOI in EPSG:3857.

    With ``stretch`` the warp writes an intermediate ``<output>.warp.tif`` that
    gdal_translate scales to Byte; otherwise the warp writes ``output_path``
    directly (inputs are expected to be 8 bit already).
    """
    if not urls:
        raise ValueError("no COG URLs to warp")
    minx, miny, maxx, maxy = aoi_bbox
    if minx >= maxx or miny >= maxy:
        raise ValueError(f"empty AOI bbox: {aoi_bbox}")

    warp_out = output_path.with_suffix(".warp.tif") if stretch else output_path
    warp: List[str] = ["gdalwarp", *_VSICURL_CONFIG, "-overwrite",
                       "-t_srs", "EPSG:3857",
                       "-te_srs", "EPSG:4326", "-te", str(minx), str(miny), str(maxx), str(maxy),
                       "-r", resampling, "-multi", "-wo", "NUM_THREADS=ALL_CPUS"]
    for band in bands:
        warp += ["-srcband", str(band)]
    if src_nodata is not None:
        warp += ["-srcnodata", _fmt(src_nodata)]
    warp += ["-dstalpha", "-of", "GTiff", "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", "-co", "BIGTIFF=IF_SAFER"]
    warp += [vsicurl(u) for u in urls]
    warp.append(str(warp_out))
    commands = [warp]

    if stretch is not None:
        translate: List[str] = ["gdal_translate", "-ot", "Byte"]
        for index in range(1, len(bands) + 1):
            translate += [f"-scale_{index}", _fmt(stretch.low), _fmt(stretch.high), "0", "255"]
            if stretch.gamma != 1.0:
                translate += [f"-exponent_{index}", _fmt(stretch.gamma)]
        alpha = len(bands) + 1
        # the alpha band of a 16-bit warp is 0 / 65535; keep it 0 / 255
        translate += [f"-scale_{alpha}", "0", "65535", "0", "255"]
        translate += ["-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", str(warp_out), str(output_path)]
        commands.append(translate)
    return commands


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)

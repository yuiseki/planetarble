"""Concrete source adapters (ADR 0001, step 2b).

Each adapter binds a ``source:`` name to its behaviour behind the
``SourceAdapter`` protocol. Step 2b implements the resolution contract
(``name`` and ``native_max_zoom``) and a factory; the execution wiring
(``plan`` / ``build_raster``, which delegate to the existing per-source
planners and managers) lands with the orchestrator in step 3, so those methods
declare the contract and raise ``NotImplementedError`` for now.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from .sources import SOURCE_REGISTRY


class BaseSourceAdapter:
    """Default behaviour shared by all adapters."""

    name: str = ""

    def native_max_zoom(self, aoi: object) -> int:
        return SOURCE_REGISTRY[self.name].native_max_zoom

    def plan(self, aoi: object, zoom_range: Tuple[int, int]) -> object:
        raise NotImplementedError(
            f"{self.name}.plan is wired with the orchestrator (ADR 0001 step 3)"
        )

    def build_raster(self, plan: object, workspace: object) -> object:
        raise NotImplementedError(
            f"{self.name}.build_raster is wired with the orchestrator (ADR 0001 step 3)"
        )


class BMNGAdapter(BaseSourceAdapter):
    name = "bmng"

    def __init__(self, resolution: str = "500m") -> None:
        self.resolution = resolution

    def native_max_zoom(self, aoi: object) -> int:
        # The 2km single frame tops out lower than the 500m panels.
        return 6 if str(self.resolution).strip().lower().startswith("2") else 8


class HLSAdapter(BaseSourceAdapter):
    name = "hls"


class Sentinel2Adapter(BaseSourceAdapter):
    name = "sentinel2"


class CopernicusAdapter(BaseSourceAdapter):
    name = "copernicus"


class GSIAdapter(BaseSourceAdapter):
    name = "gsi_orthophotos"


class ModisAdapter(BaseSourceAdapter):
    name = "modis"


class ViirsAdapter(BaseSourceAdapter):
    name = "viirs"


class OpenAerialMapAdapter(BaseSourceAdapter):
    name = "openaerialmap"

    def __init__(
        self,
        item_max_zoom: Optional[int] = None,
        *,
        max_gsd: Optional[float] = None,
        max_items: int = 1,
        resampling: str = "cubic",
        fetch: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        # OAM resolution varies per item, so the real ceiling comes from the
        # selected item's ground sample distance once known; until then the
        # registry value is only an upper guard.
        self.item_max_zoom = item_max_zoom
        self._max_gsd = max_gsd
        self._max_items = max_items
        self._resampling = resampling
        self._fetch = fetch  # injectable callable(bbox) -> List[OAMItem] for tests
        self._selected: list = []

    def native_max_zoom(self, aoi: object) -> int:
        if self.item_max_zoom is not None:
            return self.item_max_zoom
        if self._selected:
            from planetarble.acquisition.openaerialmap import gsd_to_zoom

            return gsd_to_zoom(min(item.gsd for item in self._selected))
        return SOURCE_REGISTRY[self.name].native_max_zoom

    def plan(self, aoi: Any, zoom_range: Tuple[int, int]) -> object:
        from planetarble.acquisition.openaerialmap import query_oam, select_items

        bbox = getattr(aoi, "bbox", None) or aoi
        items = self._fetch(bbox) if self._fetch is not None else query_oam(bbox)
        selected = select_items(items, max_items=self._max_items, max_gsd=self._max_gsd)
        if not selected:
            raise ValueError(f"no OpenAerialMap imagery found for bbox {bbox}")
        self._selected = selected
        return selected

    def build_raster(
        self,
        plan: Any,
        workspace: object,
        *,
        cache_dir: Any = None,
        aoi_bbox: Any = None,
    ) -> object:
        import subprocess
        from pathlib import Path

        from planetarble.acquisition.openaerialmap import (
            build_local_warp_command,
            oam_cache_path,
            oam_download_command,
        )

        items = plan if plan is not None else self._selected
        if not items:
            raise ValueError("no OAM items to build")
        cache = Path(cache_dir) if cache_dir is not None else Path("data/cache/oam")
        cache.mkdir(parents=True, exist_ok=True)
        bbox = tuple(aoi_bbox) if aoi_bbox is not None else items[0].bbox

        # 1. download each COG whole into the cache (skip if already present)
        for item in items:
            dest = oam_cache_path(item, cache)
            if dest.exists() and dest.stat().st_size > 0:
                continue
            subprocess.run(oam_download_command(item, dest), check=True)

        # 2. warp the LOCAL cached COGs into the AOI COG (no network)
        output_path = str(workspace)
        command = build_local_warp_command(
            items,
            cache_dir=cache,
            aoi_bbox=bbox,
            output_path=output_path,
            resampling=self._resampling,
        )
        subprocess.run(command, check=True)
        return output_path


class ESAWorldCoverAdapter(BaseSourceAdapter):
    """ESA WorldCover Sentinel-2 RGBNIR annual composites (ADR 0003 step 3).

    1 x 1 degree COG tiles on a public S3 bucket, named by their south-west
    corner, UInt16 reflectance x 10000 with 0 as nodata. Tiles over open sea
    do not exist, so planning keeps only the tiles that answer.
    """

    name = "esa-worldcover"
    _BASE = "https://esa-worldcover-s2.s3.amazonaws.com/rgbnir"
    _VERSIONS = {2020: "v100", 2021: "v200"}

    def __init__(
        self,
        year: int = 2021,
        *,
        stretch_max: float = 2000.0,
        gamma: float = 0.7,
        exists: Optional[Callable[[str], bool]] = None,
        run: Optional[Callable[[List[str]], Any]] = None,
    ) -> None:
        year = int(year)
        if year not in self._VERSIONS:
            raise ValueError(f"esa-worldcover year must be 2020 or 2021, got {year}")
        self._year = year
        self._stretch_max = float(stretch_max)
        self._gamma = float(gamma)
        self._exists = exists or _http_exists
        self._run = run or _run_checked

    def tile_urls(self, bbox: Tuple[float, float, float, float]) -> List[str]:
        import math

        min_lon, min_lat, max_lon, max_lat = bbox
        eps = 1e-9
        urls = []
        for lat in range(math.floor(min_lat), math.floor(max_lat - eps) + 1):
            for lon in range(math.floor(min_lon), math.floor(max_lon - eps) + 1):
                lat_part = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
                tile = f"{lat_part}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
                version = self._VERSIONS[self._year]
                urls.append(
                    f"{self._BASE}/{self._year}/{lat_part}/"
                    f"ESA_WorldCover_10m_{self._year}_{version}_{tile}_S2RGBNIR.tif"
                )
        return urls

    def plan(self, aoi: Any, zoom_range: Tuple[int, int]) -> List[str]:
        bbox = tuple(getattr(aoi, "bbox", None) or aoi)
        urls = [u for u in self.tile_urls(bbox) if self._exists(u)]
        if not urls:
            raise ValueError(f"no ESA WorldCover tile covers bbox {bbox} (open sea?)")
        return urls

    def build_raster(self, plan: Any, workspace: object, *, aoi_bbox: Any = None) -> Path:
        from pathlib import Path

        from .cogwarp import Stretch, cog_warp_commands

        output = Path(str(workspace))
        stretch = Stretch(0, self._stretch_max, self._gamma)
        for command in cog_warp_commands(
            list(plan), aoi_bbox=tuple(aoi_bbox), output_path=output, stretch=stretch, src_nodata=0
        ):
            self._run(command)
        return output


def _http_exists(url: str) -> bool:
    import requests

    response = requests.head(url, timeout=30, allow_redirects=True)
    if response.status_code in (403, 404):
        return False
    response.raise_for_status()
    return True


def _run_checked(command: List[str]) -> None:
    import subprocess

    subprocess.run(command, check=True)


_ADAPTERS: Dict[str, Type[BaseSourceAdapter]] = {
    cls.name: cls
    for cls in (
        BMNGAdapter,
        HLSAdapter,
        Sentinel2Adapter,
        CopernicusAdapter,
        GSIAdapter,
        ModisAdapter,
        ViirsAdapter,
        OpenAerialMapAdapter,
        ESAWorldCoverAdapter,
    )
}


def adapter_sources() -> List[str]:
    return sorted(_ADAPTERS)


def get_adapter(source: str, **kwargs: object) -> BaseSourceAdapter:
    cls = _ADAPTERS.get(source)
    if cls is None:
        raise ValueError(
            f"no adapter for source {source!r} (known: {sorted(_ADAPTERS)})"
        )
    return cls(**kwargs)

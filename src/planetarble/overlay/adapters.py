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


class NAIPAdapter(BaseSourceAdapter):
    """USDA NAIP aerial imagery from Microsoft Planetary Computer (ADR 0003 step 4).

    Contiguous US, 0.3 to 1 m, one COG per quarter quad and acquisition. The
    STAC search returns every year that covers the AOI; for each quarter quad
    only the newest acquisition is kept (or those of ``year`` if given), and
    the hrefs are signed with an anonymous SAS token.
    """

    name = "naip"
    _STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"

    def __init__(
        self,
        year: Optional[int] = None,
        *,
        search: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None,
        sas_token: Optional[Callable[[], str]] = None,
        run: Optional[Callable[[List[str]], Any]] = None,
    ) -> None:
        self._year = int(year) if year is not None else None
        self._search = search or self._stac_search
        self._sas_token = sas_token or (lambda: _mpc_sas_token("naip"))
        self._run = run or _run_checked

    def plan(self, aoi: Any, zoom_range: Tuple[int, int]) -> List[str]:
        from planetarble.acquisition.mpc import append_sas_token

        bbox = [float(v) for v in (getattr(aoi, "bbox", None) or aoi)]
        body: Dict[str, Any] = {"collections": ["naip"], "bbox": bbox, "limit": 200}
        if self._year is not None:
            body["datetime"] = f"{self._year}-01-01T00:00:00Z/{self._year}-12-31T23:59:59Z"
        newest: Dict[str, Dict[str, Any]] = {}
        for item in self._search(body):
            key = _naip_quarter_quad(item["id"])
            if key not in newest or item["properties"]["datetime"] > newest[key]["properties"]["datetime"]:
                newest[key] = item
        if not newest:
            raise ValueError(f"no NAIP imagery covers bbox {tuple(bbox)} (contiguous US only)")
        token = self._sas_token()
        return [append_sas_token(newest[k]["assets"]["image"]["href"], token) for k in sorted(newest)]

    def build_raster(self, plan: Any, workspace: object, *, aoi_bbox: Any = None) -> Path:
        from .cogwarp import cog_warp_commands

        output = Path(str(workspace))
        for command in cog_warp_commands(list(plan), aoi_bbox=tuple(aoi_bbox), output_path=output):
            self._run(command)
        return output

    def _stac_search(self, body: Dict[str, Any]) -> List[Dict[str, Any]]:
        import requests

        items: List[Dict[str, Any]] = []
        url: Optional[str] = self._STAC
        payload: Optional[Dict[str, Any]] = body
        while url:
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            page = response.json()
            items.extend(page.get("features", []))
            nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
            url = nxt.get("href") if nxt else None
            payload = nxt.get("body", payload) if nxt else None
        return items


def _naip_quarter_quad(item_id: str) -> str:
    """'ca_m_3712213_se_10_060_20220518' -> '3712213_se' (stable across years)."""
    parts = item_id.split("_")
    return "_".join(parts[2:4]) if len(parts) >= 4 else item_id


def _mpc_sas_token(collection: str) -> str:
    from planetarble.acquisition.mpc import fetch_sas_token

    return fetch_sas_token(collection, timeout=60)


class NZImageryAdapter(BaseSourceAdapter):
    """New Zealand aerial and satellite imagery from LINZ (ADR 0003 step 5).

    Toitū Te Whenua LINZ publishes every survey as a collection in a static
    STAC catalog on a public S3 bucket, with one COG item per map sheet.
    Planning reads the root, the collections and the chosen collection's items
    (each cached under ``cache_dir`` by its checksum in the parent link, so a
    re-plan only fetches what changed), picks a current
    RGB survey that intersects the AOI: the finest among surveys flown within
    ``max_age_years`` of the newest one (or the ``collection`` slug given), and
    returns the COGs of the sheets that intersect the AOI. The credit names
    the survey and its licensor, as the CC BY 4.0 licence requires.
    """

    name = "nz-imagery"
    _ROOT = "https://nz-imagery.s3.ap-southeast-2.amazonaws.com/catalog.json"
    _CURRENT = {"urban-aerial-photos", "rural-aerial-photos", "satellite-imagery"}
    _LICENCES = {"CC-BY-4.0"}

    def __init__(
        self,
        collection: Optional[str] = None,
        *,
        max_age_years: int = 5,
        cache_dir: Any = "data/cache/nz-imagery",
        fetch_json: Optional[Callable[[str], Dict[str, Any]]] = None,
        run: Optional[Callable[[List[str]], Any]] = None,
        concurrency: int = 16,
    ) -> None:
        self._collection = collection
        self._max_age_years = int(max_age_years)
        self._cache = Path(str(cache_dir))
        self._fetch_json = fetch_json or _fetch_json
        self._run = run or _run_checked
        self._concurrency = max(1, int(concurrency))
        self.credit: Optional[str] = None

    def plan(self, aoi: Any, zoom_range: Tuple[int, int]) -> List[str]:
        from urllib.parse import urljoin

        bbox = tuple(float(v) for v in (getattr(aoi, "bbox", None) or aoi))
        root = self._fetch_json(self._ROOT)
        links = [link for link in root.get("links", []) if link.get("rel") == "child" and "/rgb/" in link["href"]]
        if self._collection:
            links = [link for link in links if f"/{self._collection}/" in link["href"]]
            if not links:
                raise ValueError(f"no New Zealand imagery collection named {self._collection}")
        urls = [urljoin(self._ROOT, link["href"]) for link in links]
        collections = self._fetch_all(urls, [link.get("file:checksum") for link in links])

        candidates = []
        for url, col in zip(urls, collections, strict=True):
            if not _bbox_intersects(col["extent"]["spatial"]["bbox"][0], bbox):
                continue
            if not self._collection and (
                col.get("linz:geospatial_category") not in self._CURRENT or col.get("linz:lifecycle") != "completed"
            ):
                continue
            candidates.append((url, col))
        if not candidates:
            raise ValueError(f"no New Zealand imagery collection covers bbox {bbox}")
        # Finest survey among the recent ones: a city's 7.5 cm urban survey
        # beats a 0.2 m regional survey flown a year later, but not one from
        # a decade ago.
        newest = max(_nz_year(c[1]) for c in candidates)
        recent = [c for c in candidates if _nz_year(c[1]) >= newest - self._max_age_years]
        url, col = min(recent, key=lambda c: (_nz_gsd(c[1]), -_nz_year(c[1])))
        if col.get("license") not in self._LICENCES:
            raise ValueError(f"{col.get('linz:slug')}: licence {col.get('license')} is not CC BY 4.0")

        item_links = [link for link in col.get("links", []) if link.get("rel") == "item"]
        item_urls = [urljoin(url, link["href"]) for link in item_links]
        items = self._fetch_all(item_urls, [link.get("file:checksum") for link in item_links])
        hrefs = [
            urljoin(item_url, item["assets"]["visual"]["href"])
            for item_url, item in zip(item_urls, items, strict=True)
            if _bbox_intersects(item["bbox"], bbox)
        ]
        if not hrefs:
            raise ValueError(f"{col.get('linz:slug')} has no imagery inside bbox {bbox}")
        licensors = ", ".join(
            p["name"] for p in col.get("providers", []) if "licensor" in (p.get("roles") or [])
        )
        self.credit = (
            f"Imagery: {col['title']}, {licensors}, via Toitū Te Whenua Land Information New Zealand (CC BY 4.0)"
            if licensors
            else f"Imagery: {col['title']}, Toitū Te Whenua Land Information New Zealand (CC BY 4.0)"
        )
        return sorted(hrefs)

    def build_raster(self, plan: Any, workspace: object, *, aoi_bbox: Any = None) -> Path:
        from .cogwarp import cog_warp_commands

        output = Path(str(workspace))
        for command in cog_warp_commands(list(plan), aoi_bbox=tuple(aoi_bbox), output_path=output):
            self._run(command)
        return output

    def _fetch_all(self, urls: List[str], checksums: List[Optional[str]]) -> List[Dict[str, Any]]:
        import json
        from concurrent.futures import ThreadPoolExecutor

        def one(pair: Tuple[str, Optional[str]]) -> Dict[str, Any]:
            url, checksum = pair
            cached = self._cache / f"{checksum}.json" if checksum else None
            if cached is not None and cached.exists():
                return json.loads(cached.read_text(encoding="utf-8"))
            doc = self._fetch_json(url)
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(doc), encoding="utf-8")
            return doc

        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            return list(pool.map(one, zip(urls, checksums, strict=True)))


def _bbox_intersects(a: Any, b: Any) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _nz_year(collection: Dict[str, Any]) -> int:
    end = collection["extent"]["temporal"]["interval"][0][-1] or collection["extent"]["temporal"]["interval"][0][0]
    return int(str(end)[:4])


def _nz_gsd(collection: Dict[str, Any]) -> float:
    import re

    match = re.search(r"_(\d+(?:\.\d+)?)m$", str(collection.get("linz:slug", "")))
    return float(match.group(1)) if match else float("inf")


def _fetch_json(url: str) -> Dict[str, Any]:
    import requests

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    result: Dict[str, Any] = response.json()
    return result


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
        NAIPAdapter,
        NZImageryAdapter,
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

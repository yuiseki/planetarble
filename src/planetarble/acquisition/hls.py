"""Acquisition helpers for Harmonized Landsat and Sentinel-2 (HLS) data."""

from __future__ import annotations

import json
import math
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from pystac import Item

try:  # pragma: no cover - optional dependency availability
    from pystac_client import Client
    from pystac_client import exceptions as pc_exceptions
    _PYSTAC_IMPORT_ERROR: Exception | None = None
    _PYSTAC_ERRORS: Tuple[type[Exception], ...] = tuple(
        err for err in (
            getattr(pc_exceptions, "APIError", None),
            getattr(pc_exceptions, "STACError", None),
        )
        if err is not None
    ) or (Exception,)
except Exception as exc:  # pragma: no cover - executed when sqlite is missing
    Client = None  # type: ignore[assignment]
    pc_exceptions = None  # type: ignore[assignment]
    _PYSTAC_IMPORT_ERROR = exc
    _PYSTAC_ERRORS = (Exception,)

from planetarble.core.models import HLSConfig, HLSSeasonWindow
from planetarble.logging import get_logger

from .mpc import MPCError, STAC_API_ROOT, append_sas_token, fetch_sas_token

LOGGER = get_logger(__name__)


WEBMERCATOR_MIN_LAT = -85.0511287798066
WEBMERCATOR_MAX_LAT = 85.0511287798066

LAND_APPROX_BBOXES: Tuple[Tuple[float, float, float, float], ...] = (
    (-170.0, -60.0, -30.0, 72.0),   # Americas
    (-30.0, -40.0, 60.0, 75.0),     # Europe + Africa
    (60.0, -10.0, 150.0, 80.0),     # Asia
    (110.0, -60.0, 180.0, -10.0),   # Australia
    (-45.0, -55.0, -10.0, -30.0),   # Southern South America
    (20.0, -75.0, 160.0, -60.0),    # Antarctica
    (-90.0, 10.0, -60.0, 30.0),     # Caribbean / Central America
    (-170.0, -25.0, -140.0, 25.0),  # Central Pacific archipelagos
    (150.0, -50.0, 180.0, -30.0),   # New Zealand
)


@dataclass
class HLSScene:
    """Describe a single HLS scene with signed asset references."""

    collection_id: str
    item_id: str
    acquisition_date: date
    cloud_cover: Optional[float]
    bbox: Tuple[float, float, float, float]
    bands: Dict[str, str]
    qa_asset: Optional[str]


@dataclass
class HLSMosaicTask:
    """Plan entry describing how to build a single Z/X/Y composite tile."""

    z: int
    x: int
    y: int
    bbox: Tuple[float, float, float, float]
    start_date: date
    end_date: date
    season_name: str
    hemisphere: str
    collections: Tuple[str, ...]
    fallback_collections: Tuple[str, ...]
    max_cloud: float
    fallback_max_cloud: float

    def to_mapping(self) -> Dict[str, object]:
        return {
            "z": self.z,
            "x": self.x,
            "y": self.y,
            "bbox": list(self.bbox),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "season": self.season_name,
            "hemisphere": self.hemisphere,
            "collections": list(self.collections),
            "fallback_collections": list(self.fallback_collections),
            "max_cloud": self.max_cloud,
            "fallback_max_cloud": self.fallback_max_cloud,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "HLSMosaicTask":
        try:
            z = int(mapping["z"])
            x = int(mapping["x"])
            y = int(mapping["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("plan entry missing tile coordinates") from exc
        bbox_values = mapping.get("bbox")
        if not isinstance(bbox_values, (list, tuple)) or len(bbox_values) != 4:
            raise ValueError("plan entry missing bbox")
        bbox = tuple(float(value) for value in bbox_values)
        try:
            start_date = date.fromisoformat(str(mapping["start_date"]))
            end_date = date.fromisoformat(str(mapping["end_date"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("plan entry missing start/end dates") from exc
        season_name = str(mapping.get("season", "unknown"))
        hemisphere = str(mapping.get("hemisphere", "global"))
        collections = tuple(str(value) for value in mapping.get("collections", []) or [])
        fallback = tuple(str(value) for value in mapping.get("fallback_collections", []) or [])
        max_cloud = float(mapping.get("max_cloud", 100.0))
        fallback_max_cloud = float(mapping.get("fallback_max_cloud", max_cloud))
        return cls(
            z=z,
            x=x,
            y=y,
            bbox=bbox,  # type: ignore[arg-type]
            start_date=start_date,
            end_date=end_date,
            season_name=season_name,
            hemisphere=hemisphere,
            collections=collections,
            fallback_collections=fallback,
            max_cloud=max_cloud,
            fallback_max_cloud=fallback_max_cloud,
        )


@dataclass
class HLSPlanSummary:
    """Summary statistics for an HLS mosaic plan."""

    path: Path
    zoom: int
    tile_count: int
    season_counts: Dict[str, int]


class HLSMosaicPlanner:
    """Generate per-tile plan entries for a global HLS composite."""

    def __init__(self, config: HLSConfig) -> None:
        self._config = config
        self._zoom = config.target_zoom
        if self._zoom < 0:
            raise ValueError("target_zoom must be non-negative")
        self._seasons = _index_seasons(config.seasonal_windows)
        self._land_buffer_deg = max(0.0, config.land_buffer_km) / 111.32
        self._target_year = config.compositing_year or datetime.now(timezone.utc).year

    def iter_tasks(self) -> Iterator[HLSMosaicTask]:
        n = 1 << self._zoom
        for x in range(n):
            for y in range(n):
                bbox = _tile_bounds(self._zoom, x, y)
                if not _bbox_intersects_land(bbox, self._land_buffer_deg):
                    continue
                center_lat, _ = _bbox_center(bbox)
                season = _select_season(center_lat, self._seasons)
                start_date, end_date = _season_dates(season, self._target_year)
                yield HLSMosaicTask(
                    z=self._zoom,
                    x=x,
                    y=y,
                    bbox=bbox,
                    start_date=start_date,
                    end_date=end_date,
                    season_name=season.name,
                    hemisphere=season.hemisphere,
                    collections=self._config.collections,
                    fallback_collections=self._config.fallback_collections,
                    max_cloud=self._config.max_cloud,
                    fallback_max_cloud=self._config.fallback_max_cloud,
                )

    def write_plan(self, destination: Path) -> HLSPlanSummary:
        destination.parent.mkdir(parents=True, exist_ok=True)
        counter: Counter[str] = Counter()
        total = 0
        with destination.open("w", encoding="utf-8") as handle:
            for task in self.iter_tasks():
                handle.write(json.dumps(task.to_mapping(), sort_keys=True))
                handle.write("\n")
                counter[task.season_name] += 1
                total += 1
        LOGGER.info(
            "generated hls plan",
            extra={
                "path": str(destination),
                "tiles": total,
                "zoom": self._zoom,
            },
        )
        return HLSPlanSummary(path=destination, zoom=self._zoom, tile_count=total, season_counts=dict(counter))


class HLSSTACClient:
    """Perform STAC searches for HLS scenes and sign the required assets."""

    def __init__(
        self,
        config: HLSConfig,
        *,
        timeout: int | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        cache_dir: Optional[Path] = None,
        cache_ttl_days: int = 7,
    ) -> None:
        if Client is None or pc_exceptions is None:
            raise MPCError(
                "pystac-client (and its sqlite dependency) must be available to search Microsoft Planetary Computer"
            ) from _PYSTAC_IMPORT_ERROR
        self._config = config
        self._timeout = timeout or config.request_timeout_seconds
        self._max_retries = max_retries or config.max_retries
        self._backoff = backoff_factor or config.backoff_factor
        self._cache_dir = cache_dir
        self._cache_ttl = timedelta(days=cache_ttl_days)
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._client = Client.open(config.stac_api or STAC_API_ROOT, timeout=self._timeout)
        except (_PYSTAC_ERRORS + (ConnectionError,)) as exc:  # pragma: no cover - network failure path
            raise MPCError(f"Failed to open MPC STAC client: {exc}") from exc
        self._tokens: Dict[str, str] = {}

    def fetch_scenes(
        self,
        task: HLSMosaicTask,
        *,
        max_items: int = 200,
        include_fallback: bool = True,
    ) -> Dict[str, List[HLSScene]]:
        collections: List[str] = list(task.collections)
        results: Dict[str, List[HLSScene]] = {}
        results["primary"] = self._search_collections(
            collections,
            task,
            max_items=max_items,
            max_cloud=task.max_cloud,
        )
        if include_fallback and (not results["primary"] or task.fallback_collections):
            fallback = list(task.fallback_collections)
            if fallback:
                results["fallback"] = self._search_collections(
                    fallback,
                    task,
                    max_items=max_items,
                    max_cloud=task.fallback_max_cloud,
                )
            else:
                results["fallback"] = []
        return results

    def _search_collections(
        self,
        collections: Sequence[str],
        task: HLSMosaicTask,
        *,
        max_items: int,
        max_cloud: float,
    ) -> List[HLSScene]:
        scenes: List[HLSScene] = []
        datetime_filter = f"{task.start_date.isoformat()}/{task.end_date.isoformat()}"
        query = {"eo:cloud_cover": {"lte": max_cloud}}
        cache_key = self._build_cache_key(collections, task, max_cloud, max_items)
        items: List[Item]

        cached_items = self._load_cache_items(cache_key)
        if cached_items is not None:
            LOGGER.info(
                "stac search cache hit",
                extra={
                    "collections": list(collections),
                    "bbox": list(task.bbox),
                    "season": task.season_name,
                    "hemisphere": task.hemisphere,
                },
            )
            items = cached_items
        else:
            items = []
            for collection in collections:
                try:
                    LOGGER.debug(
                        "stac search request",
                        extra={
                            "collection": collection,
                            "bbox": list(task.bbox),
                            "season": task.season_name,
                            "hemisphere": task.hemisphere,
                            "max_cloud": max_cloud,
                        },
                    )
                    search = self._client.search(
                        collections=[collection],
                        bbox=list(task.bbox),
                        datetime=datetime_filter,
                        limit=max_items,
                        query=query,
                    )
                    items.extend(list(search.items()))
                except _PYSTAC_ERRORS as exc:  # pragma: no cover - network failure path
                    message = str(exc)
                    log_msg = "stac search failed"
                    if "502" in message or "Bad Gateway" in message:
                        log_msg = "stac search 502 bad gateway"
                    LOGGER.warning(
                        log_msg,
                        extra={
                            "collection": collection,
                            "bbox": list(task.bbox),
                            "season": task.season_name,
                            "hemisphere": task.hemisphere,
                            "error": message,
                        },
                    )
                    continue
            if self._cache_dir is not None:
                self._store_cache_items(cache_key, items)

        for item in items:
            collection = item.collection_id or (collections[0] if collections else "")
            if not collection:
                continue
            token = self._tokens.get(collection)
            if not token:
                token = fetch_sas_token(collection, timeout=self._timeout)
                self._tokens[collection] = token
            scene = _build_scene(
                item=item,
                collection=collection,
                token=token,
                bands=self._config.spectral_bands,
                qa_asset_key=self._config.qa_asset_key,
                task_bbox=task.bbox,
            )
            if scene:
                scenes.append(scene)
        scenes.sort(key=lambda scene: (scene.cloud_cover if scene.cloud_cover is not None else 100.0, scene.acquisition_date))
        return scenes

    def _build_cache_key(
        self,
        collections: Sequence[str],
        task: HLSMosaicTask,
        max_cloud: float,
        max_items: int,
    ) -> str:
        payload = {
            "collections": list(collections),
            "bbox": list(task.bbox),
            "start": task.start_date.isoformat(),
            "end": task.end_date.isoformat(),
            "max_cloud": max_cloud,
            "max_items": max_items,
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_cache_items(self, key: str) -> Optional[List[Item]]:
        if self._cache_dir is None:
            return None
        path = self._cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.debug("stac cache decode failed", extra={"path": str(path)})
            return None
        generated_at = payload.get("generated_at")
        if not generated_at:
            return None
        try:
            timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            LOGGER.debug("stac cache timestamp invalid", extra={"path": str(path)})
            return None
        if datetime.now(timezone.utc) - timestamp > self._cache_ttl:
            return None
        items_data = payload.get("items") or []
        if not isinstance(items_data, list):
            return None
        items: List[Item] = []
        for entry in items_data:
            if not isinstance(entry, dict):
                continue
            items.append(Item.from_dict(entry, preserve_dict=True))
        return items

    def _store_cache_items(self, key: str, items: Sequence[Item]) -> None:
        if self._cache_dir is None:
            return
        path = self._cache_dir / f"{key}.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "items": [item.to_dict(include_self_link=False) for item in items],
        }
        try:
            path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            LOGGER.debug("stac cache store failed", extra={"path": str(path), "error": str(exc)})


def _index_seasons(seasons: Iterable[HLSSeasonWindow]) -> Dict[str, List[HLSSeasonWindow]]:
    indexed: Dict[str, List[HLSSeasonWindow]] = {"north": [], "south": [], "global": []}
    for season in seasons:
        hemisphere = (season.hemisphere or "global").lower()
        indexed.setdefault(hemisphere, []).append(season)
    return indexed


def _select_season(latitude: float, indexed: Dict[str, List[HLSSeasonWindow]]) -> HLSSeasonWindow:
    hemisphere = "north" if latitude >= 0 else "south"
    candidates = indexed.get(hemisphere) or indexed.get("global")
    if not candidates:
        raise ValueError(f"No seasonal window defined for hemisphere {hemisphere}")
    return candidates[0]


def _season_dates(season: HLSSeasonWindow, target_year: int) -> Tuple[date, date]:
    start_tuple = (season.start_month, season.start_day)
    end_tuple = (season.end_month, season.end_day)
    if start_tuple <= end_tuple:
        start_year = target_year
        end_year = target_year
    else:
        start_year = target_year - 1
        end_year = target_year
    start_date = date(start_year, season.start_month, season.start_day)
    end_date = date(end_year, season.end_month, season.end_day)
    return start_date, end_date


def _tile_bounds(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    n = 1 << z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_min = _tile_latitude(y + 1, n)
    lat_max = _tile_latitude(y, n)
    lat_min = max(lat_min, WEBMERCATOR_MIN_LAT)
    lat_max = min(lat_max, WEBMERCATOR_MAX_LAT)
    return (lon_min, lat_min, lon_max, lat_max)


def _tile_latitude(y: int, n: int) -> float:
    merc_y = math.pi * (1 - 2 * y / n)
    lat_rad = math.atan(math.sinh(merc_y))
    return math.degrees(lat_rad)


def _bbox_center(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    return (0.5 * (min_lat + max_lat), 0.5 * (min_lon + max_lon))


def _bbox_intersects_land(bbox: Tuple[float, float, float, float], buffer_deg: float) -> bool:
    for land_bbox in LAND_APPROX_BBOXES:
        if _boxes_intersect(bbox, land_bbox, buffer_deg):
            return True
    min_lon, min_lat, max_lon, max_lat = bbox
    if max_lat > 75.0 or min_lat < -75.0:
        return True
    return False


def _boxes_intersect(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
    buffer_deg: float,
) -> bool:
    a_min_lon, a_min_lat, a_max_lon, a_max_lat = a
    b_min_lon, b_min_lat, b_max_lon, b_max_lat = b
    if a_max_lon < b_min_lon - buffer_deg:
        return False
    if a_min_lon > b_max_lon + buffer_deg:
        return False
    if a_max_lat < b_min_lat - buffer_deg:
        return False
    if a_min_lat > b_max_lat + buffer_deg:
        return False
    return True


def _build_scene(
    *,
    item: Item,
    collection: str,
    token: str,
    bands: Sequence[str],
    qa_asset_key: str,
    task_bbox: Tuple[float, float, float, float],
) -> Optional[HLSScene]:
    assets = item.assets or {}
    signed: Dict[str, str] = {}
    for band in bands:
        asset = assets.get(band)
        if not asset or not asset.href:
            continue
        signed[band] = append_sas_token(asset.href, token)
    if not signed:
        return None
    qa_asset: Optional[str] = None
    if qa_asset_key:
        qa = assets.get(qa_asset_key)
        if qa and qa.href:
            qa_asset = append_sas_token(qa.href, token)
    cloud_cover = _safe_float(item.properties.get("eo:cloud_cover"))
    acquisition = _parse_date(item.properties.get("datetime"))
    if item.bbox and len(item.bbox) == 4:
        bbox = tuple(float(v) for v in item.bbox)  # type: ignore[assignment]
    else:
        bbox = task_bbox
    return HLSScene(
        collection_id=collection,
        item_id=item.id,
        acquisition_date=acquisition,
        cloud_cover=cloud_cover,
        bbox=bbox,
        bands=signed,
        qa_asset=qa_asset,
    )


def _safe_float(value: object) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            pass
    LOGGER.debug("hls scene missing or invalid datetime", extra={"value": value})
    return date.today()


def iter_plan(path: Path) -> Iterator[HLSMosaicTask]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                LOGGER.warning(
                    "invalid hls plan json",
                    extra={"path": str(path), "line": line_number, "error": str(exc)},
                )
                continue
            try:
                yield HLSMosaicTask.from_mapping(data)
            except ValueError as exc:
                LOGGER.warning(
                    "invalid hls plan entry",
                    extra={"path": str(path), "line": line_number, "error": str(exc)},
                )

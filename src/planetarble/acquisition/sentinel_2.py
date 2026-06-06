"""Sentinel-2 L2A acquisition helpers backed by MPC STAC."""

from __future__ import annotations

import contextlib
import hashlib
import json
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from planetarble.core.models import Sentinel2Config
from planetarble.logging import get_logger
from planetarble.acquisition.mpc import MPCError, append_sas_token, fetch_sas_token

try:
    from pystac import Item  # type: ignore
    from pystac_client import Client  # type: ignore
except Exception as exc:  # pragma: no cover - optional dependency guard
    Item = None
    Client = None
    _PYSTAC_IMPORT_ERROR = exc
else:  # pragma: no cover - import guard
    _PYSTAC_IMPORT_ERROR = None

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class Sentinel2Scene:
    item_id: str
    collection_id: str
    acquisition_date: datetime
    cloud_cover: Optional[float]
    bbox: Tuple[float, float, float, float]
    assets: Dict[str, str]


@dataclass
class Sentinel2SceneManifest:
    scenes: List[Sentinel2Scene] = field(default_factory=list)
    summary: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "summary": self.summary,
            "scenes": [scene_to_mapping(scene) for scene in self.scenes],
        }

    def write(self, path: Path, *, indent: int = 2) -> None:
        payload = self.to_dict()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=indent, sort_keys=True), encoding="utf-8")


def scene_to_mapping(scene: Sentinel2Scene) -> Dict[str, object]:
    return {
        "collection_id": scene.collection_id,
        "item_id": scene.item_id,
        "acquisition_date": scene.acquisition_date.isoformat(),
        "cloud_cover": scene.cloud_cover,
        "bbox": list(scene.bbox),
        "assets": dict(scene.assets),
    }


class Sentinel2SceneManifestBuilder:
    """Query MPC STAC and build a Sentinel-2 scene manifest."""

    def __init__(
        self,
        config: Sentinel2Config,
        *,
        cache_dir: Optional[Path] = None,
        cache_ttl_days: Optional[int] = None,
    ) -> None:
        if Client is None or Item is None:
            raise MPCError(
                "pystac-client must be available to search Microsoft Planetary Computer"
            ) from _PYSTAC_IMPORT_ERROR
        self._config = config
        self._cache_dir = cache_dir
        self._cache_ttl_days = cache_ttl_days or config.cache_ttl_days
        self._cache_dir.mkdir(parents=True, exist_ok=True) if self._cache_dir else None
        self._client = Client.open(config.stac_api, timeout=config.request_timeout_seconds)
        self._tokens: Dict[str, str] = {}

    def build(
        self,
        *,
        bbox: Tuple[float, float, float, float],
        max_items: Optional[int] = None,
        force_refresh: bool = False,
    ) -> Sentinel2SceneManifest:
        items = self._search_items(
            bbox=bbox,
            max_items=max_items or self._config.max_items,
            force_refresh=force_refresh,
        )
        scenes = self._items_to_scenes(items, target_bbox=bbox)
        summary = {
            "items": len(items),
            "scenes": len(scenes),
            "bbox": list(bbox),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        LOGGER.info(
            "constructed sentinel-2 scene manifest",
            extra={"items": len(items), "scenes": len(scenes)},
        )
        return Sentinel2SceneManifest(scenes=scenes, summary=summary)

    def _search_items(
        self,
        *,
        bbox: Tuple[float, float, float, float],
        max_items: int,
        force_refresh: bool,
    ) -> List[Item]:
        query = {"eo:cloud_cover": {"lte": self._config.max_cloud}}
        datetime_filter = f"{self._config.start_date}/{self._config.end_date}"
        cache_key = self._build_cache_key(bbox, max_items)
        if not force_refresh:
            cached_items = self._load_cache_items(cache_key)
            if cached_items is not None:
                LOGGER.info(
                    "sentinel-2 stac cache hit",
                    extra={"bbox": list(bbox), "max_items": max_items},
                )
                return cached_items
        search = self._client.search(
            collections=[self._config.collection],
            bbox=list(bbox),
            datetime=datetime_filter,
            # limit is only the pystac-client page size; max_items is the total
            # cap that stops search.items() from paging through every match.
            limit=max_items,
            max_items=max_items,
            query=query,
        )
        LOGGER.info(
            "sentinel-2 stac searching...",
            extra={
                "stac_api": self._config.stac_api,
                "collection": self._config.collection,
                "bbox": list(bbox),
                "max_items": max_items,
                "datetime": datetime_filter,
                "query": query,
                "search_timeout_seconds": self._config.stac_search_timeout_seconds,
            },
        )
        stop_event = threading.Event()
        heartbeat = threading.Thread(
            target=_log_search_heartbeat,
            args=(stop_event, self._config.stac_api, self._config.collection, list(bbox), max_items),
            daemon=True,
        )
        heartbeat.start()
        start_time = time.monotonic()
        # MPC STAC sometimes returns a server-side "request exceeded the maximum
        # allowed time" APIError (or our client-side TimeoutError) for an
        # otherwise-valid query, transiently. Retry with backoff instead of
        # failing the whole AOI on the first slow response.
        attempts = max(1, int(self._config.max_retries))
        items = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                with _search_timeout(self._config.stac_search_timeout_seconds):
                    items = list(search.items())
                break
            except Exception as exc:  # APIError (server timeout) / TimeoutError / network
                last_exc = exc
                LOGGER.warning(
                    "sentinel-2 stac search attempt failed",
                    extra={
                        "attempt": attempt, "attempts": attempts,
                        "bbox": list(bbox), "error": str(exc)[:160],
                    },
                )
                if attempt < attempts:
                    time.sleep(min(self._config.backoff_factor ** attempt, 60.0))
        stop_event.set()
        heartbeat.join(timeout=1.0)
        elapsed = max(time.monotonic() - start_time, 0.0)
        if items is None:
            LOGGER.error(
                "sentinel-2 stac search failed after retries",
                extra={
                    "attempts": attempts, "elapsed_seconds": round(elapsed, 2),
                    "bbox": list(bbox), "error": str(last_exc)[:160],
                },
            )
            raise MPCError(f"sentinel-2 stac search failed after {attempts} attempts: {last_exc}") from last_exc
        LOGGER.info(
            "sentinel-2 stac search completed",
            extra={
                "items": len(items),
                "elapsed_seconds": round(elapsed, 2),
                "bbox": list(bbox),
                "max_items": max_items,
            },
        )
        self._store_cache_items(cache_key, items)
        return items

    def _items_to_scenes(self, items: Sequence[Item], *, target_bbox: Tuple[float, float, float, float]) -> List[Sentinel2Scene]:
        scenes: List[Sentinel2Scene] = []
        collection = self._config.collection
        token = self._tokens.get(collection)
        if not token:
            token = fetch_sas_token(collection, timeout=self._config.request_timeout_seconds)
            self._tokens[collection] = token

        for item in items:
            if item.bbox is None:
                continue
            if not _bbox_covers(tuple(float(v) for v in item.bbox), target_bbox):
                continue
            scene = _build_scene(item, collection=collection, token=token, assets=self._config.assets)
            if scene:
                scenes.append(scene)
        scenes.sort(
            key=lambda scene: (
                scene.cloud_cover if scene.cloud_cover is not None else 100.0,
                -scene.acquisition_date.timestamp(),
            )
        )
        if self._config.max_items:
            scenes = scenes[: self._config.max_items]
        if not scenes:
            raise ValueError("No Sentinel-2 scenes fully cover the requested bbox")
        return scenes

    def _build_cache_key(self, bbox: Tuple[float, float, float, float], max_items: int) -> str:
        payload = {
            "collection": self._config.collection,
            "bbox": list(bbox),
            "start": self._config.start_date,
            "end": self._config.end_date,
            "max_cloud": self._config.max_cloud,
            "max_items": max_items,
            "assets": list(self._config.assets),
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
            return None
        generated_at = payload.get("generated_at")
        if not generated_at:
            return None
        try:
            timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if datetime.now(timezone.utc) - timestamp > timedelta(days=self._cache_ttl_days):
            return None
        items_data = payload.get("items") or []
        items: List[Item] = []
        for entry in items_data:
            if isinstance(entry, dict):
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
            LOGGER.debug("sentinel-2 cache store failed", extra={"path": str(path), "error": str(exc)})


def _log_search_heartbeat(
    stop_event: threading.Event,
    stac_api: str,
    collection: str,
    bbox: List[float],
    max_items: int,
    *,
    interval_seconds: int = 60,
) -> None:
    start = time.monotonic()
    while not stop_event.wait(interval_seconds):
        elapsed = max(time.monotonic() - start, 0.0)
        LOGGER.warning(
            "sentinel-2 stac search still running",
            extra={
                "stac_api": stac_api,
                "collection": collection,
                "bbox": bbox,
                "max_items": max_items,
                "elapsed_seconds": round(elapsed, 1),
            },
        )


@contextlib.contextmanager
def _search_timeout(seconds: int) -> "contextlib.AbstractContextManager[None]":
    if seconds <= 0:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _handle_timeout(signum: int, frame: object) -> None:  # pragma: no cover - signal path
        raise TimeoutError(f"Sentinel-2 STAC search exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _build_scene(item: Item, *, collection: str, token: str, assets: Iterable[str]) -> Optional[Sentinel2Scene]:
    if item.id is None or item.bbox is None:
        return None
    props = item.properties or {}
    cloud_cover = props.get("eo:cloud_cover")
    try:
        cloud_cover_value = float(cloud_cover) if cloud_cover is not None else None
    except (TypeError, ValueError):
        cloud_cover_value = None
    assets_map: Dict[str, str] = {}
    for asset_name in assets:
        asset = item.assets.get(asset_name)
        if asset is None or not asset.href:
            return None
        assets_map[asset_name] = asset.href
    acquisition = item.datetime or datetime.now(timezone.utc)
    return Sentinel2Scene(
        item_id=item.id,
        collection_id=collection,
        acquisition_date=acquisition,
        cloud_cover=cloud_cover_value,
        bbox=(float(item.bbox[0]), float(item.bbox[1]), float(item.bbox[2]), float(item.bbox[3])),
        assets=assets_map,
    )


def _bbox_covers(candidate: Tuple[float, float, float, float], target: Tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = candidate
    t_minx, t_miny, t_maxx, t_maxy = target
    return minx <= t_minx and miny <= t_miny and maxx >= t_maxx and maxy >= t_maxy

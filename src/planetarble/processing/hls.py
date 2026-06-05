"""Utilities for building HLS scene manifests from plan files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from planetarble.acquisition.hls import (
    HLSMosaicTask,
    HLSScene,
    HLSSTACClient,
    iter_plan,
    select_scene_stack,
)
from planetarble.core.models import HLSConfig
from planetarble.logging import get_logger, log_progress

LOGGER = get_logger(__name__)


@dataclass
class HLSSceneManifest:
    """Serializable manifest describing the scenes required for compositing."""

    scenes: List[HLSScene] = field(default_factory=list)
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


def scene_to_mapping(scene: HLSScene) -> Dict[str, object]:
    return {
        "collection_id": scene.collection_id,
        "item_id": scene.item_id,
        "acquisition_date": scene.acquisition_date.isoformat(),
        "cloud_cover": scene.cloud_cover,
        "bbox": list(scene.bbox),
        "bands": dict(scene.bands),
        "qa_asset": scene.qa_asset,
    }


class HLSSceneManifestBuilder:
    """Construct a manifest of HLS scenes by evaluating a plan file."""

    def __init__(
        self,
        config: HLSConfig,
        *,
        client: Optional[HLSSTACClient] = None,
        cache_dir: Optional[Path] = None,
        cache_ttl_days: Optional[int] = None,
    ) -> None:
        self._config = config
        self._client = client
        self._cache_dir = cache_dir
        self._cache_ttl_days = cache_ttl_days or config.cache_ttl_days

    def build(
        self,
        plan_path: Path,
        *,
        max_tiles: Optional[int] = None,
        max_scenes_per_tile: int = 12,
        search_limit: Optional[int] = None,
        progress_interval: int = 200,
    ) -> HLSSceneManifest:
        # the STAC search is decoupled from the keep count: search wide, then
        # select a deep sun-angle-diverse subset so the median has the votes to
        # remove transient clouds and shifting shadows.
        if search_limit is None:
            search_limit = max(max_scenes_per_tile, 100)
        client = self._client or HLSSTACClient(
            self._config,
            cache_dir=self._cache_dir,
            cache_ttl_days=self._cache_ttl_days,
        )
        seen: Dict[Tuple[str, str], HLSScene] = {}
        tiles = 0
        fallback_tiles = 0
        start_time = time.monotonic()
        total_estimate = _estimate_plan_entries(plan_path)
        progress_step = _resolve_progress_interval(progress_interval, total_estimate)
        if total_estimate == 0:
            LOGGER.warning("hls scene manifest builder detected empty plan", extra={"plan_path": str(plan_path)})
        LOGGER.info(
            "hls scene manifest start",
            extra={
                "plan_path": str(plan_path),
                "estimated_tiles": total_estimate or None,
                "progress_interval": progress_interval,
            },
        )

        for task in iter_plan(plan_path):
            next_index = tiles + 1
            if tiles % max(progress_step, 1) == 0:
                LOGGER.info(
                    "hls tile fetch start: tile=%s bbox=%s season=%s hemisphere=%s",
                    next_index,
                    list(task.bbox),
                    task.season_name,
                    task.hemisphere,
                    extra={
                        "tile_index": next_index,
                        "bbox": list(task.bbox),
                        "season": task.season_name,
                        "hemisphere": task.hemisphere,
                    },
                )
            tiles = next_index
            fetched = client.fetch_scenes(task, max_items=search_limit)
            candidates: Sequence[HLSScene] = select_scene_stack(
                fetched.get("primary", []), max_scenes_per_tile
            )
            if not candidates:
                fallback_tiles += 1
                candidates = select_scene_stack(
                    fetched.get("fallback", []), max_scenes_per_tile
                )
            for scene in candidates:
                key = (scene.collection_id, scene.item_id)
                if key not in seen:
                    seen[key] = scene
            if max_tiles is not None and tiles >= max_tiles:
                break

            if progress_step and tiles % progress_step == 0:
                _emit_progress(
                    tiles_processed=tiles,
                    tiles_total_estimate=total_estimate,
                    start_time=start_time,
                )

        if tiles and progress_step and tiles % progress_step != 0:
            _emit_progress(
                tiles_processed=tiles,
                tiles_total_estimate=total_estimate,
                start_time=start_time,
            )

        summary = {
            "tiles_evaluated": tiles,
            "unique_scenes": len(seen),
            "fallback_tiles": fallback_tiles,
            "max_scenes_per_tile": max_scenes_per_tile,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        scenes = list(seen.values())
        LOGGER.info(
            "constructed hls scene manifest",
            extra={
                "tiles": tiles,
                "unique_scenes": len(scenes),
                "fallback_tiles": fallback_tiles,
                "path": str(plan_path),
            },
        )
        return HLSSceneManifest(scenes=scenes, summary=summary)


def _emit_progress(*, tiles_processed: int, tiles_total_estimate: Optional[int], start_time: float) -> None:
    elapsed = time.monotonic() - start_time
    percent: Optional[float] = None
    eta_str: Optional[str] = None
    estimate = tiles_total_estimate or 0
    if estimate > 0:
        percent = round(min(tiles_processed / estimate, 1.0) * 100, 2)
        remaining = max(estimate - tiles_processed, 0)
        if tiles_processed > 0:
            avg_time = elapsed / tiles_processed
            eta = avg_time * remaining
            eta_str = _format_duration(eta)
    message = "hls scene manifest progress"
    if estimate > 0:
        message = (
            f"{message}: {tiles_processed}/{estimate} tiles "
            f"({percent if percent is not None else '?'}%) elapsed={_format_duration(elapsed)}"
            f"{'' if eta_str is None else f' eta={eta_str}'}"
        )
    else:
        message = f"{message}: {tiles_processed} tiles elapsed={_format_duration(elapsed)}"
    log_progress(
        LOGGER,
        phase="process",
        step="hls scene manifest",
        current=tiles_processed,
        total=estimate or None,
        percent=percent,
        elapsed=_format_duration(elapsed),
        eta=eta_str,
        extra={"detail": message},
    )


def _estimate_plan_entries(path: Path, sample_size: int = 200) -> Optional[int]:
    try:
        size = path.stat().st_size
        if size <= 0:
            return 0
        with path.open("r", encoding="utf-8") as handle:
            samples: List[str] = []
            for _ in range(sample_size):
                line = handle.readline()
                if not line:
                    break
                stripped = line.strip()
                if stripped:
                    samples.append(line)
        if not samples:
            return 0
        avg_length = sum(len(line) for line in samples) / len(samples)
        if avg_length <= 0:
            return None
        return int(max(size / avg_length, len(samples)))
    except FileNotFoundError:
        LOGGER.warning("hls plan not found when estimating entries", extra={"path": str(path)})
        return None


def _resolve_progress_interval(requested: int, total_estimate: Optional[int]) -> int:
    if total_estimate is None or total_estimate <= 0:
        return max(1, requested)
    if requested <= 0:
        return max(1, total_estimate // 200)
    return max(1, min(requested, total_estimate))


def _format_duration(seconds_value: float) -> str:
    total_seconds = int(max(seconds_value, 0))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

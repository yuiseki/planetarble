"""Copernicus tile collection and format helpers for processing."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from planetarble.core.models import CopernicusConfig
from planetarble.logging import get_logger

LOGGER = get_logger(__name__)


def _collect_copernicus_tiles(
    layer_dir: Path,
    config: CopernicusConfig,
) -> List[Tuple[Path, int, int, int]]:
    records: List[Tuple[Path, int, int, int]] = []
    valid_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    available_zooms = []
    for zoom in range(config.min_zoom, config.max_zoom + 1):
        zoom_dir = layer_dir / str(zoom)
        if not zoom_dir.exists():
            continue
        available_zooms.append(zoom)
    if not available_zooms:
        return records
    target_zoom = config.max_zoom if config.max_zoom in available_zooms else max(available_zooms)
    if target_zoom != config.max_zoom:
        LOGGER.warning(
            "copernicus max zoom tiles missing; using highest available zoom",
            extra={"requested": config.max_zoom, "available": target_zoom},
        )
    for zoom in [target_zoom]:
        zoom_dir = layer_dir / str(zoom)
        for x_dir in sorted(p for p in zoom_dir.iterdir() if p.is_dir()):
            try:
                x = int(x_dir.name)
            except ValueError:
                continue
            for tile_file in sorted(x_dir.iterdir()):
                if not tile_file.is_file():
                    continue
                if tile_file.suffix.lower() not in valid_suffixes:
                    continue
                try:
                    y = int(tile_file.stem)
                except ValueError:
                    continue
                records.append((tile_file, zoom, x, y))
    return records


def _extension_for_format(fmt: str) -> str:
    normalized = fmt.lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if normalized == "image/png":
        return "png"
    if normalized == "image/webp":
        return "webp"
    return normalized.split("/")[-1] or "bin"


def _compression_for_format(fmt: str) -> str:
    normalized = fmt.lower()
    if normalized in {"image/png", "image/webp"}:
        return "DEFLATE"
    if normalized in {"image/jpeg", "image/jpg"}:
        return "JPEG"
    return "DEFLATE"


"""Config / path resolution and validation of pipeline artefacts for the CLI."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from planetarble.acquisition.copernicus import copernicus_layer_slug
from planetarble.config import PipelineConfig
from planetarble.core.models import CopernicusLayerConfig, GSIOrthophotoConfig


def _resolve_config_path(path: Path | None) -> Path:
    if path is not None:
        resolved = path.resolve()
        if not resolved.exists():
            raise SystemExit(f"Configuration file not found: {resolved}")
        return resolved
    default_cfg = Path("configs/base/pipeline.yaml")
    if default_cfg.exists():
        return default_cfg.resolve()
    raise SystemExit("No configuration file found; supply --config or create configs/base/pipeline.yaml")


def _resolve_copernicus_cog(processing_dir: Path, layers: Iterable[CopernicusLayerConfig]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[Path] = set()

    for layer in layers or []:
        slug = copernicus_layer_slug(layer)
        candidate = processing_dir / f"copernicus_{slug}_cog.tif"
        if candidate.exists() and candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)

    for candidate in sorted(processing_dir.glob("copernicus_*_cog.tif")):
        if candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)

    return ordered



def _resolve_hls_plan_path(cfg: PipelineConfig, plan_region: Optional[str]) -> Path:
    region = plan_region or cfg.hls.plan_region
    if region:
        return cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan_{region}.ndjson"
    return cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan.ndjson"


def _resolve_hls_scene_manifest_path(cfg: PipelineConfig, plan_region: Optional[str]) -> Path:
    region = plan_region or cfg.hls.plan_region
    filename = "hls_scene_manifest.json" if not region else f"hls_scene_manifest_{region}.json"
    return (cfg.output_dir / "processing" / filename).resolve()


def _resolve_sentinel2_scene_manifest_path(cfg: PipelineConfig, plan_region: Optional[str]) -> Path:
    region = plan_region or cfg.sentinel2.plan_region
    filename = (
        "sentinel2_scene_manifest.json"
        if not region
        else f"sentinel2_scene_manifest_{region}.json"
    )
    return (cfg.output_dir / "processing" / filename).resolve()


def _is_valid_hls_scene_manifest(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scenes = data.get("scenes")
    summary = data.get("summary")
    if not isinstance(scenes, list) or not scenes:
        return False
    if not isinstance(summary, dict):
        return False
    return True


def _is_valid_sentinel2_scene_manifest(path: Path, *, required_assets: Optional[Sequence[str]] = None) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scenes = data.get("scenes")
    summary = data.get("summary")
    if not isinstance(scenes, list) or not scenes:
        return False
    if not isinstance(summary, dict):
        return False
    if required_assets:
        asset_set = {asset.lower() for asset in required_assets if asset}
        if asset_set:
            matched = False
            for scene in scenes:
                assets = scene.get("assets") if isinstance(scene, dict) else None
                if not isinstance(assets, dict):
                    continue
                available = {str(name).lower() for name in assets.keys()}
                if asset_set.issubset(available):
                    matched = True
                    break
            if not matched:
                return False
    return True



def _is_valid_mbtiles(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=1)
    except sqlite3.Error:
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tiles'")
        if cur.fetchone() is None:
            return False
        cur.execute("SELECT COUNT(*) FROM tiles")
        count = cur.fetchone()[0]
        return count > 0
    finally:
        conn.close()


def _read_mbtiles_metadata(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        conn = sqlite3.connect(str(path), timeout=1)
    except sqlite3.Error:
        return {}
    try:
        cur = conn.cursor()
        meta_exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone()
        metadata: dict[str, Any] = {}
        if meta_exists:
            for key, value in cur.execute("SELECT name, value FROM metadata"):
                metadata[str(key)] = value
        min_zoom = metadata.get("minzoom")
        max_zoom = metadata.get("maxzoom")
        if min_zoom is None or max_zoom is None:
            row = cur.execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles").fetchone()
            if row:
                if min_zoom is None:
                    min_zoom = row[0]
                if max_zoom is None:
                    max_zoom = row[1]
        if min_zoom is not None:
            metadata["minzoom"] = int(min_zoom)
        if max_zoom is not None:
            metadata["maxzoom"] = int(max_zoom)
        bounds = metadata.get("bounds")
        if isinstance(bounds, str):
            parts = [p.strip() for p in bounds.split(",")]
            if len(parts) == 4:
                try:
                    metadata["bounds"] = [float(part) for part in parts]
                except ValueError:
                    pass
        center = metadata.get("center")
        if isinstance(center, str):
            parts = [p.strip() for p in center.split(",")]
            if len(parts) == 3:
                try:
                    metadata["center"] = [float(parts[0]), float(parts[1]), int(float(parts[2]))]
                except ValueError:
                    pass
        return metadata
    finally:
        conn.close()


def _is_valid_pmtiles(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    pmtiles_cli = shutil.which("pmtiles")
    if pmtiles_cli is None:
        return True
    try:
        subprocess.run([pmtiles_cli, "verify", str(path)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        return False
    return True


def _is_valid_tilejson(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    required = ("tilejson", "tiles", "minzoom", "maxzoom", "bounds")
    return all(key in data for key in required)


def _resolve_gsi_tile_template(config: GSIOrthophotoConfig) -> str:
    template = config.tile_template
    if template:
        return template
    product = (config.product or "seamlessphoto").lower()
    if product == "seamlessphoto":
        return "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
    if product == "orthophoto":
        return "https://cyberjapandata.gsi.go.jp/xyz/ort/{z}/{x}/{y}.jpg"
    raise SystemExit(f"Unsupported gsi_orthophotos.product value: {config.product}")

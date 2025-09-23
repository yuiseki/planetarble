"""Manifest generation for downloaded assets."""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import Dict, Mapping

from planetarble.core.models import AssetManifest, AssetSource

from .download import DownloadResult


def build_manifest(
    downloads: Mapping[str, DownloadResult],
    *,
    generation_params: Dict[str, object] | None = None,
    version: str = "1.0",
) -> AssetManifest:
    sources = {
        asset_id: AssetSource(
            name=result.asset.name,
            url=result.url,
            file_size=result.size_bytes,
            sha256=result.sha256,
            license=result.asset.license,
            attribution=result.asset.attribution,
        )
        for asset_id, result in downloads.items()
    }
    manifest = AssetManifest(
        sources=sources,
        generation_params=generation_params or {},
        version=version,
    )
    return manifest


def manifest_to_dict(manifest: AssetManifest) -> Dict[str, object]:
    created_at = manifest.created_at.replace(tzinfo=timezone.utc).isoformat()
    return {
        "version": manifest.version,
        "created_at": created_at,
        "generation_params": manifest.generation_params,
        "sources": {
            key: _asset_source_to_dict(value) for key, value in manifest.sources.items()
        },
    }


def write_manifest(manifest: AssetManifest, path: Path, *, indent: int = 2) -> None:
    payload = manifest_to_dict(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=indent, sort_keys=True), encoding="utf-8")


def _asset_source_to_dict(source: AssetSource) -> Dict[str, object]:
    payload = {
        "name": source.name,
        "url": source.url,
        "file_size": source.file_size,
        "sha256": source.sha256,
        "license": source.license,
        "attribution": source.attribution,
    }
    return {key: value for key, value in payload.items() if value is not None}

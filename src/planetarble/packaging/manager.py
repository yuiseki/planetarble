"""PMTiles packaging utilities."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from planetarble.core.models import TileMetadata
from planetarble.logging import get_logger

from .base import PackagingManager as PackagingProtocol

LOGGER = get_logger(__name__)


class PackagingError(RuntimeError):
    """Raised when PMTiles packaging commands fail."""


class PackagingManager(PackagingProtocol):
    """Convert MBTiles outputs into distribution-ready artifacts."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._pmtiles_cli = shutil.which("pmtiles")
        if self._pmtiles_cli is None:
            LOGGER.warning("pmtiles CLI not found; packaging command will fail unless installed")

    def convert_to_pmtiles(self, mbtiles_path: Path, destination: Optional[Path] = None) -> Path:
        pmtiles_path = destination or mbtiles_path.with_suffix(".pmtiles")
        command = [
            self._pmtiles_cli or "pmtiles",
            "convert",
            str(mbtiles_path),
            str(pmtiles_path),
        ]
        LOGGER.info("packaging step", extra={"description": "convert MBTiles to PMTiles", "command": " ".join(command)})
        if not self._dry_run:
            try:
                subprocess.run(command, check=True)
            except subprocess.CalledProcessError as exc:  # pragma: no cover - depends on pmtiles CLI
                raise PackagingError(f"Command failed: {' '.join(command)}") from exc
        return pmtiles_path

    def generate_tilejson(self, pmtiles_path: Path, metadata: TileMetadata, destination: Optional[Path] = None) -> Path:
        tilejson_path = destination or pmtiles_path.with_suffix(".tilejson.json")
        tilejson_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tilejson": "3.0.0",
            "name": metadata.name,
            "description": metadata.description,
            "version": metadata.version,
            "attribution": metadata.attribution,
            "bounds": metadata.bounds,
            "center": metadata.center,
            "minzoom": metadata.minzoom,
            "maxzoom": metadata.maxzoom,
            "format": metadata.format.lower(),
            "scheme": metadata.scheme,
            "tiles": [f"pmtiles://{pmtiles_path.name}"],
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        LOGGER.info("packaging step", extra={"description": "write TileJSON metadata", "path": str(tilejson_path)})
        if not self._dry_run:
            tilejson_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return tilejson_path

    def create_distribution_package(
        self,
        pmtiles_path: Path,
        *,
        tilejson_path: Path,
        manifest_path: Path,
        license_text: str,
        destination: Optional[Path] = None,
    ) -> Path:
        package_dir = destination or pmtiles_path.parent / "distribution"
        package_dir.mkdir(parents=True, exist_ok=True)

        LOGGER.info("packaging step", extra={"description": "assemble distribution package", "directory": str(package_dir)})
        if not self._dry_run:
            shutil.copy2(pmtiles_path, package_dir / pmtiles_path.name)
            shutil.copy2(tilejson_path, package_dir / tilejson_path.name)
            if manifest_path.exists():
                shutil.copy2(manifest_path, package_dir / manifest_path.name)
            license_path = package_dir / "LICENSE_AND_CREDITS.txt"
            license_path.write_text(license_text, encoding="utf-8")
        return package_dir

"""PMTiles packaging utilities."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from planetarble.core.models import TileMetadata, utc_now
from planetarble.logging import get_logger, log_step

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
        log_step(LOGGER, phase="package", step="convert MBTiles to PMTiles", command=command)
        if not self._dry_run:
            try:
                subprocess.run(command, check=True)
            except subprocess.CalledProcessError as exc:  # pragma: no cover - depends on pmtiles CLI
                raise PackagingError(f"Command failed: {' '.join(command)}") from exc
        return pmtiles_path

    def set_header_center(self, pmtiles_path: Path, center: Tuple[float, float, int]) -> None:
        """Rewrite the header center in place, keeping the rest of the header.

        ``pmtiles convert`` (go-pmtiles 1.30.3) averages the bounds as E7
        int32 values; east of about 107.4 degrees the sum overflows and the
        header center lands on the other side of the globe.
        """
        cli = self._pmtiles_cli or "pmtiles"
        show = [cli, "show", str(pmtiles_path), "--header-json"]
        log_step(LOGGER, phase="package", step="set PMTiles header center", command=show,
                 extra={"center": list(center)})
        if self._dry_run:
            return
        try:
            header = json.loads(subprocess.run(show, check=True, capture_output=True, text=True).stdout)
            header["center"] = [float(center[0]), float(center[1]), int(center[2])]
            with tempfile.TemporaryDirectory() as scratch:
                header_path = Path(scratch) / "header.json"
                header_path.write_text(json.dumps(header), encoding="utf-8")
                subprocess.run([cli, "edit", str(pmtiles_path), "--header-json", str(header_path)],
                               check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise PackagingError(f"Command failed: {' '.join(exc.cmd)}\n{exc.stderr or ''}") from exc

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
            "created_at": utc_now().isoformat() + "Z",
        }
        log_step(LOGGER, phase="package", step="write TileJSON metadata", extra={"path": str(tilejson_path)})
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

        log_step(
            LOGGER,
            phase="package",
            step="assemble distribution package",
            extra={"directory": str(package_dir)},
        )
        if not self._dry_run:
            shutil.copy2(pmtiles_path, package_dir / pmtiles_path.name)
            shutil.copy2(tilejson_path, package_dir / tilejson_path.name)
            if manifest_path.exists():
                shutil.copy2(manifest_path, package_dir / manifest_path.name)
            license_path = package_dir / "LICENSE_AND_CREDITS.txt"
            license_path.write_text(license_text, encoding="utf-8")
        return package_dir

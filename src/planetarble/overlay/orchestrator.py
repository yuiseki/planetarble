"""Build orchestrator (ADR 0001, step 3).

Sequences a PipelineSpec into a planet: build the global base, build each AOI
overlay, merge the overlays onto the base in declared order (later wins), and
package the result. The heavy, GDAL- and network-bound work lives behind a
``PlanetExecutor`` so the control flow can be tested without GDAL; the real
executor wiring to the existing planners/managers is verified on a GDAL host.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Protocol

from .resolve import ResolvedAOI, resolve_aoi
from .spec import BaseSpec, Overlay, PipelineSpec
from .validate import validate_pipeline_spec


class PlanetExecutor(Protocol):
    """The heavy steps the orchestrator coordinates."""

    def build_base(self, base: BaseSpec) -> Path:
        """Build the global floor and return its MBTiles path."""

    def build_overlay(self, overlay: Overlay, resolved: ResolvedAOI) -> Path:
        """Build one AOI overlay and return its MBTiles path."""

    def merge(self, base_mbtiles: Path, overlay_mbtiles: Path) -> Path:
        """Overlay one MBTiles onto another and return the merged path."""

    def package(self, mbtiles: Path, output_name: str) -> Path:
        """Convert the final MBTiles into a PMTiles artifact and return it."""


@dataclass
class BuildResult:
    planet: Path
    base_mbtiles: Path
    overlay_mbtiles: List[Path] = field(default_factory=list)


def build_planet(
    spec: PipelineSpec,
    executor: PlanetExecutor,
    *,
    data_dir: Path,
    land_mask_path: str | None = None,
    strict: bool = True,
) -> BuildResult:
    issues = validate_pipeline_spec(spec)
    if issues and strict:
        raise ValueError("invalid pipeline spec: " + "; ".join(issues))

    base_mbtiles = executor.build_base(spec.base)
    current = base_mbtiles
    overlay_paths: List[Path] = []

    for overlay in spec.overlays:
        resolved = resolve_aoi(overlay.aoi, data_dir=data_dir, land_mask_path=land_mask_path)
        overlay_mbtiles = executor.build_overlay(overlay, resolved)
        overlay_paths.append(overlay_mbtiles)
        current = executor.merge(current, overlay_mbtiles)

    planet = executor.package(current, spec.output_name)
    return BuildResult(planet=planet, base_mbtiles=base_mbtiles, overlay_mbtiles=overlay_paths)

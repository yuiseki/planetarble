"""Build orchestrator (ADR 0001, step 3).

Sequences a PipelineSpec into a planet using the stacking model proven on the
Atami build: a global base, then for each overlay a stack composited over that
overlay's footprint from all lower sources (base + earlier overlays + this one)
with overzoom fill, merged onto the running planet in declared order. The heavy
GDAL/tiling work lives behind a ``PlanetExecutor`` so the control flow is tested
without GDAL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Protocol

from .resolve import resolve_aoi
from .spec import BaseSpec, Overlay, PipelineSpec
from .validate import validate_pipeline_spec


class PlanetExecutor(Protocol):
    """The heavy steps the orchestrator coordinates."""

    def build_base(self, base: BaseSpec) -> Path:
        """Build (or reuse) the global floor MBTiles."""

    def build_overlay_source(self, overlay: Overlay, resolved) -> Path:
        """Build one overlay's own tiled MBTiles (a composite source)."""

    def stack(self, sources: List[Path], aoi_bbox, min_zoom: int, max_zoom: int) -> Path:
        """Overzoom-composite the ordered sources (bottom..top) over the AOI."""

    def merge(self, base_mbtiles: Path, overlay_mbtiles: Path) -> Path:
        """Overlay one MBTiles onto another and return the merged path."""

    def package(self, mbtiles: Path, output_name: str) -> Path:
        """Convert the final MBTiles into a PMTiles artifact and return it."""


@dataclass
class BuildResult:
    planet: Path
    base_mbtiles: Path
    stacks: List[Path] = field(default_factory=list)


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
    planet = base_mbtiles
    sources: List[Path] = [base_mbtiles]
    stacks: List[Path] = []
    overlay_min_zoom = spec.base.max_zoom + 1

    for overlay in spec.overlays:
        resolved = resolve_aoi(overlay.aoi, data_dir=data_dir, land_mask_path=land_mask_path)
        source = executor.build_overlay_source(overlay, resolved)
        sources.append(source)
        min_zoom = overlay.min_zoom or overlay_min_zoom
        stack = executor.stack(list(sources), resolved.bbox, min_zoom, overlay.max_zoom)
        stacks.append(stack)
        planet = executor.merge(planet, stack)

    planet = executor.package(planet, spec.output_name)
    return BuildResult(planet=planet, base_mbtiles=base_mbtiles, stacks=stacks)

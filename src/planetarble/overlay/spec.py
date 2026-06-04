"""Declarative AOI overlay pipeline spec (ADR 0001).

Parses a single config that declares a global ``base`` source plus an ordered
list of ``overlays`` (each pairing an AOI with a source and a zoom range) into
typed objects. Pure data, no GDAL or network, so it is cheap to validate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .sources import SOURCE_REGISTRY


@dataclass(frozen=True)
class AOI:
    """An area of interest, selected by exactly one of several mechanisms.

    Unifies what the per-source configs accept today (bbox, natural_earth,
    miniplanet) and adds geojson.
    """

    bbox: Optional[Tuple[float, float, float, float]] = None
    natural_earth: Optional[Dict[str, Any]] = None
    miniplanet: Optional[str] = None
    geojson: Optional[str] = None
    land_only: bool = False

    _SELECTORS = ("bbox", "natural_earth", "miniplanet", "geojson")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AOI":
        if not isinstance(data, Mapping):
            raise ValueError("aoi must be a mapping")
        present = [key for key in cls._SELECTORS if data.get(key) is not None]
        if len(present) != 1:
            raise ValueError(
                f"aoi must specify exactly one of {cls._SELECTORS}, got {present or 'none'}"
            )
        bbox = None
        if data.get("bbox") is not None:
            raw = data["bbox"]
            if not isinstance(raw, (list, tuple)) or len(raw) != 4:
                raise ValueError("aoi.bbox must be a list of four numbers")
            bbox = tuple(float(v) for v in raw)
        natural_earth = None
        if data.get("natural_earth") is not None:
            ne = data["natural_earth"]
            if not isinstance(ne, Mapping):
                raise ValueError("aoi.natural_earth must be a mapping")
            natural_earth = dict(ne)
        miniplanet = str(data["miniplanet"]) if data.get("miniplanet") is not None else None
        geojson = str(data["geojson"]) if data.get("geojson") is not None else None
        return cls(
            bbox=bbox,  # type: ignore[arg-type]
            natural_earth=natural_earth,
            miniplanet=miniplanet,
            geojson=geojson,
            land_only=bool(data.get("land_only", False)),
        )


@dataclass(frozen=True)
class BaseSpec:
    """The global floor source."""

    source: str
    max_zoom: int
    min_zoom: int = 0
    resolution: Optional[str] = None


@dataclass(frozen=True)
class Overlay:
    """A single AOI-by-source overlay composited onto the base."""

    name: str
    source: str
    aoi: AOI
    max_zoom: int
    min_zoom: int = 0
    source_options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineSpec:
    """A complete custom-planet build description."""

    base: BaseSpec
    overlays: Tuple[Overlay, ...]
    output_name: str
    ocean: Dict[str, Any] = field(default_factory=dict)


def _require_source(source: str, where: str) -> None:
    if source not in SOURCE_REGISTRY:
        raise ValueError(
            f"{where}: unknown source {source!r} (known: {sorted(SOURCE_REGISTRY)})"
        )


def parse_pipeline_spec(data: Mapping[str, Any]) -> PipelineSpec:
    if not isinstance(data, Mapping):
        raise ValueError("pipeline spec must be a mapping")

    base_raw = data.get("base")
    if not isinstance(base_raw, Mapping) or not base_raw.get("source"):
        raise ValueError("pipeline spec requires a base with a source")
    _require_source(str(base_raw["source"]), "base")
    if base_raw.get("max_zoom") is None:
        raise ValueError("base.max_zoom is required")
    base = BaseSpec(
        source=str(base_raw["source"]),
        max_zoom=int(base_raw["max_zoom"]),
        min_zoom=int(base_raw.get("min_zoom", 0)),
        resolution=base_raw.get("resolution"),
    )

    overlays: List[Overlay] = []
    for index, raw in enumerate(data.get("overlays", []) or []):
        if not isinstance(raw, Mapping):
            raise ValueError(f"overlays[{index}] must be a mapping")
        name = raw.get("name")
        source = raw.get("source")
        if not name or not source or raw.get("aoi") is None:
            raise ValueError(f"overlays[{index}] requires name, source, and aoi")
        _require_source(str(source), f"overlays[{index}]")
        if raw.get("max_zoom") is None:
            raise ValueError(f"overlays[{index}] ({name}) requires max_zoom")
        overlays.append(
            Overlay(
                name=str(name),
                source=str(source),
                aoi=AOI.from_mapping(raw["aoi"]),
                max_zoom=int(raw["max_zoom"]),
                min_zoom=int(raw.get("min_zoom", 0)),
                source_options=dict(raw.get("source_options") or {}),
            )
        )

    output_raw = data.get("output") or {}
    output_name = str(output_raw.get("name") or "planet")

    return PipelineSpec(
        base=base,
        overlays=tuple(overlays),
        output_name=output_name,
        ocean=dict(data.get("ocean") or {}),
    )

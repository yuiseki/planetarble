"""Declarative AOI overlay pipeline (ADR 0001): global floor plus AOI-by-source overlays."""

from .adapters import adapter_sources, get_adapter
from .resolve import ResolvedAOI, resolve_aoi
from .sources import SOURCE_REGISTRY, SourceAdapter, SourceInfo, known_sources
from .spec import AOI, BaseSpec, Overlay, PipelineSpec, parse_pipeline_spec
from .validate import validate_pipeline_spec

__all__ = [
    "AOI",
    "BaseSpec",
    "Overlay",
    "PipelineSpec",
    "parse_pipeline_spec",
    "ResolvedAOI",
    "resolve_aoi",
    "adapter_sources",
    "get_adapter",
    "SOURCE_REGISTRY",
    "SourceAdapter",
    "SourceInfo",
    "known_sources",
    "validate_pipeline_spec",
]

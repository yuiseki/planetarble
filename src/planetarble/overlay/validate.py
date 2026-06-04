"""Validation for AOI overlay pipeline specs (ADR 0001).

Keeps SOURCE.md as the single source of truth for resolution limits: a spec may
not request a zoom above a source's advertised ceiling, so configs cannot
silently oversample.
"""

from __future__ import annotations

from typing import List

from .sources import SOURCE_REGISTRY
from .spec import PipelineSpec


def validate_pipeline_spec(spec: PipelineSpec) -> List[str]:
    """Return a list of human-readable issues; empty means the spec is sound."""
    issues: List[str] = []

    base_info = SOURCE_REGISTRY.get(spec.base.source)
    if base_info is not None and spec.base.max_zoom > base_info.native_max_zoom:
        issues.append(
            f"base source {spec.base.source!r} max_zoom {spec.base.max_zoom} "
            f"exceeds its ceiling z{base_info.native_max_zoom}"
        )

    seen = set()
    for overlay in spec.overlays:
        if overlay.name in seen:
            issues.append(f"duplicate overlay name {overlay.name!r}")
        seen.add(overlay.name)

        info = SOURCE_REGISTRY.get(overlay.source)
        if info is not None and overlay.max_zoom > info.native_max_zoom:
            issues.append(
                f"overlay {overlay.name!r} source {overlay.source!r} max_zoom "
                f"{overlay.max_zoom} exceeds its ceiling z{info.native_max_zoom}"
            )
        if overlay.min_zoom > overlay.max_zoom:
            issues.append(
                f"overlay {overlay.name!r} min_zoom {overlay.min_zoom} > max_zoom {overlay.max_zoom}"
            )

    return issues

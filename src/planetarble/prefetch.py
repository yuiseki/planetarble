"""Throttle-aware prefetch pacing.

Prefetch downloads imagery (whole COGs) into the cache ahead of any tiling, so a
later build is download-free. Microsoft Planetary Computer rate-limits by
*slowing down* rather than blocking (see docs/operations/mpc-rate-limits.md), so
between tiles we pace: a short random jitter when throughput was healthy, and a
longer cooldown when the last tile came down throttled. The pacing decision is a
pure function (rng injected) so it is unit tested without sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional


@dataclass(frozen=True)
class PrefetchPacing:
    """Tunables for inter-tile pacing (all seconds except the floor)."""

    throttle_floor_kibps: float = 150.0
    jitter_min_s: float = 60.0
    jitter_max_s: float = 300.0
    cooldown_min_s: float = 600.0
    cooldown_max_s: float = 900.0


def prefetch_wait_seconds(
    downloaded_bytes: int,
    elapsed_seconds: float,
    pacing: PrefetchPacing,
    rng: Callable[[float, float], float],
) -> float:
    """Seconds to wait AFTER fetching one tile's assets, before the next tile.

    - Nothing actually downloaded (all cache hits): 0 — don't pace, we didn't
      touch the network and want cached tiles to fly by.
    - Otherwise compute effective KiB/s over the bytes fetched: below
      ``throttle_floor_kibps`` we were throttled, so back off into the cooldown
      range; healthy throughput only needs the short jitter range.

    ``rng(a, b)`` is injected (e.g. ``random.uniform``) for deterministic tests.
    """
    if downloaded_bytes <= 0:
        return 0.0
    if elapsed_seconds <= 0:
        speed_kibps = float("inf")
    else:
        speed_kibps = (downloaded_bytes / 1024.0) / elapsed_seconds
    if speed_kibps < pacing.throttle_floor_kibps:
        return rng(pacing.cooldown_min_s, pacing.cooldown_max_s)
    return rng(pacing.jitter_min_s, pacing.jitter_max_s)


@dataclass(frozen=True)
class PrefetchStats:
    """Outcome of prefetching one overlay's Sentinel-2 assets."""

    overlay: str
    downloaded_count: int = 0
    downloaded_bytes: int = 0
    hit_count: int = 0
    elapsed_seconds: float = 0.0


def prefetch_planet(
    spec: Any,
    executor: Any,
    *,
    pacer: Callable[[PrefetchStats], None],
    on_skip: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[Any, Exception], None]] = None,
) -> List[PrefetchStats]:
    """Download-only pass: warm the cache for each Sentinel-2 overlay, no tiling.

    Iterates the spec's overlays; for each ``sentinel2`` overlay it asks the
    executor to fetch that AOI's assets and then calls ``pacer`` (which sleeps
    according to the measured throughput). Non-Sentinel-2 overlays are skipped.

    Resilient by design: a per-overlay error (e.g. a transient MPC STAC timeout)
    is reported via ``on_error`` and the run CONTINUES to the next overlay rather
    than aborting the whole unattended job. Already-fetched overlays stay cached,
    so re-running picks up only the ones that failed. The executor and pacer are
    injected so the control flow is unit tested without GDAL, network or sleeps.
    """
    results: List[PrefetchStats] = []
    for overlay in spec.overlays:
        if getattr(overlay, "source", None) != "sentinel2":
            if on_skip is not None:
                on_skip(overlay)
            continue
        try:
            stats = executor.prefetch_overlay(overlay)
        except Exception as exc:  # noqa: BLE001 - keep the unattended run going
            if on_error is not None:
                on_error(overlay, exc)
            continue
        results.append(stats)
        pacer(stats)
    return results

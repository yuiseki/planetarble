"""Throttle-aware prefetch pacing.

Prefetch downloads imagery (whole COGs) into the cache ahead of any tiling, so a
later build is download-free. Microsoft Planetary Computer rate-limits by
*slowing down* rather than blocking (see docs/operations/mpc-rate-limits.md), so
between tiles we pace: a short random jitter when throughput was healthy, and a
longer cooldown when the last tile came down throttled. The pacing decision is a
pure function (rng injected) so it is unit tested without sleeping.
"""

from __future__ import annotations

import time
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
    recovery_wait_s: float = 0.0,
    max_rounds: int = 1,
    on_recovery_wait: Optional[Callable[[int, int, float], None]] = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> List[PrefetchStats]:
    """Download-only pass: warm the cache for each Sentinel-2 overlay, no tiling.

    For each ``sentinel2`` overlay it asks the executor to fetch that AOI's
    assets, then calls ``pacer`` (which sleeps per measured throughput).
    Non-Sentinel-2 overlays are skipped.

    Two layers of resilience for unattended runs:

    1. Per-overlay: an error (e.g. a transient MPC STAC timeout, already retried
       inside the STAC client) is reported via ``on_error`` and the run CONTINUES
       to the next overlay instead of aborting.
    2. Recovery rounds: when MPC has a *broader* outage (every query times out),
       a whole overlay still fails after its per-request retries. With
       ``max_rounds > 1`` the still-failed overlays are retried in later rounds,
       waiting ``recovery_wait_s`` between rounds for MPC to recover (this is the
       built-in equivalent of the external "wait for MPC, then resume" watcher).
       Already-fetched overlays are cached, so each round only re-attempts the
       failures.

    ``sleeper`` and the callbacks are injected so the control flow is unit tested
    without GDAL, network or real sleeps. Returns the successful PrefetchStats.
    """
    results: List[PrefetchStats] = []
    pending: List[Any] = []
    for overlay in spec.overlays:
        if getattr(overlay, "source", None) != "sentinel2":
            if on_skip is not None:
                on_skip(overlay)
            continue
        pending.append(overlay)

    rounds = max(1, int(max_rounds))
    for round_index in range(1, rounds + 1):
        failed: List[Any] = []
        for overlay in pending:
            try:
                stats = executor.prefetch_overlay(overlay)
            except Exception as exc:  # noqa: BLE001 - keep the unattended run going
                if on_error is not None:
                    on_error(overlay, exc)
                failed.append(overlay)
                continue
            results.append(stats)
            pacer(stats)
        if not failed:
            break
        pending = failed
        if round_index < rounds and recovery_wait_s > 0:
            if on_recovery_wait is not None:
                on_recovery_wait(round_index, len(pending), recovery_wait_s)
            sleeper(recovery_wait_s)
    return results

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
from typing import Callable


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

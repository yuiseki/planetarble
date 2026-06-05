"""Tests for the throttle-aware prefetch pacing decision (pure)."""

from __future__ import annotations

from planetarble.prefetch import PrefetchPacing, prefetch_wait_seconds


class _RecordingRng:
    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, a: float, b: float) -> float:
        self.calls.append((a, b))
        return a  # deterministic


PACING = PrefetchPacing(
    throttle_floor_kibps=150.0,
    jitter_min_s=60.0, jitter_max_s=300.0,
    cooldown_min_s=600.0, cooldown_max_s=900.0,
)


def test_no_download_no_wait() -> None:
    rng = _RecordingRng()
    # all cache hits -> 0 bytes -> no wait, rng untouched
    assert prefetch_wait_seconds(0, 5.0, PACING, rng) == 0.0
    assert rng.calls == []


def test_healthy_speed_uses_jitter_range() -> None:
    rng = _RecordingRng()
    # 10 MiB in 20 s = 512 KiB/s > 150 floor -> jitter
    wait = prefetch_wait_seconds(10 * 1024 * 1024, 20.0, PACING, rng)
    assert rng.calls == [(60.0, 300.0)]
    assert wait == 60.0


def test_throttled_speed_uses_cooldown_range() -> None:
    rng = _RecordingRng()
    # 1 MiB in 20 s = 51 KiB/s < 150 floor -> cooldown
    prefetch_wait_seconds(1024 * 1024, 20.0, PACING, rng)
    assert rng.calls == [(600.0, 900.0)]


def test_zero_elapsed_treated_as_fast() -> None:
    rng = _RecordingRng()
    prefetch_wait_seconds(1024 * 1024, 0.0, PACING, rng)
    assert rng.calls == [(60.0, 300.0)]  # infinite speed -> jitter

from pathlib import Path

import pytest

from planetarble.acquisition.copernicus import CopernicusAccessError, _RateLimiter


def test_rate_limiter_persists_across_instances(tmp_path: Path) -> None:
    state_path = tmp_path / "rate_limit.json"
    limiter = _RateLimiter(
        state_path,
        min_interval_seconds=0.0,
        max_requests=1,
        window_seconds=3600,
    )
    limiter.acquire()

    limiter2 = _RateLimiter(
        state_path,
        min_interval_seconds=0.0,
        max_requests=1,
        window_seconds=3600,
    )
    with pytest.raises(CopernicusAccessError):
        limiter2.acquire()

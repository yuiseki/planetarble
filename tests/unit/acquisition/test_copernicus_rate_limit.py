from pathlib import Path

import pytest

from planetarble.acquisition.copernicus import CopernicusAccessError, _RateLimiter, _fetch_tile
from planetarble.core.models import CopernicusConfig


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


class _StubResponse:
    def __init__(self, status_code: int, retry_after: str | None = None) -> None:
        self.status_code = status_code
        self.headers = {}
        if retry_after is not None:
            self.headers["Retry-After"] = retry_after
        self.content = b""


class _StubSession:
    def __init__(self, responses: list[_StubResponse]) -> None:
        self._responses = responses

    def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self._responses.pop(0)


class _StubCreds:
    pass


def test_fetch_tile_respects_retry_after(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    responses = [_StubResponse(429, retry_after="200"), _StubResponse(200)]
    session = _StubSession(responses)
    config = CopernicusConfig(request_interval_seconds=0.1, backoff_factor=2.0)
    destination = tmp_path / "tile.jpg"
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("planetarble.acquisition.copernicus.time.sleep", fake_sleep)

    success, status = _fetch_tile(
        session=session,
        base_url="https://example",
        params={},
        timeout=5,
        credentials=_StubCreds(),
        config=config,
        layer_name="TRUE_COLOR",
        zoom=12,
        x=1,
        y=2,
        destination=destination,
        rate_limiter=None,
    )

    assert success is True
    assert status == 200
    assert slept and slept[0] == pytest.approx(0.2, rel=1e-2)

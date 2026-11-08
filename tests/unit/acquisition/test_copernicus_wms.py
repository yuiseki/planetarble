"""Copernicus (CDSE Sentinel Hub) WMS: token, capabilities, tile download loop.

requests and time.sleep are faked; nothing reaches dataspace.copernicus.eu.
Tile math and the Retry-After case live in their own test modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from planetarble.acquisition import copernicus
from planetarble.acquisition.copernicus import (
    CopernicusAccessError,
    CopernicusAuthError,
    CopernicusCredentials,
    CopernicusCredentialsMissing,
    _extension_for_format,
    _fetch_tile,
    _RateLimiter,
    download_tiles,
    get_available_layers,
    list_wms_layers,
    request_access_token,
    verify_wms_access,
)
from planetarble.core.models import CopernicusConfig, CopernicusLayerConfig

CREDS = CopernicusCredentials(instance_id="inst", client_id="cid", client_secret="secret")


class Response:
    def __init__(self, status: int = 200, payload: Any = None, *, text: str = "", content: bytes = b"",
                 headers: Optional[Dict[str, str]] = None) -> None:
        self.status_code = status
        self._payload = payload
        self.text = text
        self.content = content
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> List[float]:
    slept: List[float] = []
    monkeypatch.setattr(copernicus.time, "sleep", slept.append)
    return slept


# --- credentials and token ----------------------------------------------------


def test_credentials_need_all_three_variables(monkeypatch) -> None:
    monkeypatch.setenv("COPERNICUS_INSTANCE_ID", "i")
    monkeypatch.setenv("COPERNICUS_CLIENT_ID", "c")
    monkeypatch.delenv("COPERNICUS_CLIENT_SECRET", raising=False)
    with pytest.raises(CopernicusCredentialsMissing):
        CopernicusCredentials.from_env()
    monkeypatch.setenv("COPERNICUS_CLIENT_SECRET", "s")
    assert CopernicusCredentials.from_env() == CopernicusCredentials("i", "c", "s")


def test_access_token_uses_the_client_credentials_grant(monkeypatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_post(url, data, timeout):
        seen.update(url=url, data=data)
        return Response(payload={"access_token": "tok"})

    monkeypatch.setattr(copernicus.requests, "post", fake_post)
    assert request_access_token(CREDS) == "tok"
    assert seen["url"] == copernicus.TOKEN_ENDPOINT
    assert seen["data"] == {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "secret"}


@pytest.mark.parametrize("response", [Response(401, text="invalid_client"), Response(payload={})])
def test_access_token_failures(monkeypatch, response: Response) -> None:
    monkeypatch.setattr(copernicus.requests, "post", lambda url, data, timeout: response)
    with pytest.raises(CopernicusAuthError):
        request_access_token(CREDS)


def test_verify_wms_access_sends_the_bearer_token(monkeypatch) -> None:
    seen: Dict[str, Any] = {}
    monkeypatch.setattr(copernicus, "request_access_token", lambda creds, timeout: "tok")

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, headers=headers, params=params)
        return Response(200)

    monkeypatch.setattr(copernicus.requests, "get", fake_get)
    assert verify_wms_access(CREDS) is True
    assert seen["url"] == "https://sh.dataspace.copernicus.eu/ogc/wms/inst"
    assert seen["headers"] == {"Authorization": "Bearer tok"}
    assert seen["params"]["request"] == "GetCapabilities"


def test_verify_wms_access_failure(monkeypatch) -> None:
    monkeypatch.setattr(copernicus, "request_access_token", lambda creds, timeout: "tok")
    monkeypatch.setattr(copernicus.requests, "get", lambda url, params, headers, timeout: Response(403, text="no"))
    with pytest.raises(CopernicusAccessError, match="403"):
        verify_wms_access(CREDS)


# --- capabilities -------------------------------------------------------------

CAPABILITIES = """<?xml version="1.0"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
  <Capability>
    <Layer>
      <Title>root without a name</Title>
      <Layer><Name>TRUE_COLOR</Name><Title>True color</Title></Layer>
      <Layer><Name> NDVI </Name></Layer>
      <Layer><Name></Name><Title>empty</Title></Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>"""


def test_list_wms_layers_reads_named_layers() -> None:
    assert list_wms_layers(CAPABILITIES) == [("TRUE_COLOR", "True color"), ("NDVI", "NDVI")]


def test_list_wms_layers_without_capability_is_empty() -> None:
    assert list_wms_layers('<WMS_Capabilities xmlns="http://www.opengis.net/wms"/>') == []


def test_available_layers_need_an_instance(monkeypatch) -> None:
    monkeypatch.delenv("COPERNICUS_INSTANCE_ID", raising=False)
    with pytest.raises(CopernicusCredentialsMissing):
        get_available_layers()


def test_available_layers_fall_back_to_anonymous_when_the_token_fails(monkeypatch) -> None:
    tokens: List[Optional[str]] = []
    monkeypatch.setattr(CopernicusCredentials, "from_env", classmethod(lambda cls: CREDS))

    def no_token(creds, timeout):
        raise CopernicusAuthError("down")

    def fake_capabilities(*, instance_id, token, timeout):
        tokens.append(token)
        return CAPABILITIES

    monkeypatch.setattr(copernicus, "request_access_token", no_token)
    monkeypatch.setattr(copernicus, "fetch_wms_capabilities", fake_capabilities)

    assert get_available_layers(instance_id="inst")[0] == ("TRUE_COLOR", "True color")
    assert tokens == [None]


# --- tile download loop -------------------------------------------------------


class Session:
    def __init__(self, responder) -> None:
        self.responder = responder
        self.headers: Dict[str, str] = {}
        self.requests: List[Dict[str, str]] = []
        self.closed = False

    def get(self, url, params, timeout):
        self.requests.append(dict(params))
        return self.responder(params)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def wms(monkeypatch):
    state: Dict[str, Any] = {"responder": lambda params: Response(200, content=b"JPEG")}
    monkeypatch.setattr(copernicus, "request_access_token", lambda creds, timeout: "tok")
    monkeypatch.setattr(copernicus, "fetch_wms_capabilities", lambda **kw: CAPABILITIES)

    def make_session():
        state["session"] = Session(lambda params: state["responder"](params))
        return state["session"]

    monkeypatch.setattr(copernicus.requests, "Session", make_session)
    return state


def _config(**kwargs: Any) -> CopernicusConfig:
    base: Dict[str, Any] = dict(
        bbox=(139.70, 35.65, 139.80, 35.70), min_zoom=12, max_zoom=13, request_interval_seconds=0.0,
        layers=(CopernicusLayerConfig(name="TRUE_COLOR", time="2024-05-01/2024-05-31"),),
    )
    base.update(kwargs)
    return CopernicusConfig(**base)


def test_tiles_are_written_per_layer_zoom_x_y(tmp_path: Path, wms) -> None:
    (summary,) = download_tiles(CREDS, _config(), tmp_path)

    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.jpg"))
    assert summary["tiles_written"] == len(written) == summary["tile_count_estimate"]
    assert all(p.startswith("true_color/1") for p in written)
    first = wms["session"].requests[0]
    assert first["LAYERS"] == "TRUE_COLOR" and first["CRS"] == "EPSG:3857" and first["TIME"] == "2024-05-01/2024-05-31"
    assert first["WIDTH"] == first["HEIGHT"] == "256"
    assert wms["session"].headers["Authorization"] == "Bearer tok"
    assert wms["session"].closed
    assert (tmp_path / "capabilities.xml").read_text() == CAPABILITIES


def test_existing_tiles_are_skipped_unless_forced(tmp_path: Path, wms) -> None:
    download_tiles(CREDS, _config(), tmp_path)
    (summary,) = download_tiles(CREDS, _config(), tmp_path)
    assert summary["tiles_written"] == 0 and summary["tiles_skipped"] == summary["tile_count_estimate"]
    (forced,) = download_tiles(CREDS, _config(), tmp_path, force=True)
    assert forced["tiles_written"] == forced["tile_count_estimate"]


def test_max_tiles_per_layer_stops_the_layer(tmp_path: Path, wms) -> None:
    (summary,) = download_tiles(CREDS, _config(max_tiles_per_layer=2), tmp_path)
    assert summary["tiles_written"] == 2
    assert summary["limit_reached"] is True
    assert len(wms["session"].requests) == 2


def test_forbidden_stops_the_layer_after_one_request(tmp_path: Path, wms) -> None:
    wms["responder"] = lambda params: Response(403, text="quota exceeded")
    (summary,) = download_tiles(CREDS, _config(), tmp_path)
    assert (summary["tiles_written"], summary["tiles_failed"], summary["limit_reached"]) == (0, 1, True)
    assert len(wms["session"].requests) == 1


def test_capabilities_failure_does_not_stop_the_download(tmp_path: Path, wms, monkeypatch) -> None:
    def failing(**kw):
        raise CopernicusAccessError("no capabilities")

    monkeypatch.setattr(copernicus, "fetch_wms_capabilities", failing)
    (summary,) = download_tiles(CREDS, _config(), tmp_path)
    assert summary["tiles_written"] > 0


def test_no_layers_means_no_requests(tmp_path: Path, wms) -> None:
    assert download_tiles(CREDS, _config(layers=()), tmp_path) == []
    assert "session" not in wms


# --- single tile retries ------------------------------------------------------


def _fetch(session, tmp_path: Path, **config: Any):
    return _fetch_tile(
        session=session, base_url="https://wms", params={}, timeout=5, credentials=CREDS,
        config=CopernicusConfig(request_interval_seconds=1.0, backoff_factor=2.0, **config),
        layer_name="L", zoom=1, x=0, y=0, destination=tmp_path / "t.jpg", rate_limiter=None,
    )


def test_expired_token_is_refreshed_and_the_tile_retried(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(copernicus, "request_access_token", lambda creds, timeout: "fresh")
    responses = iter([Response(401), Response(200, content=b"tile")])
    session = Session(lambda params: next(responses))

    assert _fetch(session, tmp_path) == (True, 200)
    assert session.headers["Authorization"] == "Bearer fresh"
    assert (tmp_path / "t.jpg").read_bytes() == b"tile"


def test_server_errors_are_retried_with_backoff_then_given_up(tmp_path: Path, sleeps: List[float]) -> None:
    session = Session(lambda params: Response(503, text="busy"))
    assert _fetch(session, tmp_path, max_retries=3) == (False, 503)
    assert len(session.requests) == 3
    assert sleeps == [2.0, 4.0, 8.0]
    assert not (tmp_path / "t.jpg").exists()


def test_rate_limit_without_retry_after_uses_backoff(tmp_path: Path, sleeps: List[float]) -> None:
    responses = iter([Response(429), Response(200, content=b"x")])
    assert _fetch(Session(lambda params: next(responses)), tmp_path) == (True, 200)
    assert sleeps == [2.0]


# --- rate limiter -------------------------------------------------------------


def test_rate_limiter_window_resets(tmp_path: Path, monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(copernicus.time, "time", lambda: clock[0])
    limiter = _RateLimiter(tmp_path / "rl.json", min_interval_seconds=0, max_requests=1, window_seconds=60)
    limiter.acquire()
    with pytest.raises(CopernicusAccessError, match="wait 30s"):
        clock[0] = 1030.0
        limiter.acquire()
    clock[0] = 1061.0
    limiter.acquire()  # a new window


def test_rate_limiter_spaces_requests(tmp_path: Path, monkeypatch, sleeps: List[float]) -> None:
    monkeypatch.setattr(copernicus.time, "time", lambda: 500.0)
    limiter = _RateLimiter(tmp_path / "rl.json", min_interval_seconds=2.5, max_requests=None, window_seconds=60)
    limiter.acquire()
    limiter.acquire()
    assert sleeps == [2.5]


def test_rate_limiter_ignores_a_corrupt_state_file(tmp_path: Path) -> None:
    (tmp_path / "rl.json").write_text("{not json")
    _RateLimiter(tmp_path / "rl.json", min_interval_seconds=0, max_requests=1, window_seconds=60).acquire()


@pytest.mark.parametrize(
    ("fmt", "ext"), [("image/jpeg", "jpg"), ("IMAGE/PNG", "png"), ("image/tiff", "tiff"), ("", "bin")]
)
def test_extension_for_format(fmt: str, ext: str) -> None:
    assert _extension_for_format(fmt) == ext

"""AppEEARS client and MODIS/VIIRS task helpers, against a fake HTTP session."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import requests

from planetarble.acquisition import appeears
from planetarble.acquisition.appeears import (
    API_ROOT,
    AppEEARSAuthError,
    AppEEARSClient,
    AppEEARSDownloadError,
    _default_viirs_layers,
    _parse_content_disposition,
    download_mcd43a4_tiles,
    download_viirs_corrected_reflectance,
    modis_tile_polygon,
)


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, *, headers: Optional[Dict[str, str]] = None,
                 body: bytes = b"") -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)
        self.headers = headers or {}
        self._body = body

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, chunk_size: int):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class FakeSession:
    def __init__(self, routes: Dict[str, Any]) -> None:
        self.routes = routes
        self.headers: Dict[str, str] = {}
        self.calls: List[Dict[str, Any]] = []

    def _route(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        handler = self.routes.get((method, url.replace(API_ROOT, "")))
        if handler is None:
            return FakeResponse(404, {"message": "no route"})
        return handler(**kwargs) if callable(handler) else handler

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._route("POST", url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._route("GET", url, **kwargs)


# --- credentials and login ----------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch) -> None:
    for key in ("EARTHDATA_USERNAME", "EARTHDATA_PASSWORD", "APPEEARS_AUTHORIZATION", "APPEEARS_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_from_env_prefers_username_and_password(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("EARTHDATA_USERNAME", "u")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "p")
    monkeypatch.setenv("APPEEARS_TOKEN", "t")
    client = AppEEARSClient.from_env(session=FakeSession({}))
    assert (client._username, client._password, client._authorization) == ("u", "p", None)


@pytest.mark.parametrize(("token", "header"), [("abc", "Bearer abc"), ("Bearer abc", "Bearer abc")])
def test_from_env_turns_a_token_into_a_bearer_header(clean_env, monkeypatch, token: str, header: str) -> None:
    monkeypatch.setenv("APPEEARS_TOKEN", token)
    session = FakeSession({})
    AppEEARSClient.from_env(session=session)
    assert session.headers["Authorization"] == header


def test_from_env_without_credentials_is_an_auth_error(clean_env) -> None:
    with pytest.raises(AppEEARSAuthError):
        AppEEARSClient.from_env()


def test_login_and_logout_with_username_and_password() -> None:
    session = FakeSession({("POST", "login"): FakeResponse(payload={"token": "tok"}),
                           ("POST", "logout"): FakeResponse(204)})
    with AppEEARSClient("u", "p", session=session):  # type: ignore[arg-type]
        assert session.headers["Authorization"] == "Bearer tok"
        assert session.calls[0]["auth"] == ("u", "p")
    assert "Authorization" not in session.headers
    assert [c["url"] for c in session.calls] == [f"{API_ROOT}login", f"{API_ROOT}logout"]


@pytest.mark.parametrize("response", [FakeResponse(401, "bad"), FakeResponse(payload={})])
def test_failed_login_is_an_auth_error(response: FakeResponse) -> None:
    client = AppEEARSClient("u", "p", session=FakeSession({("POST", "login"): response}))  # type: ignore[arg-type]
    with pytest.raises(AppEEARSAuthError):
        client.login()


def test_login_without_credentials_is_an_auth_error() -> None:
    with pytest.raises(AppEEARSAuthError):
        AppEEARSClient(session=FakeSession({})).login()  # type: ignore[arg-type]


def test_preset_authorization_needs_no_login_request() -> None:
    session = FakeSession({})
    with AppEEARSClient(authorization="Bearer x", session=session):  # type: ignore[arg-type]
        assert session.headers["Authorization"] == "Bearer x"
    assert session.calls == []
    assert "Authorization" not in session.headers


# --- tasks --------------------------------------------------------------------


def _client(routes: Dict[Any, Any]) -> AppEEARSClient:
    return AppEEARSClient(authorization="Bearer x", poll_interval=0, session=FakeSession(routes))  # type: ignore[arg-type]


def test_submit_area_task_payload() -> None:
    client = _client({("POST", "task"): FakeResponse(payload={"task_id": "T1"})})
    polygon = modis_tile_polygon("h31v05")

    task_id = client.submit_area_task(
        task_name="n", product="MCD43A4.061", start_date=date(2024, 5, 29), end_date=date(2024, 5, 30),
        polygon=polygon, layers=["L1", "L2"],
    )

    assert task_id == "T1"
    payload = client._session.calls[0]["json"]  # type: ignore[attr-defined]
    assert payload["task_type"] == "area"
    assert payload["params"]["dates"] == [{"startDate": "05-29-2024", "endDate": "05-30-2024"}]
    assert payload["params"]["layers"] == [{"product": "MCD43A4.061", "layer": "L1"},
                                           {"product": "MCD43A4.061", "layer": "L2"}]
    assert payload["params"]["geo"]["features"] == [polygon]
    assert payload["params"]["output"] == {"format": {"type": "geotiff"}, "projection": "geographic"}


@pytest.mark.parametrize(
    ("response", "match"),
    [(FakeResponse(400, {"message": "bad layer"}), "bad layer"), (FakeResponse(payload={}), "task_id")],
)
def test_submit_area_task_errors(response: FakeResponse, match: str) -> None:
    client = _client({("POST", "task"): response})
    with pytest.raises(AppEEARSDownloadError, match=match):
        client.submit_area_task(task_name="n", product="p", start_date=date(2024, 1, 1), end_date=date(2024, 1, 1),
                                polygon={}, layers=["L"])


def test_wait_for_tasks_polls_until_each_is_done_or_failed(monkeypatch) -> None:
    sleeps: List[float] = []
    monkeypatch.setattr(appeears.time, "sleep", sleeps.append)
    states = {"A": iter(["queued", "processing", "done"]), "B": iter(["error"])}
    client = _client({
        ("GET", "task/A"): lambda **kw: FakeResponse(payload={"status": next(states["A"])}),
        ("GET", "task/B"): lambda **kw: FakeResponse(payload={"status": next(states["B"])}),
    })
    assert client.wait_for_tasks({"h1": "A", "h2": "B"}) == {"h1": True, "h2": False}
    assert len(sleeps) == 3


def test_download_file_uses_the_server_file_name(tmp_path: Path) -> None:
    client = _client({("GET", "bundle/T/F"): FakeResponse(
        headers={"Content-Disposition": 'attachment; filename="MCD43A4_B1.tif"'}, body=b"x" * 20000)})
    path = client.download_file("T", "F", tmp_path / "d" / "fallback.tif")
    assert path == tmp_path / "d" / "MCD43A4_B1.tif"
    assert path.read_bytes() == b"x" * 20000


def test_download_file_cannot_escape_the_destination(tmp_path: Path) -> None:
    client = _client({("GET", "bundle/T/F"): FakeResponse(
        headers={"Content-Disposition": "attachment; filename=../../evil.tif"}, body=b"x")})
    path = client.download_file("T", "F", tmp_path / "d" / "fallback.tif")
    assert path == tmp_path / "d" / "evil.tif"


def test_download_file_falls_back_to_the_given_name(tmp_path: Path) -> None:
    client = _client({("GET", "bundle/T/F"): FakeResponse(body=b"x")})
    assert client.download_file("T", "F", tmp_path / "f.tif") == tmp_path / "f.tif"


@pytest.mark.parametrize(
    ("header", "name"),
    [(None, None), ("attachment", None), ('attachment; filename="a.tif"', "a.tif"), ("attachment; filename=b.tif", "b.tif"),
     ('attachment; filename=""', None)],
)
def test_parse_content_disposition(header, name) -> None:
    assert _parse_content_disposition(header) == name


# --- MODIS / VIIRS helpers ----------------------------------------------------


class ScriptedClient:
    def __init__(self, statuses: Dict[str, bool], files: List[Dict[str, Any]]) -> None:
        self.submitted: List[Dict[str, Any]] = []
        self.statuses = statuses
        self.files = files
        self.downloaded: List[Any] = []

    def submit_area_task(self, **kwargs: Any) -> str:
        self.submitted.append(kwargs)
        return f"task-{kwargs['polygon']['properties']['tile']}"

    def wait_for_tasks(self, tasks: Dict[str, str]) -> Dict[str, bool]:
        return {tile: self.statuses.get(tile, True) for tile in tasks}

    def list_bundle_files(self, task_id: str) -> List[Dict[str, Any]]:
        return self.files

    def download_file(self, task_id: str, file_id: str, destination: Path) -> Path:
        self.downloaded.append((task_id, file_id, destination))
        return destination


def test_mcd43a4_submits_one_task_per_tile_and_downloads_bundles(tmp_path: Path) -> None:
    client = ScriptedClient({}, [{"file_id": "f1", "file_name": "a.tif"}, {"file_id": "f2"}])

    outputs = download_mcd43a4_tiles(
        client, date_value=date(2024, 5, 29), tiles=["h31v05", "h32v05"], destination=tmp_path  # type: ignore[arg-type]
    )

    assert [s["task_name"] for s in client.submitted] == ["mcd43a4_2024150_h31v05", "mcd43a4_2024150_h32v05"]
    assert len(client.submitted[0]["layers"]) == 14  # 7 reflectance + 7 quality bands
    assert outputs["h31v05"] == [tmp_path / "2024150" / "h31v05" / "a.tif", tmp_path / "2024150" / "h31v05" / "f2"]


def test_bundle_records_without_a_file_id_are_skipped(tmp_path: Path) -> None:
    client = ScriptedClient({}, [{"file_name": "README.md"}, {"file_id": "f1", "file_name": "a.tif"}])
    download_mcd43a4_tiles(client, date_value=date(2024, 5, 29), tiles=["h31v05"], destination=tmp_path)  # type: ignore[arg-type]
    assert [d[1] for d in client.downloaded] == ["f1"]


def test_failed_tasks_are_reported_by_tile(tmp_path: Path) -> None:
    client = ScriptedClient({"h32v05": False, "h31v05": True}, [])
    with pytest.raises(AppEEARSDownloadError, match="h32v05"):
        download_viirs_corrected_reflectance(
            client, date_value=date(2024, 5, 29), tiles=["h31v05", "h32v05"], destination=tmp_path  # type: ignore[arg-type]
        )


def test_viirs_task_names_and_layers(tmp_path: Path) -> None:
    client = ScriptedClient({}, [])
    download_viirs_corrected_reflectance(
        client, date_value=date(2024, 1, 5), tiles=["h31v05"], destination=tmp_path, product="VNP09GA.001"  # type: ignore[arg-type]
    )
    assert client.submitted[0]["task_name"] == "viirs_2024005_h31v05"
    assert client.submitted[0]["product"] == "VNP09GA.001"
    assert len(client.submitted[0]["layers"]) == 6


@pytest.mark.parametrize(
    ("product", "count"), [("VNP09GA.002", 3), ("VNP09GA.003", 3), ("VNP09GA.001", 6), (None, 6)]
)
def test_default_viirs_layers(product, count: int) -> None:
    assert len(_default_viirs_layers(product)) == count


def test_modis_tile_polygon_corners() -> None:
    feature = modis_tile_polygon("h18v04")
    ring = feature["geometry"]["coordinates"][0]  # type: ignore[index]
    assert ring[0] == ring[-1] and len(ring) == 5
    (lon0, lat0), (lon1, _), (_, lat1) = ring[0], ring[1], ring[2]
    assert lat0 == pytest.approx(50.0, abs=1e-6)  # 5 tiles of 10 degrees north of the equator
    assert lat1 == pytest.approx(40.0, abs=1e-6)
    assert lon0 == pytest.approx(0.0, abs=1e-9)
    assert lon1 == pytest.approx(10.0 / math.cos(math.radians(50.0)), abs=1e-6)  # sinusoidal: wider at higher lat


@pytest.mark.parametrize("tile", ["h1v1", "x18v04", "h18x04", "h36v00", "h00v18"])
def test_modis_tile_polygon_rejects_bad_ids(tile: str) -> None:
    with pytest.raises(ValueError):
        modis_tile_polygon(tile)

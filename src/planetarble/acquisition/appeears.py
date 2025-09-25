"""AppEEARS integration helpers for MODIS acquisitions."""

from __future__ import annotations

import math
import os
import time
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import requests

__all__ = [
    "AppEEARSClient",
    "AppEEARSDownloadError",
    "AppEEARSAuthError",
    "download_mcd43a4_tiles",
    "download_viirs_corrected_reflectance",
    "modis_tile_polygon",
]

API_ROOT = "https://appeears.earthdatacloud.nasa.gov/api/"
EARTH_RADIUS = 6_371_007.181
TILE_SIZE = 1_111_950.5196666666
H_TILES = 36
V_TILES = 18


class AppEEARSAuthError(RuntimeError):
    """Raised when authentication with AppEEARS fails."""


class AppEEARSDownloadError(RuntimeError):
    """Raised when a requested AppEEARS task cannot be completed."""


class AppEEARSClient:
    """Thin client for the AppEEARS REST API."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        *,
        authorization: Optional[str] = None,
        poll_interval: int = 60,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._username = username
        self._password = password
        self._authorization = authorization
        self._poll_interval = poll_interval
        self._session = session or requests.Session()
        self._token: Optional[str] = None
        if self._authorization:
            self._session.headers.update({"Authorization": self._authorization})

    @classmethod
    def from_env(
        cls,
        *,
        username_var: str = "EARTHDATA_USERNAME",
        password_var: str = "EARTHDATA_PASSWORD",
        authorization_var: str = "APPEEARS_AUTHORIZATION",
        token_var: str = "APPEEARS_TOKEN",
        **kwargs,
    ) -> "AppEEARSClient":
        username = os.getenv(username_var)
        password = os.getenv(password_var)
        if username and password:
            return cls(username=username, password=password, **kwargs)

        authorization = os.getenv(authorization_var)
        token = os.getenv(token_var)
        if not authorization and token:
            authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"

        if authorization:
            return cls(authorization=authorization, **kwargs)

        raise AppEEARSAuthError(
            "AppEEARS credentials not provided; set EARTHDATA_USERNAME/EARTHDATA_PASSWORD or APPEEARS_AUTHORIZATION/APPEEARS_TOKEN."
        )

    def __enter__(self) -> "AppEEARSClient":
        self.login()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.logout()

    def login(self) -> None:
        if self._authorization:
            self._token = self._authorization
            return

        if not self._username or not self._password:
            raise AppEEARSAuthError("Username/password not configured for AppEEARS login")

        headers = {"Content-Length": "0"}
        response = self._session.post(
            f"{API_ROOT}login",
            auth=(self._username, self._password),
            headers=headers,
        )
        if response.status_code != 200:
            raise AppEEARSAuthError(f"AppEEARS login failed: {response.status_code} {response.text}")
        payload = response.json()
        self._token = payload.get("token")
        if not self._token:
            raise AppEEARSAuthError("AppEEARS login did not return an access token")
        self._session.headers.update({"Authorization": f"Bearer {self._token}"})

    def logout(self) -> None:
        if self._authorization:
            self._session.headers.pop("Authorization", None)
            self._token = None
            return

        if not self._token:
            return
        try:
            self._session.post(f"{API_ROOT}logout")
        finally:
            self._session.headers.pop("Authorization", None)
            self._token = None

    def submit_area_task(
        self,
        *,
        task_name: str,
        product: str,
        start_date: date,
        end_date: date,
        polygon: Dict[str, object],
        layers: Iterable[str],
        output_format: str = "geotiff",
        projection: str = "geographic",
    ) -> str:
        payload = {
            "task_type": "area",
            "task_name": task_name,
            "params": {
                "dates": [
                    {
                        "startDate": start_date.strftime("%m-%d-%Y"),
                        "endDate": end_date.strftime("%m-%d-%Y"),
                    }
                ],
                "layers": [{"product": product, "layer": layer} for layer in layers],
                "geo": {
                    "type": "FeatureCollection",
                    "features": [polygon],
                },
                "output": {
                    "format": {"type": output_format},
                    "projection": projection,
                },
            },
        }
        response = self._session.post(f"{API_ROOT}task", json=payload)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                detail = response.json().get("message", "")
            except Exception:  # pragma: no cover - response not json
                detail = response.text
            raise AppEEARSDownloadError(f"Task submission failed ({response.status_code}): {detail}") from exc
        task_id = response.json().get("task_id")
        if not task_id:
            raise AppEEARSDownloadError("AppEEARS task submission response missing task_id")
        return task_id

    def get_task_status(self, task_id: str) -> Dict[str, object]:
        response = self._session.get(f"{API_ROOT}task/{task_id}")
        response.raise_for_status()
        return response.json()

    def wait_for_tasks(self, tasks: Dict[str, str]) -> Dict[str, bool]:
        remaining = dict(tasks)
        results: Dict[str, bool] = {}
        while remaining:
            time.sleep(self._poll_interval)
            finished = []
            for key, task_id in remaining.items():
                info = self.get_task_status(task_id)
                status = info.get("status")
                if status in {"done", "error"}:
                    results[key] = status == "done"
                    finished.append(key)
            for key in finished:
                remaining.pop(key, None)
        return results

    def list_bundle_files(self, task_id: str) -> List[Dict[str, object]]:
        response = self._session.get(f"{API_ROOT}bundle/{task_id}")
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("files", []))

    def download_file(self, task_id: str, file_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._session.get(f"{API_ROOT}bundle/{task_id}/{file_id}", stream=True) as response:
            response.raise_for_status()
            filename = _parse_content_disposition(response.headers.get("Content-Disposition")) or destination.name
            path = destination.parent / filename
            with path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        handle.write(chunk)
        return path


def download_mcd43a4_tiles(
    client: AppEEARSClient,
    *,
    date_value: date,
    tiles: Sequence[str],
    destination: Path,
    layers: Optional[Sequence[str]] = None,
    projection: str = "geographic",
    product: str = "MCD43A4.061",
) -> Dict[str, List[Path]]:
    """Request and download MODIS MCD43A4 tiles for a given date."""

    if layers is None:
        reflectance = [f"Nadir_Reflectance_Band{i}" for i in range(1, 8)]
        quality = [f"BRDF_Albedo_Band_Mandatory_Quality_Band{i}" for i in range(1, 8)]
        layers = (*reflectance, *quality)

    tasks: Dict[str, str] = {}
    doy = date_value.timetuple().tm_yday
    for tile in tiles:
        polygon = modis_tile_polygon(tile)
        task_name = f"mcd43a4_{date_value.year}{doy:03d}_{tile}"
        task_id = client.submit_area_task(
            task_name=task_name,
            product=product,
            start_date=date_value,
            end_date=date_value,
            polygon=polygon,
            layers=layers,
            output_format="geotiff",
            projection=projection,
        )
        tasks[tile] = task_id

    statuses = client.wait_for_tasks(tasks)
    failed = [tile for tile, ok in statuses.items() if not ok]
    if failed:
        raise AppEEARSDownloadError(f"AppEEARS tasks failed: {', '.join(sorted(failed))}")

    outputs: Dict[str, List[Path]] = {}
    for tile, task_id in tasks.items():
        files = client.list_bundle_files(task_id)
        tile_dir = destination / f"{date_value.year}{doy:03d}" / tile
        for record in files:
            file_id = str(record.get("file_id"))
            if not file_id:
                continue
            file_path = client.download_file(task_id, file_id, tile_dir / str(record.get("file_name", file_id)))
            outputs.setdefault(tile, []).append(file_path)
    return outputs


def download_viirs_corrected_reflectance(
    client: AppEEARSClient,
    *,
    date_value: date,
    tiles: Sequence[str],
    destination: Path,
    layers: Optional[Sequence[str]] = None,
    projection: str = "geographic",
    product: str = "VNP09GA.002",
) -> Dict[str, List[Path]]:
    """Request and download VIIRS corrected reflectance tiles for a given date."""

    if layers is None:
        layers = _default_viirs_layers(product)

    tasks: Dict[str, str] = {}
    doy = date_value.timetuple().tm_yday
    for tile in tiles:
        polygon = modis_tile_polygon(tile)
        task_name = f"viirs_{date_value.year}{doy:03d}_{tile}"
        task_id = client.submit_area_task(
            task_name=task_name,
            product=product,
            start_date=date_value,
            end_date=date_value,
            polygon=polygon,
            layers=layers,
            output_format="geotiff",
            projection=projection,
        )
        tasks[tile] = task_id

    statuses = client.wait_for_tasks(tasks)
    failed = [tile for tile, ok in statuses.items() if not ok]
    if failed:
        raise AppEEARSDownloadError(f"AppEEARS tasks failed: {', '.join(sorted(failed))}")

    outputs: Dict[str, List[Path]] = {}
    for tile, task_id in tasks.items():
        files = client.list_bundle_files(task_id)
        tile_dir = destination / f"{date_value.year}{doy:03d}" / tile
        for record in files:
            file_id = str(record.get("file_id"))
            if not file_id:
                continue
            file_path = client.download_file(task_id, file_id, tile_dir / str(record.get("file_name", file_id)))
            outputs.setdefault(tile, []).append(file_path)
    return outputs


def modis_tile_polygon(tile: str) -> Dict[str, object]:
    """Return a GeoJSON feature covering a MODIS tile in WGS84 coordinates."""

    if len(tile) != 6 or tile[0].lower() != "h" or tile[3].lower() != "v":
        raise ValueError(f"Invalid MODIS tile identifier: {tile}")
    h = int(tile[1:3])
    v = int(tile[4:6])
    if not (0 <= h < H_TILES and 0 <= v < V_TILES):
        raise ValueError(f"MODIS tile index out of range: {tile}")

    origin_x = TILE_SIZE * H_TILES / 2.0
    origin_y = TILE_SIZE * V_TILES / 2.0
    x0 = h * TILE_SIZE - origin_x
    y0 = origin_y - v * TILE_SIZE
    x1 = x0 + TILE_SIZE
    y1 = y0 - TILE_SIZE

    corners = [
        (x0, y0),
        (x1, y0),
        (x1, y1),
        (x0, y1),
    ]

    def _sinusoidal_to_lon_lat(x: float, y: float) -> List[float]:
        lat_rad = y / EARTH_RADIUS
        cos_lat = math.cos(lat_rad)
        if abs(cos_lat) < 1e-12:
            cos_lat = 1e-12 if cos_lat >= 0 else -1e-12
        lon_rad = x / (EARTH_RADIUS * cos_lat)
        return [math.degrees(lon_rad), math.degrees(lat_rad)]

    coordinates = [_sinusoidal_to_lon_lat(x, y) for x, y in corners]
    coordinates.append(coordinates[0])

    return {
        "type": "Feature",
        "properties": {"tile": tile},
        "geometry": {
            "type": "Polygon",
            "coordinates": [coordinates],
        },
    }


def _parse_content_disposition(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    parts = [part.strip() for part in header.split(";")]
    for part in parts[1:]:
        if part.startswith("filename="):
            value = part.split("=", 1)[1].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            return value or None
    return None


def _default_viirs_layers(product: Optional[str]) -> Sequence[str]:
    collection = (product or "").strip()
    if collection.endswith(('.002', '.003')):
        return ("SurfReflect_I1_1", "SurfReflect_I2_1", "SurfReflect_I3_1")
    reflectance = ["SurfReflect_I1", "SurfReflect_I2", "SurfReflect_I3"]
    quality = ["SurfReflect_QC_I1", "SurfReflect_QC_I2", "SurfReflect_QC_I3"]
    return (*reflectance, *quality)

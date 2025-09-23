"""Asset catalog loader for dataset acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml


@dataclass(frozen=True)
class AssetRecord:
    """Metadata describing a downloadable asset."""

    asset_id: str
    name: str
    description: str
    urls: List[str]
    destination: Path
    license: str
    attribution: str
    media_type: str
    expected_sha256: Optional[str] = None

    def target_path(self, data_directory: Path) -> Path:
        """Return the resolved path for the asset inside the data directory."""

        return (data_directory / self.destination).resolve()


class AssetCatalog:
    """In-memory representation of the asset catalog."""

    def __init__(self, records: Dict[str, AssetRecord]) -> None:
        self._records = records

    @classmethod
    def from_mapping(cls, mapping: Dict[str, dict]) -> "AssetCatalog":
        records: Dict[str, AssetRecord] = {}
        for asset_id, raw in mapping.items():
            record = AssetRecord(
                asset_id=asset_id,
                name=raw["name"],
                description=raw.get("description", ""),
                urls=list(raw.get("urls", [])),
                destination=Path(raw["destination"]),
                license=raw.get("license", ""),
                attribution=raw.get("attribution", ""),
                media_type=raw.get("media_type", "unknown"),
                expected_sha256=raw.get("checksum"),
            )
            records[asset_id] = record
        return cls(records)

    @classmethod
    def load(cls, path: Path) -> "AssetCatalog":
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assets = payload.get("assets", {})
        if not isinstance(assets, dict):  # pragma: no cover - configuration guard
            raise ValueError("assets catalog must be a mapping")
        return cls.from_mapping(assets)

    @classmethod
    def load_default(cls) -> "AssetCatalog":
        root = Path(__file__).resolve().parents[3]
        default_path = root / "configs" / "base" / "assets.yaml"
        return cls.load(default_path)

    def get(self, asset_id: str) -> AssetRecord:
        return self._records[asset_id]

    def iter_records(self) -> Iterable[AssetRecord]:
        return self._records.values()

    def find_many(self, asset_ids: Iterable[str]) -> List[AssetRecord]:
        return [self.get(asset_id) for asset_id in asset_ids]

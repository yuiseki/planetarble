"""Ocean rendering utilities built on NOAA ETOPO datasets."""

from __future__ import annotations

import importlib.resources as resources
import json
from pathlib import Path
from typing import Dict, List, Sequence

from planetarble.core.models import OceanConfig
from planetarble.logging import get_logger

LOGGER = get_logger(__name__)


class OceanRenderer:
    """Render ocean depth shading and hillshade from ETOPO inputs."""

    def __init__(
        self,
        config: OceanConfig,
        runner,
        *,
        temp_dir: Path,
        output_dir: Path,
    ) -> None:
        self._config = config
        self._runner = runner
        self._temp_dir = temp_dir
        self._output_dir = output_dir
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def render(self, etopo_path: Path) -> Dict[str, Path]:
        ramp_path = self._prepare_ramp_table()
        color_path = self._output_dir / "etopo_depth_color.tif"
        hillshade_path = self._output_dir / "etopo_hillshade.tif"

        self._runner.run(
            [
                "gdaldem",
                "color-relief",
                str(etopo_path),
                str(ramp_path),
                str(color_path),
                "-alpha",
            ],
            description="apply color ramp to ETOPO depths",
        )

        if self._config.apply_hillshade:
            self._runner.run(
                [
                    "gdaldem",
                    "hillshade",
                    str(etopo_path),
                    str(hillshade_path),
                    "-az",
                    f"{self._config.hillshade_azimuth}",
                    "-alt",
                    f"{self._config.hillshade_altitude}",
                    "-compute_edges",
                ],
                description="generate ocean hillshade",
            )
        else:
            hillshade_path = Path()

        return {
            "color": color_path,
            "hillshade": hillshade_path,
        }

    def _prepare_ramp_table(self) -> Path:
        spec = self._config.depth_color_ramp
        entries = _load_depth_ramp(spec)
        ramp_path = self._temp_dir / "etopo_depth_ramp.txt"
        with ramp_path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                depth = float(entry["depth"])
                color = entry.get("color")
                if not isinstance(color, Sequence) or len(color) < 3:
                    raise ValueError("color ramp entries must define RGB colors")
                r, g, b = (int(value) for value in color[:3])
                handle.write(f"{depth} {r} {g} {b}\n")
        LOGGER.debug("prepared depth color ramp", extra={"path": str(ramp_path), "entries": len(entries)})
        return ramp_path


def _load_depth_ramp(spec: str) -> List[Dict[str, object]]:
    if spec.startswith("planetarble:"):
        relative = spec.split(":", 1)[1]
        package_path = resources.files("planetarble.data").joinpath(relative)
        with package_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        with Path(spec).expanduser().resolve().open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("depth color ramp must be a list")
    return data

"""build_zxy must retry serially when gdal raster tile fails (WEBP race).

`gdal raster tile` defaults to -j ALL_CPUS. At high zoom (many tiles) the WEBP
overview generation can hit a read/write race ("file exists but cannot be opened
with WEBP driver"). The previous retry only fired for non-bilinear resampling,
so a bilinear run (the overlay default) had no safety net. Now any failure is
retried once with --num-threads 1 (serial), which removes the race.
"""

from __future__ import annotations

from pathlib import Path

from planetarble.core.models import ProcessingConfig
from planetarble.tiling.manager import TileCommandError
from planetarble.tiling.pmtiles import PmtilesTilingManager


class _FailOnceRunner:
    def __init__(self) -> None:
        self.calls: list = []

    def run(self, command, *, description: str) -> None:  # type: ignore[no-untyped-def]
        self.calls.append((tuple(command), description))
        if len(self.calls) == 1:
            raise TileCommandError("simulated WEBP overview race")


def test_build_zxy_retries_serially_on_failure(tmp_path: Path) -> None:
    mgr = PmtilesTilingManager(
        ProcessingConfig(), temp_dir=tmp_path / "tmp", output_dir=tmp_path / "out", dry_run=False
    )
    mgr._runner = _FailOnceRunner()  # type: ignore[attr-defined]
    source = tmp_path / "in.tif"
    source.touch()

    mgr.build_zxy(
        source, min_zoom=8, max_zoom=14, tile_format="WEBP", quality=85, resampling="bilinear"
    )

    calls = mgr._runner.calls  # type: ignore[attr-defined]
    assert len(calls) == 2, "a failed tile run must be retried once"
    retry_cmd = calls[1][0]
    assert "--num-threads" in retry_cmd
    assert retry_cmd[retry_cmd.index("--num-threads") + 1] == "1"

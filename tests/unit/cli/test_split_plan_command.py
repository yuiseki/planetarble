import importlib
import json
from pathlib import Path

cli_main = importlib.import_module("planetarble.cli.main")

from planetarble.acquisition.miniplanets import tile_to_miniplanet_id


def _write_min_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "\n".join(
            [
                f"data_dir: {tmp_path / 'data'}",
                "temp_dir: tmp",
                "output_dir: output",
                "processing:",
                "  tile_source: hls",
                "hls:",
                "  target_zoom: 10",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_split_plan_cli_writes_shards(tmp_path: Path, capsys) -> None:
    config_path = _write_min_config(tmp_path)
    plan_path = tmp_path / "global_plan.ndjson"
    tiles = [(10, 909, 403), (10, 160, 395)]
    with plan_path.open("w", encoding="utf-8") as handle:
        for (z, x, y) in tiles:
            handle.write(
                json.dumps(
                    {
                        "z": z,
                        "x": x,
                        "y": y,
                        "bbox": [0, 0, 1, 1],
                        "start_date": "2024-04-01",
                        "end_date": "2024-10-31",
                        "miniplanet": tile_to_miniplanet_id(z, x, y),
                    },
                    sort_keys=True,
                )
            )
            handle.write("\n")

    out_dir = tmp_path / "shards"
    exit_code = cli_main.main(
        [
            "split-plan",
            "--config",
            str(config_path),
            "--plan",
            str(plan_path),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    expected_keys = {tile_to_miniplanet_id(*t) for t in tiles}
    written = {p.stem.rsplit("_", 1)[-1] for p in out_dir.glob("*.ndjson")}
    assert written == expected_keys

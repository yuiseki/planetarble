from pathlib import Path

from planetarble.config import load_config


def test_load_config_parses_sentinel2_plan_regions(tmp_path: Path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data_dir: data",
                "temp_dir: tmp",
                "output_dir: output",
                "processing:",
                "  tile_source: sentinel2",
                "sentinel2:",
                "  plan_region: tokyo_pref",
                "  plan_regions:",
                "    - name: tokyo_pref",
                "      natural_earth:",
                "        dataset: admin_1",
                "        where: \"adm0_a3='JPN' AND name='Tokyo'\"",
                "      land_only: true",
                "    - name: kanto_bbox",
                "      bbox: [138.0, 34.0, 140.0, 36.0]",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.sentinel2.plan_region == "tokyo_pref"
    assert len(cfg.sentinel2.plan_regions) == 2
    region = cfg.sentinel2.plan_regions[0]
    assert region.name == "tokyo_pref"
    assert region.land_only is True
    assert region.natural_earth is not None
    assert region.natural_earth.dataset == "admin_1"
    assert "Tokyo" in region.natural_earth.where
    bbox_region = cfg.sentinel2.plan_regions[1]
    assert bbox_region.bbox == (138.0, 34.0, 140.0, 36.0)

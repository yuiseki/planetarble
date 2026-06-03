from pathlib import Path

from planetarble.config import load_config


def test_load_config_parses_hls_plan_regions(tmp_path: Path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data_dir: data",
                "temp_dir: tmp",
                "output_dir: output",
                "processing:",
                "  tile_source: hls",
                "hls:",
                "  plan_region: tokyo_land",
                "  plan_include_global: true",
                "  plan_regions:",
                "    - name: tokyo_land",
                "      natural_earth:",
                "        dataset: admin_1",
                "        where: \"adm0_a3='JPN' AND name='Tokyo'\"",
                "      land_only: true",
                "    - name: japan_bbox",
                "      bbox: [122.9, 24.2, 153.9, 45.5]",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.hls.plan_region == "tokyo_land"
    assert cfg.hls.plan_include_global is True
    assert len(cfg.hls.plan_regions) == 2
    region = cfg.hls.plan_regions[0]
    assert region.name == "tokyo_land"
    assert region.land_only is True
    assert region.natural_earth is not None
    assert region.natural_earth.dataset == "admin_1"
    assert "Tokyo" in region.natural_earth.where
    bbox_region = cfg.hls.plan_regions[1]
    assert bbox_region.bbox == (122.9, 24.2, 153.9, 45.5)


def test_load_config_parses_hls_miniplanet_plan_regions(tmp_path: Path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data_dir: data",
                "temp_dir: tmp",
                "output_dir: output",
                "processing:",
                "  tile_source: hls",
                "hls:",
                "  plan_regions:",
                "    - name: mp_00",
                "      miniplanet: 0",
                "      land_only: true",
                "    - name: mp_17",
                "      miniplanet: \"17\"",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert len(cfg.hls.plan_regions) == 2
    first = cfg.hls.plan_regions[0]
    assert first.miniplanet == "00"  # integer 0 normalized to zero-padded string
    assert first.land_only is True
    assert cfg.hls.plan_regions[1].miniplanet == "17"

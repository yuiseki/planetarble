"""Command lines for clipping remote COGs to an AOI (ADR 0003 step 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay.cogwarp import Stretch, cog_warp_commands, vsicurl

BBOX = (139.74, 35.66, 139.79, 35.70)


def test_vsicurl() -> None:
    assert vsicurl("https://a/b.tif") == "/vsicurl/https://a/b.tif"
    assert vsicurl("/vsis3/bucket/b.tif") == "/vsis3/bucket/b.tif"
    assert vsicurl("/data/b.tif") == "/data/b.tif"


def test_8bit_rgb_single_warp(tmp_path: Path) -> None:
    out = tmp_path / "aoi.tif"
    (warp,) = cog_warp_commands(["https://x/a.tif", "https://x/b.tif?sig=1"], aoi_bbox=BBOX, output_path=out)
    assert warp[0] == "gdalwarp"
    assert warp[-3:] == ["/vsicurl/https://x/a.tif", "/vsicurl/https://x/b.tif?sig=1", str(out)]
    assert warp[warp.index("-te") + 1 : warp.index("-te") + 5] == ["139.74", "35.66", "139.79", "35.7"]
    assert [warp[i + 1] for i, a in enumerate(warp) if a == "-srcband"] == ["1", "2", "3"]
    assert {"-dstalpha", "-overwrite"} <= set(warp)
    assert warp[warp.index("-t_srs") + 1] == "EPSG:3857"
    assert "-srcnodata" not in warp


def test_16bit_source_is_stretched_to_byte(tmp_path: Path) -> None:
    out = tmp_path / "aoi.tif"
    warp, translate = cog_warp_commands(
        ["https://x/a.tif"], aoi_bbox=BBOX, output_path=out, stretch=Stretch(0, 2000, 0.7), src_nodata=0
    )
    assert warp[-1] == str(tmp_path / "aoi.warp.tif")
    assert warp[warp.index("-srcnodata") + 1] == "0"
    assert translate[:3] == ["gdal_translate", "-ot", "Byte"]
    for band in (1, 2, 3):
        i = translate.index(f"-scale_{band}")
        assert translate[i + 1 : i + 5] == ["0", "2000", "0", "255"]
        assert translate[translate.index(f"-exponent_{band}") + 1] == "0.7"
    i = translate.index("-scale_4")
    assert translate[i + 1 : i + 5] == ["0", "65535", "0", "255"]
    assert translate[-2:] == [str(tmp_path / "aoi.warp.tif"), str(out)]


def test_band_selection(tmp_path: Path) -> None:
    (warp,) = cog_warp_commands(["https://x/a.tif"], aoi_bbox=BBOX, output_path=tmp_path / "o.tif", bands=(3, 2, 1))
    assert [warp[i + 1] for i, a in enumerate(warp) if a == "-srcband"] == ["3", "2", "1"]


@pytest.mark.parametrize(("urls", "bbox"), [([], BBOX), (["https://x/a.tif"], (1.0, 1.0, 1.0, 2.0))])
def test_rejects_empty_input(tmp_path: Path, urls, bbox) -> None:
    with pytest.raises(ValueError):
        cog_warp_commands(urls, aoi_bbox=bbox, output_path=tmp_path / "o.tif")

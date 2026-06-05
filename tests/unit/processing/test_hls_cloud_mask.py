"""Tests for HLS Fmask cloud masking helpers."""

from __future__ import annotations

import pytest

from planetarble.processing.manager import _mask_band_command, _qa_mask_value


def test_qa_mask_value_combines_bits() -> None:
    # HLS Fmask bits: cirrus=1, cloud=2, adjacent_cloud=4, cloud_shadow=8, snow=16
    assert _qa_mask_value(("cloud", "cloud_shadow", "snow")) == 2 + 8 + 16
    assert _qa_mask_value(("cloud",)) == 2
    assert _qa_mask_value(()) == 0
    # unknown flags are ignored
    assert _qa_mask_value(("cloud", "bogus")) == 2


def test_mask_band_command_uses_bitwise_and_and_nodata(tmp_path) -> None:
    band = tmp_path / "B04.tif"
    fmask = tmp_path / "Fmask.tif"
    out = tmp_path / "B04_masked.tif"
    cmd = _mask_band_command(band, fmask, out, mask_value=26, gdal_calc="gdal_calc.py")

    assert cmd[0] == "gdal_calc.py"
    joined = " ".join(cmd)
    assert f"-A {band}" in joined
    assert f"-B {fmask}" in joined
    # keep band value only where (Fmask & 26) == 0, else nodata 0
    assert "26" in joined
    assert "bitwise_and" in joined
    assert "--NoDataValue=0" in cmd
    assert str(out) in cmd


def test_mask_band_command_zero_mask_is_identity_passthrough(tmp_path) -> None:
    # with mask_value 0 there is nothing to mask; callers should skip, but the
    # command must still be well formed if built.
    cmd = _mask_band_command(tmp_path / "b.tif", tmp_path / "f.tif", tmp_path / "o.tif", mask_value=0)
    assert cmd[0] == "gdal_calc.py"

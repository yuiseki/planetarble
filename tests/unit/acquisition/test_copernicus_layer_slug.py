"""One slug for a Copernicus layer across acquisition, processing and the CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.acquisition.copernicus import copernicus_layer_slug
from planetarble.cli.checks import _resolve_copernicus_cog
from planetarble.core.models import CopernicusLayerConfig


@pytest.mark.parametrize(
    ("layer", "slug"),
    [
        (CopernicusLayerConfig(name="TRUE_COLOR"), "true_color"),
        (CopernicusLayerConfig(name="TRUE COLOR / 2024"), "true_color_2024"),
        (CopernicusLayerConfig(name="X", output="Tokyo-Bay"), "tokyo_bay"),
        (CopernicusLayerConfig(name="!!!"), "layer"),
    ],
)
def test_slug(layer: CopernicusLayerConfig, slug: str) -> None:
    assert copernicus_layer_slug(layer) == slug


def test_cli_finds_the_cog_processing_writes(tmp_path: Path) -> None:
    layers = (CopernicusLayerConfig(name="Other"), CopernicusLayerConfig(name="TRUE COLOR"))
    cog = tmp_path / f"copernicus_{copernicus_layer_slug(layers[1])}_cog.tif"
    cog.write_bytes(b"x")
    stray = tmp_path / "copernicus_aaa_cog.tif"
    stray.write_bytes(b"x")
    # configured layers first (in config order), then any other COGs
    assert _resolve_copernicus_cog(tmp_path, layers) == [cog, stray]

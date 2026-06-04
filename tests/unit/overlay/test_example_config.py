"""The shipped overlay example must parse and validate cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay import parse_pipeline_spec, validate_pipeline_spec

yaml = pytest.importorskip("yaml")

EXAMPLE = Path(__file__).resolve().parents[3] / "configs" / "overlays" / "disaster-example.yaml"


def test_disaster_example_parses_and_validates() -> None:
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    spec = parse_pipeline_spec(data)

    assert spec.base.source == "bmng"
    assert [o.source for o in spec.overlays] == ["hls", "openaerialmap"]
    assert validate_pipeline_spec(spec) == []

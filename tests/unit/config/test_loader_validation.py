"""Configuration mistakes must fail at load time with a clear message.

Pipeline runs take hours, so a misspelt section silently falling back to
defaults, or a wrong-typed value that only explodes deep inside processing,
is expensive. The shipped configs must keep loading unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.config import load_config

REPO = Path(__file__).resolve().parents[3]
SHIPPED = sorted([REPO / "configs" / "base" / "pipeline.yaml", *(REPO / "configs" / "profiles").glob("*.yaml")])


def _load(tmp_path: Path, text: str):
    path = tmp_path / "pipeline.yaml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_shipped_configs_load(path: Path) -> None:
    assert load_config(path).processing.max_zoom >= 0


def test_unknown_top_level_section_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"unknown top-level key\(s\): proccessing"):
        _load(tmp_path, "proccessing:\n  max_zoom: 12\n")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("processing:\n  max_zom: 12\n", r"processing: unknown key\(s\): max_zom"),
        ("hls:\n  enabld: false\n", r"hls: unknown key\(s\): enabld"),
        ("ocean:\n  enable: false\n", r"ocean: unknown key\(s\): enable"),
        ("gsi_orthophotos:\n  zoom_level: 16\n", r"gsi_orthophotos: unknown key\(s\): zoom_level"),
    ],
)
def test_unknown_key_names_the_section(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("processing:\n  max_zoom: twelve\n", r"processing\.max_zoom must be int, got 'twelve'"),
        ("processing:\n  tile_format: 5\n", r"processing\.tile_format must be str, got 5"),
        ("hls:\n  enabled: 'no'\n", r"hls\.enabled must be bool, got 'no'"),
        ("processing:\n  min_zoom: true\n", r"processing\.min_zoom must be int, got True"),
    ],
)
def test_wrong_scalar_type_is_rejected(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, text)


def test_int_is_accepted_for_float_and_none_for_optional(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "hls:\n  max_cloud: 50\n  plan_region: null\n")
    assert cfg.hls.max_cloud == 50
    assert cfg.hls.plan_region is None


def test_inert_keys_load_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="planetarble.config.loader"):
        cfg = _load(tmp_path, "processing:\n  color_enhancement: 1.2\nocean:\n  tone_mapping: flat\n")
    assert cfg.processing.color_enhancement == 1.2
    messages = [r.getMessage() for r in caplog.records]
    assert any("processing.color_enhancement has no effect" in m for m in messages)
    assert any("ocean.tone_mapping has no effect" in m for m in messages)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_shipped_configs_set_no_inert_keys(path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="planetarble.config.loader"):
        load_config(path)
    assert [r.getMessage() for r in caplog.records] == []

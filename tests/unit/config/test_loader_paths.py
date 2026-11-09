"""Where data_dir, temp_dir and output_dir point.

Relative directories resolve against the config file's own directory, not the
current working directory, so a profile run from anywhere writes beside
itself. The README's quickstart copies a profile into the workspace for this
reason.
"""

from __future__ import annotations

from pathlib import Path

from planetarble.config import load_config


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_relative_directories_resolve_beside_the_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _write(tmp_path / "profiles" / "p.yaml", "data_dir: data\ntemp_dir: scratch\noutput_dir: out\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    cfg = load_config(config)

    assert cfg.data_dir == config.parent / "data"
    assert cfg.temp_dir == config.parent / "scratch"
    assert cfg.output_dir == config.parent / "out"


def test_omitted_directories_also_resolve_beside_the_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _write(tmp_path / "ws" / "p.yaml", "processing:\n  max_zoom: 8\n")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(config)
    assert (cfg.data_dir, cfg.temp_dir, cfg.output_dir) == (
        config.parent / "data", config.parent / "tmp", config.parent / "output")


def test_absolute_directories_are_kept(tmp_path: Path) -> None:
    target = tmp_path / "big_disk" / "output"
    config = _write(tmp_path / "ws" / "p.yaml", f"output_dir: {target}\n")
    assert load_config(config).output_dir == target


def test_a_relative_config_path_is_found_from_the_current_directory(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path / "ws" / "p.yaml", "output_dir: output\n")
    monkeypatch.chdir(tmp_path)
    assert load_config("ws/p.yaml").output_dir == (tmp_path / "ws" / "output").resolve()

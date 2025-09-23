# Planetarble

Planetarble builds a fully open global raster basemap and packages it as a single PMTiles archive for offline distribution. The project orchestrates three core phases:

1. **Acquire** the required datasets (NASA BMNG 2004, GEBCO 2024 Global Grid, Natural Earth 10 m layers) with integrity checks.
2. **Process** the rasters into a blended Web Mercator tile pyramid covering zoom levels 0–10.
3. **Package** the output as `world_YYYY.pmtiles` with companion metadata and licensing bundles.

The Earth is famously seen as a "blue marble", a description inspired by the 1972 photograph taken by the Apollo 17 crew that revealed our planet as a delicate swirl of blues and whites. Planetarble carries forward that legacy by relying on NASA’s Blue Marble Next Generation imagery. It continues the tradition of sharing a whole-Earth view constructed entirely from open data.

## Quickstart

```bash
# Install in editable mode (requires Python 3.10+)
pip install -e .

# Fallback when a global install is not possible (relies on current repo checkout)
PYTHONPATH=src python -m planetarble.cli.main --help

# Download source datasets and emit MANIFEST.json into the output directory
planetarble acquire --config configs/base/pipeline.yaml

# or, without installing the package system-wide
PYTHONPATH=src python -m planetarble.cli.main acquire --config configs/base/pipeline.yaml

# resume-friendly downloads are enabled by default when aria2c is in PATH
# disable aria2c only if required
planetarble acquire --config configs/base/pipeline.yaml --no-aria2
```

The default configuration stores raw data in `data/`, temporary artifacts in `tmp/`, and final outputs in `output/`. Adjust paths and parameters by copying `configs/base/pipeline.yaml` and editing as needed. Expect roughly 4.5 GB of downloads on the first run (BMNG 500 m panels, GEBCO netCDF, Natural Earth archives); on an 80 Mbps connection the acquisition step typically completes in about 10 minutes.

## Caching & Re-download Policy

- Each asset is downloaded to a deterministic location under `data/`. On repeated runs, Planetarble reuses the existing file after validating its SHA256 hash.
- To force a fresh download (for example, if a file was truncated or updated upstream), pass `--force` to the `planetarble acquire` command (or the `python -m ... acquire` fallback). This flag flows through to the downloader and overwrites local copies.
- The manifest records the exact URLs, file sizes, and hashes that were used; verify integrity later with `planetarble`'s `verify_checksums` helper once the processing pipeline is complete.
- When `aria2c` is available it is used automatically to provide resumable downloads. If the binary is not found the CLI falls back to Python's built-in downloader; you can also disable it explicitly with `--no-aria2` if needed.
- Large transfers (several gigabytes) are long-running—consider wrapping the command in `screen` or `tmux` so the process survives SSH disconnects.

## Roadmap

- Implement the preprocessing pipeline (`ProcessingManager`) to normalize BMNG imagery, generate GEBCO hillshade, unpack Natural Earth masks, and convert merged rasters to Cloud Optimized GeoTIFFs.
- Add commands for tiling, PMTiles conversion, and output verification.

## Requirements

- GDAL ≥ 3.x and the PMTiles CLI must be installed locally for processing steps.
- Python dependencies are recorded in `pyproject.toml` (PyYAML is required for configuration loading).

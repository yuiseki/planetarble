# Planetarble

Planetarble builds a fully open global raster basemap and packages it as a single PMTiles archive for offline distribution. The project orchestrates three core phases:

1. **Acquire** the required datasets (NASA BMNG 2004, GEBCO Global Grid, Natural Earth 10 m layers) with integrity checks.
2. **Process** the rasters into a blended Web Mercator tile pyramid covering zoom levels 0–10.
3. **Package** the output as `world_YYYY.pmtiles` with companion metadata and licensing bundles.

The Earth is famously seen as a "blue marble"—a description inspired by the Apollo 17 crew’s 1972 photograph that revealed our planet as a delicate swirl of blues and whites. Planetarble embraces that heritage by relying on NASA’s Blue Marble Next Generation imagery, continuing the tradition of sharing a whole-Earth view built entirely from open data.

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
```

The default configuration stores raw data in `data/`, temporary artifacts in `tmp/`, and final outputs in `output/`. Adjust paths and parameters by copying `configs/base/pipeline.yaml` and editing as needed. Expect roughly 4.5 GB of downloads on the first run (BMNG 500 m panels, GEBCO netCDF, Natural Earth archives); on an 80 Mbps connection the acquisition step typically completes in about 10 minutes.

## Caching & Re-download Policy

- Each asset is downloaded to a deterministic location under `data/`. On repeated runs, Planetarble reuses the existing file after validating its SHA256 hash.
- To force a fresh download (for example, if a file was truncated or updated upstream), pass `--force` to the `planetarble acquire` command (or the `python -m ... acquire` fallback). This flag flows through to the downloader and overwrites local copies.
- The manifest records the exact URLs, file sizes, and hashes that were used; verify integrity later with `planetarble`'s `verify_checksums` helper once the processing pipeline is complete.

## Roadmap

- Implement the preprocessing pipeline (`ProcessingManager`) to normalize BMNG imagery, generate GEBCO hillshade, unpack Natural Earth masks, and convert merged rasters to Cloud Optimized GeoTIFFs.
- Add commands for tiling, PMTiles conversion, and output verification.

## Requirements

- GDAL ≥ 3.x and the PMTiles CLI must be installed locally for processing steps.
- Python dependencies are recorded in `pyproject.toml` (PyYAML is required for configuration loading).

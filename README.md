# Planetarble

[![Image from Gyazo](https://i.gyazo.com/aefeffdeb3c3575ff02037a8509c4d7c.png)](https://pmtiles.io/#url=https%3A%2F%2Fz.yuiseki.net%2Fstatic%2Fplanetarble%2Fplanet.pmtiles&map=1.88/0/0)

Planetarble builds a fully open global raster basemap and packages it as a single PMTiles archive for offline distribution.

The project orchestrates three core phases:

1. **Acquire** the required datasets (NASA BMNG 2004, GEBCO 2024 Global Grid, Natural Earth 10 m layers, and optional MODIS/VIIRS surface reflectance tiles) with integrity checks.
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

# preprocess rasters (mosaic BMNG, hillshade GEBCO, unpack Natural Earth)
planetarble process --config configs/base/pipeline.yaml

# preview commands without executing
planetarble process --config configs/base/pipeline.yaml --dry-run

# generate MBTiles pyramid (requires gdal_translate/gdaladdo)
planetarble tile --config configs/base/pipeline.yaml

# convert to PMTiles and assemble distribution bundle (requires pmtiles CLI)
planetarble package --config configs/base/pipeline.yaml

# increase JPEG quality or switch to WebP when experimenting
planetarble tile --config configs/base/pipeline.yaml --quality 95 --tile-format WEBP

# inspect available Copernicus (Sentinel-2) WMS layers
planetarble copernicus-layers
```

The default configuration stores raw data in `data/`, temporary artifacts in `tmp/`, and final outputs in `output/`. Adjust paths and parameters by copying `configs/base/pipeline.yaml` and editing as needed. Expect roughly 4.5 GB of downloads on the first run (BMNG 500 m panels, GEBCO netCDF, Natural Earth archives); on an 80 Mbps connection the acquisition step typically completes in about 10 minutes.

## Imagery Options

- Planetarble ships with NASA Blue Marble Next Generation (BMNG) as the default imagery. The processing stage produces a normalized Cloud Optimized GeoTIFF under `output/processing/*_normalized_cog.tif` and the tiling stage uses it when `processing.tile_source` is left at `bmng`.
- MODIS MCD43A4 surface reflectance can be enabled by setting `processing.modis_enabled: true`, providing a day-of-year (`processing.modis_doy`) and listing desired sinusoidal tiles (`processing.modis_tiles`). The acquisition command requests the corresponding assets via NASA AppEEARS. Switch the final tile source by setting `processing.tile_source: modis` in your configuration.
- VIIRS corrected reflectance (VNP09GA.002) is now supported. Set `processing.viirs_enabled: true`, choose the acquisition date (`processing.viirs_date` in `YYYYJJJ` format), and list the tiles in `processing.viirs_tiles`. Select `processing.tile_source: viirs` to build the MBTiles/PMTiles pyramid from the VIIRS COG. Adjust `processing.viirs_product` if you need to target another collection (e.g., `VJ109GA.002` for NOAA-20); Planetarble automatically requests the correct Collection 2 layer names.
- Sentinel-2 L2A imagery from the Copernicus Data Space Ecosystem can be downloaded by enabling the `copernicus` block in `configs/base/pipeline.yaml`. The default configuration targets Japan (`bbox: [123, 24, 147, 46]`) for zoom levels `8–12` and fetches the `TRUE_COLOR` and `VEGETATION_INDEX` layers. Adjust `copernicus.layers`, `bbox`, `min_zoom`, `max_zoom`, or `max_tiles_per_layer` to control coverage and cost. Make sure `COPERNICUS_INSTANCE_ID`, `COPERNICUS_CLIENT_ID`, and `COPERNICUS_CLIENT_SECRET` are defined in `.env` before running `planetarble acquire`.
- When Copernicus throttles access, run `planetarble mpc-fetch` to anonymously clip a high-resolution Sentinel-2 True Color chip (10 m) from Microsoft Planetary Computer without downloading a full tile. Example:

  ```bash
  planetarble mpc-fetch \
    --lat 35.6839 \
    --lon 139.7021 \
    --width-m 600 \
    --height-m 600 \
    --max-cloud 10 \
    --output output/processing/mpc_yoyogi_true_color.tif
  ```

  The command queries the MPC STAC API for a low-cloud Sentinel-2 L2A scene, signs the `visual` COG asset with an anonymous SAS token, and calls `gdal_translate` with `-projwin` so only the requested footprint is streamed from storage.
- For sub-meter coverage in Japan, `planetarble gsi-fetch --lat 35.6839 --lon 139.7021 --width-m 300 --height-m 300 --zoom 18 --output output/processing/gsi_yoyogi_ortho.tif` streams only the requested area from 国土地理院の航空写真（デフォルトで `https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg` を使用）して COG 化します。ズーム 19 以上が必要な場合は `--tile-template` で別レイヤーを指定してください。配布時は必ず国土地理院の出典表記を添えてください。
- Both MODIS and VIIRS downloads require AppEEARS credentials. Export `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD`, or provide an `APPEEARS_TOKEN`, before running `planetarble acquire` so the CLI can authenticate with the service.
- You can enable multiple imagery sources simultaneously; each processed raster is preserved under `output/processing/`. Switching `processing.tile_source` lets you compare BMNG, MODIS, and VIIRS outputs without re-running the acquisition step.

## Quality Tuning

- `processing.tile_quality` defaults to 95 in `configs/base/pipeline.yaml`; raise or lower this value to trade file size for fidelity.
- You can override quality and format per run: `planetarble tile --quality 95 --tile-format WEBP` regenerates MBTiles with WebP tiles at quality 95.
- After changing quality-related settings, rerun `planetarble tile` and `planetarble package` (and `planetarble process` if upstream rasters changed) to rebuild artifacts.

## Caching & Re-download Policy

- Each asset is downloaded to a deterministic location under `data/`. On repeated runs, Planetarble reuses the existing file after validating its SHA256 hash.
- To force a fresh download (for example, if a file was truncated or updated upstream), pass `--force` to the `planetarble acquire` command (or the `python -m ... acquire` fallback). This flag flows through to the downloader and overwrites local copies.
- The manifest records the exact URLs, file sizes, and hashes that were used; verify integrity later with `planetarble`'s `verify_checksums` helper once the processing pipeline is complete.
- When `aria2c` is available it is used automatically to provide resumable downloads. If the binary is not found the CLI falls back to Python's built-in downloader; you can also disable it explicitly with `--no-aria2` if needed.
- Large transfers (several gigabytes) are long-running—consider wrapping the command in `screen` or `tmux` so the process survives SSH disconnects.

## Roadmap

- Support higher-resolution outputs across the entire basemap without compromising reproducibility.
- Integrate Sentinel-2 acquisitions via Copernicus services to unlock higher zoom levels where source data allows.
- Offer selective high-zoom coverage so priority regions can receive detailed tiles while keeping the global bundle lean.
- Provide region-scoped refresh workflows that update only the areas requiring newer imagery.
- Expand quality assurance with automated visual diffs and `pmtiles verify` integration once long-running workflow orchestration is in place.

## Requirements

- GDAL ≥ 3.x must be installed locally to run the processing and tiling commands (`gdalbuildvrt`, `gdal_translate`, `gdaldem`, `gdalwarp`, `gdaladdo`).
- The PMTiles CLI (`pmtiles convert`) is required to produce the final `world_YYYY.pmtiles` artifact.
- `aria2c` is expected for the default acquisition workflow so downloads can resume cleanly; the CLI falls back to Python’s downloader if aria2c is missing, but installing it avoids broken transfers.
- Python dependencies are recorded in `pyproject.toml` (PyYAML is required for configuration loading).

# Planetarble

[![Image from Gyazo](https://i.gyazo.com/aefeffdeb3c3575ff02037a8509c4d7c.png)](https://pmtiles.io/#url=https%3A%2F%2Fz.yuiseki.net%2Fstatic%2Fplanetarble%2Fplanet.pmtiles&map=1.88/0/0)

Planetarble builds a fully open global raster basemap anchored on the NASA/USGS Harmonized Landsat and Sentinel-2 (HLS) v2 archive streamed directly from Microsoft Planetary Computer. Persistent cloud gaps are backfilled with Landsat Collection 2 Level-2 surface reflectance, and oceans are rendered with NOAA’s CC0 ETOPO 2022 bathymetry and hillshade. The entire stack remains effectively license-free (NASA/USGS public domain + NOAA CC0) and is distributed as a single PMTiles artifact for offline use.

When HLS mode is enabled—the new default configuration—the pipeline orchestrates three core phases:

1. **Acquire** — build an `hls_z{zoom}_plan.ndjson` that enumerates every land ZL10 tile, its seasonal window, and fallback collections, without downloading multi-terabyte imagery up front.
2. **Process** — resolve the plan into a deduplicated, SAS-signed HLS scene manifest and pre-render NOAA ETOPO ocean shading ready for compositing.
3. **Package** — convert the generated MBTiles into a PMTiles bundle (recommended credits are documented separately).

The legacy BMNG/GEBCO/Natural Earth workflow remains available by switching `processing.tile_source` back to `bmng`.

## Try it in five minutes

Build a small but complete global basemap PMTiles from NASA Blue Marble (2 km). No terabytes involved; the whole run takes well under a minute once the image is built.

```bash
docker build -t planetarble .
mkdir -p workspace
docker run --rm -v "$PWD/workspace:/workspace" --entrypoint bash planetarble -c '
  curl -sL -o bmng.jpg "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73776/world.topo.bathy.200408.3x5400x2700.jpg" &&
  gdal_translate -q -a_srs EPSG:4326 -a_ullr -180 90 180 -90 bmng.jpg bmng.tif &&
  planetarble tiling pmtiles --input bmng.tif --out . --min-zoom 0 --max-zoom 5 --format webp --bounds-mode global'
```

This writes `workspace/bmng_0-5.pmtiles` (about 4 MB, zoom 0-5, the full globe). Drop the file onto <https://pmtiles.io> to view it. The same three commands work without Docker if you have the toolchain from [Requirements](#requirements) installed.

Where to go next:

- `configs/profiles/` contains ready-made recipes for the real pipelines (global BMNG at z8, regional HLS at z11, Sentinel-2 at z14); each file's header documents the exact commands.
- `configs/overlays/` contains AOI overlay specs for `planetarble build` (see [Custom AOI planets](#custom-aoi-planets)).
- If you just want a planet file without building anything, see [Prebuilt artifacts](#prebuilt-artifacts).

## Custom AOI planets

A global floor everyone can afford, plus high-resolution overlays only where you care, declared in one file and merged into a single planet. This is the [AOI overlay architecture](docs/adr/0001-aoi-overlay-architecture.md): a `base` source for global coverage, plus an ordered list of `overlays`, each pairing an area of interest with a source and zoom range. At every zoom the finest source is composited on top and lower sources fill underneath, so there are no holes.

```bash
# build the planet described by an overlay spec onto a prebuilt global floor
planetarble build \
  --spec configs/overlays/atami-example.yaml \
  --base-mbtiles output/tiling/planet_2024_8z.mbtiles
```

`configs/overlays/atami-example.yaml` stacks a global BMNG floor, an HLS context overlay (derived from the target bbox plus a buffer, cloud-masked with Fmask), and an OpenAerialMap orthophoto over the city, producing one PMTiles where zooming in goes BMNG to HLS to OAM. `disaster-example.yaml` shows the disaster-response shape (national HLS context plus a city-scale OAM overlay). Source choice per AOI is open: add a source adapter and any AOI can use it.

## Prebuilt artifacts

A prebuilt global PMTiles produced by this pipeline is available for download and direct use with MapLibre GL + pmtiles.js:

- <https://z.yuiseki.net/static/planetarble/planet.pmtiles> (BMNG-based global basemap; preview it on [pmtiles.io](https://pmtiles.io/#url=https%3A%2F%2Fz.yuiseki.net%2Fstatic%2Fplanetarble%2Fplanet.pmtiles&map=1.88/0/0))

## Run with Docker

The bundled `Dockerfile` packages the complete toolchain (GDAL 3.11 with the unified `gdal` CLI, go-pmtiles, mb-util, aria2) together with the `planetarble` CLI, so none of it needs to be installed on the host.

```bash
docker build -t planetarble .

# data/, tmp/, and output/ resolve relative to /workspace inside the container
docker run --rm -v "$PWD/workspace:/workspace" planetarble --help
docker run --rm -v "$PWD/workspace:/workspace" planetarble acquire --config /opt/planetarble/configs/profiles/bmng-global-z8.yaml
```

Mount a host directory at `/workspace` to keep downloaded data and generated tiles between runs.

## Quickstart

```bash
# Install in editable mode (requires Python 3.10+)
pip install -e .

# Fallback when a global install is not possible (relies on current repo checkout)
PYTHONPATH=src python -m planetarble.cli.main --help

# Generate the HLS land plan (writes data/plans/hls_z10_plan.ndjson)
planetarble acquire --config configs/base/pipeline.yaml

# or, without installing the package system-wide
PYTHONPATH=src python -m planetarble.cli.main acquire --config configs/base/pipeline.yaml

# resume-friendly downloads are enabled by default when aria2c is in PATH
# disable aria2c only if required
planetarble acquire --config configs/base/pipeline.yaml --no-aria2

# build the MPC scene manifest and ocean shading (long-running; streams STAC metadata)
planetarble process --config configs/base/pipeline.yaml

# preview commands without executing
planetarble process --config configs/base/pipeline.yaml --dry-run

# generate MBTiles pyramid once land/ocean rasters are ready (requires gdal_translate/gdaladdo)
planetarble tile --config configs/base/pipeline.yaml

# convert to PMTiles and assemble distribution bundle (requires pmtiles CLI)
planetarble package --config configs/base/pipeline.yaml

# increase WEBP quality (default pipeline uses WEBP Q=82)
planetarble tile --config configs/base/pipeline.yaml --quality 90 --tile-format WEBP

# inspect available Copernicus (Sentinel-2) WMS layers
planetarble copernicus-layers
```

The default configuration keeps plan and manifest artefacts under `data/`, scratch working files in `tmp/`, and final outputs in `output/`. Copy `configs/base/pipeline.yaml` to create your own profile and adjust parameters (seasonal windows, cloud thresholds, ocean options) as needed. Ready-made recipes live under `configs/profiles/`: `bmng-global-z8.yaml` (global BMNG 500m + GEBCO ocean, z0-8), `hls-regional-z11.yaml` (HLS v2 land imagery per prefecture or miniplanet, z11), and `sentinel2-tokyo-z14.yaml` (Sentinel-2 L2A around Tokyo, z14); each file's header documents the exact commands. `planetarble acquire` also pulls down the NOAA ETOPO 2022 15 arc-second bedrock GeoTIFF (≈9 GB compressed) whenever `ocean.enabled` is true so ocean shading can run offline. Streaming the full HLS land archive during processing remains a long-running operation—plan on 1.6–2.0 TB of transfer against Microsoft Planetary Computer for a complete ZL10 build, roughly 60–75 % lower than fetching land + ocean pixels.

## Imagery Options

See [SOURCE.md](SOURCE.md) for the full matrix of supported sources with native resolutions and maximum Web Mercator zoom levels (日本語: [SOURCE.ja.md](SOURCE.ja.md)).

- **HLS v2 (default)** — `processing.tile_source: hls` activates the new global workflow. `planetarble acquire` writes an `hls_z10_plan.ndjson`; `planetarble process` expands it into `output/processing/hls_scene_manifest.json` with MPC-signed URLs for the required `B02/B03/B04` COGs and QA masks. Seasonal windows default to April–October for the northern hemisphere and October–April for the southern hemisphere. Default collections use `hls2-s30` and `hls2-l30`.
- **Landsat Collection 2 Level-2 SR fallback** — listed in `hls.fallback_collections`. When the primary HLS collections cannot clear clouds, the manifest builder records low-cloud Landsat scenes that align with the same tile footprints and QA masks.
- **NOAA ETOPO 2022 ocean rendering** — `ocean.enabled: true` combines the CC0 bathymetry grid with a configurable color ramp and lambertian hillshade. `planetarble acquire` downloads the global 15 arc-second bedrock GeoTIFF to `data/etopo/ETOPO_2022_15s_bed.tif`; you can point `ocean.source_id` at a custom path if you maintain your own copy.
- **Legacy BMNG / MODIS / VIIRS / Copernicus** — set `processing.tile_source` back to `bmng` (and toggle the respective blocks) to reuse the historical workflow that mosaics BMNG, optional MODIS/VIIRS reflectance, and Copernicus WMS tiles. All legacy commands remain available for backwards compatibility and regional experiments.

## Regional HLS Planning

HLS plan generation can be split into deterministic regions so you can make steady progress under rate limits.
Define `hls.plan_regions` in your config and optionally select a single region with `--plan-region`.

Example (Tokyo with land-only filtering):

```yaml
hls:
  plan_regions:
    - name: "tokyo_land"
      natural_earth:
        dataset: "admin_1"
        where: "adm0_a3='JPN' AND name='Tokyo'"
      land_only: true
```

Generate the plan:

```bash
planetarble acquire --config configs/base/pipeline.yaml --plan-region tokyo_land
```

Process the plan:

```bash
planetarble process --config configs/base/pipeline.yaml --plan-region tokyo_land
```

Natural Earth admin boundaries (`ne_10m_admin_0_countries.zip`, `ne_10m_admin_1_states_provinces.zip`) are downloaded on-demand
when a plan region references them.

For a Japan-wide incremental plan and rate-limit guidance, see `docs/operations/hls-japan-plan.md`.

To generate regional HLS tiles and overlay them onto a BMNG basemap:

```bash
# build HLS mosaic (regional) + scene manifest
planetarble process --config configs/base/pipeline.yaml --plan-region tokyo_land

# tile only z11 for HLS (z12 is oversampled at display time)
planetarble tile --config configs/base/pipeline.yaml --plan-region tokyo_land --min-zoom 11 --max-zoom 11

# merge HLS tiles onto the BMNG MBTiles
planetarble tiling merge-mbtiles \
  --base output/tiling/planet_2024_12z.mbtiles \
  --overlay output/tiling/planet_hls_tokyo_land_12z.mbtiles \
  --out output/tiling/planet_2024_tokyo_hls_12z.mbtiles

Note: HLS imagery has ~30 m effective resolution, which maps to about z11 in Web Mercator.
Serving z12 is typically done via client-side overscaling of z11 tiles rather than generating new data.
```

## Quality Tuning

- `processing.tile_quality` defaults to 82 for the HLS workflow; raise or lower this value to trade file size for fidelity.
- Override quality and format per run: `planetarble tile --quality 88 --tile-format WEBP` regenerates MBTiles with WebP tiles at the requested quality.
- After changing quality-related settings, rerun `planetarble tile` and `planetarble package` (and `planetarble process` if upstream rasters changed) to rebuild artifacts.

## Caching & Re-download Policy

- Each asset is downloaded to a deterministic location under `data/`. On repeated runs, Planetarble reuses the existing file after validating its SHA256 hash.
- To force a fresh download (for example, if a file was truncated or updated upstream), pass `--force` to the `planetarble acquire` command (or the `python -m ... acquire` fallback). This flag flows through to the downloader and overwrites local copies.
- The manifest records the exact URLs, file sizes, and hashes that were used; verify integrity later with `planetarble`'s `verify_checksums` helper once the processing pipeline is complete.
- When `aria2c` is available it is used automatically to provide resumable downloads. If the binary is not found the CLI falls back to Python's built-in downloader; you can also disable it explicitly with `--no-aria2` if needed.
- Large transfers (several gigabytes) are long-running—consider wrapping the command in `screen` or `tmux` so the process survives SSH disconnects.

## Roadmap

The global floor plus AOI overlay model is implemented (see [Custom AOI planets](#custom-aoi-planets) and [docs/adr/0001-aoi-overlay-architecture.md](docs/adr/0001-aoi-overlay-architecture.md)): a global BMNG base everyone can afford, plus per-area high-resolution overlays from any source declared in a single config (including OpenAerialMap for disaster response), merged into one custom planet via `planetarble build`. The full max-resolution global build stays supported as a special case.

- Support higher-resolution outputs across the entire basemap without compromising reproducibility.
- Integrate Sentinel-2 acquisitions via Copernicus services to unlock higher zoom levels where source data allows.
- Offer selective high-zoom coverage so priority regions can receive detailed tiles while keeping the global bundle lean.
- Provide region-scoped refresh workflows that update only the areas requiring newer imagery.
- Expand quality assurance with automated visual diffs and `pmtiles verify` integration once long-running workflow orchestration is in place.

## Requirements

The bundled `Dockerfile` provides everything below in one image (see [Run with Docker](#run-with-docker)); install the toolchain manually only if you prefer running on the host.

- GDAL ≥ 3.11 must be installed locally to run the processing and tiling commands (`gdalbuildvrt`, `gdal_translate`, `gdaldem`, `gdalwarp`, `gdaladdo`, and the unified `gdal` CLI used by `gdal raster tile`).
- The go-pmtiles CLI (`pmtiles convert`) is required to produce the final `planet_{YYYY}_{max_zoom_level}z.pmtiles` artifact.
- `mb-util` (`pip install mbutil`) is required to package XYZ tile pyramids into MBTiles.
- `aria2c` is expected for the default acquisition workflow so downloads can resume cleanly; the CLI falls back to Python’s downloader if aria2c is missing, but installing it avoids broken transfers.
- Python dependencies are recorded in `pyproject.toml` (PyYAML is required for configuration loading); regional HLS/Sentinel-2 planning additionally needs the GDAL Python bindings (`osgeo`).

# Planetarble

[![Image from Gyazo](https://i.gyazo.com/aefeffdeb3c3575ff02037a8509c4d7c.png)](https://pmtiles.io/#url=https%3A%2F%2Fz.yuiseki.net%2Fstatic%2Fplanetarble%2Fplanet.pmtiles&map=1.88/0/0)

Planetarble builds a fully open global raster basemap anchored on the NASA/USGS Harmonized Landsat and Sentinel-2 (HLS) v2 archive streamed directly from Microsoft Planetary Computer. Persistent cloud gaps are backfilled with Landsat Collection 2 Level-2 surface reflectance, and oceans are rendered with NOAA’s CC0 ETOPO 2022 bathymetry and hillshade. The entire stack remains effectively license-free (NASA/USGS public domain + NOAA CC0) and is distributed as a single PMTiles artifact for offline use.

With `processing.tile_source: hls` (the built-in default when a config does not set it; see `configs/profiles/hls-regional-z11.yaml`) the pipeline runs four stages:

1. **Acquire** — build an `hls_z{zoom}_plan.ndjson` that enumerates every land ZL10 tile, its seasonal window, and fallback collections, without downloading multi-terabyte imagery up front.
2. **Process** — resolve the plan into a deduplicated, SAS-signed HLS scene manifest, mosaic the scenes, and pre-render NOAA ETOPO ocean shading ready for compositing.
3. **Tile** — cut the mosaic into an MBTiles pyramid.
4. **Package** — convert the MBTiles into a PMTiles bundle with a TileJSON and a credits file.

Other values of `processing.tile_source` select the legacy BMNG/GEBCO/Natural Earth workflow (`bmng`), Sentinel-2 L2A from Microsoft Planetary Computer (`sentinel2`), GSI orthophotos for Japan (`gsi_orthophotos`), or the MODIS, VIIRS and Copernicus WMS experiments (`modis`, `viirs`, `copernicus`).

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

# data/, tmp/ and output/ resolve beside the config file, so keep the config in the mounted workspace
mkdir -p workspace && cp configs/profiles/bmng-global-z8.yaml workspace/
docker run --rm -v "$PWD/workspace:/workspace" planetarble --help
docker run --rm -v "$PWD/workspace:/workspace" planetarble acquire --config /workspace/bmng-global-z8.yaml
```

Mount a host directory at `/workspace` to keep downloaded data and generated tiles between runs. A config under `/opt/planetarble/configs/` would write its outputs inside the container, where they are lost when it exits.

## Local setup (uv)

Python dependencies are managed with [uv](https://docs.astral.sh/uv/) and pinned in `uv.lock`. GDAL's Python bindings (`osgeo`) and numpy come from the OS packages instead, because the bindings must match the system libgdal and `gdal_array` is compiled against the system numpy. Create the venv on the system Python with access to system site-packages:

```bash
sudo apt install gdal-bin python3-gdal   # GDAL CLI + bindings (pulls in python3-numpy)
uv venv --python /usr/bin/python3 --system-site-packages
uv sync                                  # project deps + dev group (pytest)
uv run pytest
uv run planetarble --help
```

Hosts that only collect GSI tiles need neither GDAL nor numpy; plain `uv sync` is enough (see `docs/operations/collection-node.md`), and the GDAL/numpy tests are skipped there.

If a conda environment is active, its `GDAL_DATA`, `GDAL_DRIVER_PATH`, and `PROJ_DATA` point the system GDAL at conda's plugins and data, and GDAL-backed tests fail. Deactivate conda first, or unset them: `env -u GDAL_DATA -u GDAL_DRIVER_PATH -u PROJ_DATA uv run pytest`.

### Development checks

CI runs these on every push and pull request:

```bash
uv run ruff check   # pyflakes, pycodestyle, import order, bugbear
uv run mypy         # gradual type checking of src/
uv run pytest
```

The module layout is described in `docs/architecture.md` and design decisions in `docs/adr/`.

Golden files pin behaviour that refactoring must not change: `tests/unit/cli/golden/parser.json` (every CLI option, default and help text), `tests/unit/processing/golden/` (the GDAL command lines `ProcessingManager` builds) and `tests/unit/tiling/golden/` (the `planetarble tile` command lines). When a change to one of them is intended, regenerate them with `PLANETARBLE_UPDATE_GOLDEN=1 uv run pytest` and review the diff.

Configuration files are validated on load: an unknown section or key, or a value of the wrong type (for example `max_zoom: twelve`), stops with an error that names it.

## Quickstart

The real pipelines run in four stages. This example builds the global BMNG basemap at z0-8 (the prebuilt artifact below); the other recipes under `configs/profiles/` follow the same pattern.

```bash
# Set up the venv as described in "Local setup (uv)", then activate it
source .venv/bin/activate

# data/, tmp/ and output/ resolve relative to the directory that holds the config file
# (not the current directory), so copy the profile into the workspace and run it there
mkdir -p workspace && cp configs/profiles/bmng-global-z8.yaml workspace/ && cd workspace

# download BMNG 500m panels, GEBCO, Natural Earth and (with ocean.enabled) the ETOPO grid,
# and write output/MANIFEST.json
# (aria2c is used for resumable downloads when it is in PATH; --no-aria2 disables it)
planetarble acquire --config bmng-global-z8.yaml

# mosaic, shade and normalise the rasters (--dry-run prints the commands instead)
planetarble process --config bmng-global-z8.yaml

# build output/tiling/planet_2024_8z.mbtiles
planetarble tile --config bmng-global-z8.yaml

# convert to PMTiles and assemble output/distribution/ (PMTiles, TileJSON, <name>.LICENSE_AND_CREDITS.txt)
planetarble package --config bmng-global-z8.yaml

# view it in the bundled MapLibre viewer at http://localhost:8081/viewer/
planetarble serve --pmtiles output/distribution/planet_2024_8z.pmtiles --open
```

`python -m planetarble.cli.main` runs the same CLI without installing the package (`PYTHONPATH=src` from the repository root).

Plan and manifest artefacts live under `data/`, scratch working files in `tmp/`, and final outputs in `output/`. Ready-made recipes live under `configs/profiles/`: `bmng-global-z8.yaml` (global BMNG 500m + GEBCO ocean, z0-8), `hls-regional-z11.yaml` (HLS v2 land imagery per prefecture or miniplanet, z11), and `sentinel2-tokyo-z14.yaml` (Sentinel-2 L2A around Tokyo, z14); each file's header documents the exact commands. Copy one to create your own profile and adjust parameters (seasonal windows, cloud thresholds, ocean options) as needed. `configs/base/pipeline.yaml` is the maintainer's working configuration and currently selects GSI orthophotos, so it is not a starting point for the global pipelines. `planetarble copernicus-layers` lists the Copernicus WMS layers available to your instance. `planetarble acquire` also pulls down the NOAA ETOPO 2022 15 arc-second bedrock GeoTIFF (≈9 GB compressed) whenever `ocean.enabled` is true so ocean shading can run offline; with `gsi_orthophotos` it downloads nothing, because `planetarble process` fetches the GSI tiles itself. Streaming the full HLS land archive during processing remains a long-running operation—plan on 1.6–2.0 TB of transfer against Microsoft Planetary Computer for a complete ZL10 build, roughly 60–75 % lower than fetching land + ocean pixels.

## Imagery Options

Outside Japan, overlays can use login-free 10 m ESA WorldCover composites anywhere on land, NAIP aerial imagery in the US, LINZ imagery in New Zealand, OpenAerialMap, or any openly licensed COG by URL; see `configs/overlays/world-cities-example.yaml` and ADR 0003. The credits of every source used are written into the built PMTiles. See [docs/sources/](docs/sources/README.md) for every supported source with its native resolution, maximum Web Mercator zoom, licence and attribution (日本語: [docs/sources/README.ja.md](docs/sources/README.ja.md)).

- **HLS v2 (default)** — `processing.tile_source: hls` activates the new global workflow. `planetarble acquire` writes `data/plans/hls_z10_plan.ndjson` (`hls_z10_plan_<region>.ndjson` per plan region); `planetarble process` expands it into `output/processing/hls_scene_manifest.json` (`hls_scene_manifest_<region>.json`) with MPC-signed URLs for the required `B02/B03/B04` COGs and QA masks. Seasonal windows default to April–October for the northern hemisphere and October–April for the southern hemisphere. Default collections use `hls2-s30` and `hls2-l30`.
- **Landsat Collection 2 Level-2 SR fallback** — listed in `hls.fallback_collections`. When the primary HLS collections cannot clear clouds, the manifest builder records low-cloud Landsat scenes that align with the same tile footprints and QA masks.
- **NOAA ETOPO 2022 ocean rendering** — `ocean.enabled: true` combines the CC0 bathymetry grid with a configurable color ramp and lambertian hillshade. `planetarble acquire` downloads the global 15 arc-second bedrock GeoTIFF to `data/etopo/ETOPO_2022_15s_bed.tif`. `ocean.source_id` names an asset in `configs/base/assets.yaml`; add an entry there to download from your own mirror.
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
      center: [139.69, 35.69]  # optional
```

The build opens on the middle of its bounds unless the region names a `center: [lon, lat]`. Tokyo needs one: its bounds reach Ogasawara, so their middle is open sea at 146.45 E. `tile --plan-region` writes the center into the MBTiles, and `package --plan-region` applies it to an MBTiles tiled before the region had one. A center outside the bounds is an error.

`configs/profiles/hls-regional-z11.yaml` defines the Japanese prefectures and the 18 global miniplanets as plan regions. Copy it into your workspace directory and run from there:

```bash
cp ../configs/profiles/hls-regional-z11.yaml .

# generate the plan (data/plans/hls_z10_plan_tokyo_land.ndjson)
planetarble acquire --config hls-regional-z11.yaml --plan-region tokyo_land

# build the scene manifest and the regional HLS mosaic
planetarble process --config hls-regional-z11.yaml --plan-region tokyo_land

# tile z11 only (output/tiling/planet_hls_tokyo_land_11z.mbtiles)
planetarble tile --config hls-regional-z11.yaml --plan-region tokyo_land --min-zoom 11 --max-zoom 11

# package under the name that `planetarble serve --region tokyo_land` looks for
planetarble package --config hls-regional-z11.yaml \
  --input output/tiling/planet_hls_tokyo_land_11z.mbtiles \
  --pmtiles-name planet_2024_11z_tokyo_land_hls.pmtiles --plan-region tokyo_land
planetarble serve --config hls-regional-z11.yaml --region tokyo_land --open
```

HLS imagery has about 30 m effective resolution, which maps to about z11 in Web Mercator; deeper zooms are best left to client-side overscaling of the z11 tiles.

Natural Earth admin boundaries (`ne_10m_admin_0_countries.zip`, `ne_10m_admin_1_states_provinces.zip`) are downloaded on demand when a plan region references them.

For a Japan-wide incremental plan and rate-limit guidance, see `docs/operations/hls-japan-plan.md`.

To overlay the regional HLS tiles onto the global BMNG basemap from the quickstart:

```bash
planetarble tiling merge-mbtiles \
  --base output/tiling/planet_2024_8z.mbtiles \
  --overlay output/tiling/planet_hls_tokyo_land_11z.mbtiles \
  --out output/tiling/planet_2024_tokyo_land_hls_11z.mbtiles
```

`planetarble build` with an overlay spec (see [Custom AOI planets](#custom-aoi-planets)) does the same in one step and also fills the zooms between the two sources.

## Quality Tuning

- `processing.tile_quality` (with `processing.tile_format`) sets the tile encoding. The built-in default is WEBP at 82; the recipes in `configs/profiles/` use JPEG at 95. Raise or lower it to trade file size for fidelity.
- Override quality and format per run: `planetarble tile --quality 88 --tile-format WEBP` regenerates MBTiles with WebP tiles at the requested quality.
- After changing quality-related settings, rerun `planetarble tile` and `planetarble package` (and `planetarble process` if upstream rasters changed) to rebuild artifacts.

## Caching & Re-download Policy

- Each asset is downloaded to a deterministic location under `data/`. On repeated runs, Planetarble reuses the existing file; when the asset catalog lists a checksum, the file's SHA256 must match it.
- To force a fresh download (for example, if a file was truncated or updated upstream), pass `--force` to the `planetarble acquire` command (or the `python -m ... acquire` fallback). This flag flows through to the downloader and overwrites local copies.
- `output/MANIFEST.json` records the exact URLs, file sizes, and SHA256 hashes that were used, so a later run can be compared against it.
- When `aria2c` is available it is used automatically to provide resumable downloads. If the binary is not found the CLI falls back to Python's built-in downloader; you can also disable it explicitly with `--no-aria2` if needed.
- Large transfers (several gigabytes) are long-running—consider wrapping the command in `screen` or `tmux` so the process survives SSH disconnects.

## Roadmap

The global floor plus AOI overlay model is implemented (see [Custom AOI planets](#custom-aoi-planets) and [docs/adr/0001-aoi-overlay-architecture.md](docs/adr/0001-aoi-overlay-architecture.md)): a global BMNG base everyone can afford, plus per-area high-resolution overlays from any source declared in a single config (including OpenAerialMap for disaster response), merged into one custom planet via `planetarble build`. The full max-resolution global build stays supported as a special case.

- Support higher-resolution outputs across the entire basemap without compromising reproducibility.
- Provide region-scoped refresh workflows that update only the areas requiring newer imagery.
- Expand quality assurance with automated visual diffs, and run `pmtiles verify` in `planetarble package` as `planetarble tiling pmtiles` already does.

## Requirements

The bundled `Dockerfile` provides everything below in one image (see [Run with Docker](#run-with-docker)); install the toolchain manually only if you prefer running on the host.

- GDAL ≥ 3.11 must be installed locally to run the processing and tiling commands (`gdalbuildvrt`, `gdal_translate`, `gdaldem`, `gdalwarp`, `gdaladdo`, and the unified `gdal` CLI used by `gdal raster tile`).
- The go-pmtiles CLI (`pmtiles convert`) is required to produce the final `planet_{YYYY}_{max_zoom_level}z.pmtiles` artifact.
- `mb-util` (`pip install mbutil`) is required to package XYZ tile pyramids into MBTiles.
- `aria2c` is expected for the default acquisition workflow so downloads can resume cleanly; the CLI falls back to Python’s downloader if aria2c is missing, but installing it avoids broken transfers.
- Python dependencies are recorded in `pyproject.toml` (PyYAML is required for configuration loading); regional HLS/Sentinel-2 planning additionally needs the GDAL Python bindings (`osgeo`).

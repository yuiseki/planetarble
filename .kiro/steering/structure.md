# Repository Structure

This describes the repository as it is. Keep it in sync when modules move.

Repository root
- README.md / README.ja.md — overview, local setup (uv), quickstart, imagery options
- SOURCE.md / SOURCE.ja.md — the source matrix (resolution and licence per imagery source)
- AGENTS.md — rules for coding agents working in this repository
- pyproject.toml / uv.lock — packaging, pinned dependencies, ruff and mypy settings
- Dockerfile — GDAL + go-pmtiles + mb-util runtime image with the CLI
- Makefile — regional HLS batch targets (Kanto, Chubu, Kinki)
- configs/ — pipeline configurations
- docs/ — architecture decisions (adr/) and operational notes (operations/)
- src/planetarble/ — the Python package and `planetarble` CLI
- tests/unit/ — pytest suite (no network; GDAL / numpy tests skip when absent)
- tools/ — one-off generators (e.g. gen_miniplanets.py)
- data/, tmp/, output/ — runtime workspaces (git-ignored)

configs/
- base/pipeline.yaml — default pipeline configuration
- base/assets.yaml — asset catalog (download sources and checksums)
- profiles/ — ready-made recipes (global BMNG z8, regional HLS z11, Sentinel-2 z14)
- overlays/ — AOI overlay specs for `planetarble build` / `prefetch` (ADR 0001)

src/planetarble/
- __init__.py — lazy exports of the main managers and config types
- config/loader.py — YAML/JSON loading into dataclasses; rejects unknown keys and wrong scalar types
- core/models.py — config and metadata dataclasses shared by every module
- logging/ — structured logging (`configure_logging`, `log_step`, `log_skip`)
- acquisition/ — downloads and planning: asset catalog and manifest, BMNG/GEBCO/Natural Earth, HLS planning and STAC search (hls.py), Sentinel-2 (sentinel_2.py), Microsoft Planetary Computer (mpc.py), Copernicus WMS, GSI tiles and mokuroku, OpenAerialMap, miniplanets
- processing/ — raster processing behind `planetarble process`
  - manager.py — `ProcessingManager`, the orchestrator for every tile source
  - runner.py — `CommandRunner` (all external GDAL commands go through it)
  - assets.py — HLS / Sentinel-2 asset download, cache and validity checks
  - hls_mosaic.py, sentinel2_mosaic.py — per-source mosaic building
  - copernicus.py, geometry.py, composite.py, ocean.py, hls.py (scene manifests)
- tiling/ — MBTiles / PMTiles generation and MBTiles utilities (merge, union, stitch, overzoom compositing)
- packaging/ — PMTiles conversion, TileJSON and distribution bundle
- overlay/ — AOI overlay pipeline (spec parsing, source adapters, orchestrator, executor)
- prefetch.py — throttle-aware asset prefetch for overlays
- cli/ — the `planetarble` command (ADR 0002)
  - main.py — `build_parser()` and `main()`; dispatch via `set_defaults(handler=...)`
  - acquire.py, process.py, tile.py, package.py — the four pipeline stages
  - tiling.py, overlay.py, gsi.py, sources.py, serve.py — the other subcommands
  - checks.py — path resolution and artefact validation shared by the commands
- viewer/index.html — MapLibre + pmtiles.js viewer used by `planetarble serve`
- data/ — packaged data (ocean depth ramps)

tests/unit/
- mirrors the package layout (acquisition/, cli/, config/, overlay/, processing/, tiling/)
- cli/golden/parser.json — pinned CLI interface; processing/golden/ — pinned GDAL command lines
- regenerate goldens with `PLANETARBLE_UPDATE_GOLDEN=1` only for an intended change, and review the diff

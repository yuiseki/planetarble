# ADR 0002: Split processing/manager.py and cli/main.py into modules

- Status: Accepted (implemented)
- Date: 2026-11-07
- Deciders: yuiseki

## Implementation status

- Phase 1, processing (done): `processing/manager.py` went from 2,326 to 970 lines and now holds only `ProcessingManager`. Moved in this order: runner and geometry, copernicus, assets, hls_mosaic, sentinel2_mosaic. `assets` moved before the two mosaic modules because both use `_strip_query`, and Sentinel-2 also uses `_scene_cloud_cover` from `hls_mosaic`. Golden command files were unchanged at every step, and a real-GDAL smoke (BMNG compose, normalize, COG, hash reuse) passed.
- Phase 2, cli (done): `cli/main.py` went from 2,068 to about 100 lines. Each command module defines `register_<command>(subcommands)`, called from `build_parser` in the original order, so `--help` lists subcommands as before. `tests/unit/cli/golden/parser.json` pins the parser structure, and a test checks that only `build_parser` calls `register_*`.
- Phase 3 (done): `main()` dispatches with `args.handler(args)`; each subcommand binds its handler with `set_defaults(handler=...)`.

## Context

Two files hold most of the pipeline logic and have grown past the point where they are easy to read or review:

| File | Lines | Shape |
|---|---|---|
| `src/planetarble/processing/manager.py` | 2,326 | `ProcessingManager` (888 lines) plus 64 module-level helpers (about 1,370 lines) for HLS, Sentinel-2, Copernicus, MODIS/VIIRS, asset caching and geometry |
| `src/planetarble/cli/main.py` | 2,068 | a 474-line `build_parser`, an `if` chain in `main()`, and 17 `_handle_*` functions with their validation helpers |

Before this ADR the two files had 22% and 24% branch coverage, so moving code risked silent breakage (a lost import only fails when that path runs, often hours into a real build). The following safety net was put in place first:

- `ruff check` with the pyflakes rules (`F`) in CI, which catches undefined names and stale imports in code that tests do not reach.
- `tests/unit/test_imports.py`: every module imports, with and without GDAL/numpy.
- `tests/unit/cli/test_cli_dispatch.py`: the set of subcommands, `--help` for each, and the command to handler routing.
- `tests/unit/cli/test_cli_handlers.py`: per `tile_source` branch, which manager methods each handler calls, in what order and with which arguments (managers are `create_autospec`'d).
- `tests/unit/processing/test_processing_manager_characterisation.py`: the GDAL command lines each `ProcessingManager` method builds, compared with golden files in `tests/unit/processing/golden/`.

With these, coverage of the two files is 57% and 54%, and the uncovered remainder is mostly network and download code (asset caching, aria2, the `serve` HTTP handler, `gsi-collect`).

## Decision

Split both files by responsibility, one mechanical move per commit, with every commit green on CI. A commit that moves code does not also change what the code does.

### processing

Module-level helpers move out of `processing/manager.py`; the class stays as the orchestrator.

| New module | Contents |
|---|---|
| `processing/runner.py` | `CommandRunner`, `CommandExecutionError`, `_cog_command` |
| `processing/geometry.py` | `bbox_from_geometry`, `clip_land_to_region`, `write_geometry_geojson`, tile/lon/lat helpers |
| `processing/copernicus.py` | Copernicus tile collection, compression/extension by format, `_slugify` |
| `processing/sentinel2_mosaic.py` | scene manifest loading and capping, SAS URL refresh, band and visual VRTs, `_translate_sentinel2_rgb`, mosaic file naming |
| `processing/hls_mosaic.py` | Fmask masking, band lists, VRT and temporal median compositing, `_translate_hls_rgb` |
| `processing/assets.py` | asset download and cache (HLS and Sentinel-2), aria2, quarantine, readability checks, progress formatting |
| `processing/manager.py` | `ProcessingManager` only |

`processing/hls.py` already holds `HLSSceneManifestBuilder`, so the mosaic code goes to `hls_mosaic.py`.

`overlay/resolve.py` currently imports the private `_bbox_from_geometry` and `_clip_land_to_region` from `processing.manager`. They become public names in `processing/geometry.py`.

Order: runner and geometry, then copernicus, assets, hls_mosaic, sentinel2_mosaic (dependencies first).

### cli

Each subcommand's argument definitions move next to its handler. Each command module exposes `register(subparsers)`.

| New module | Contents |
|---|---|
| `cli/checks.py` | artefact validation (`_is_valid_*`, MBTiles metadata) and config / path resolution |
| `cli/acquire.py`, `cli/process.py`, `cli/tile.py`, `cli/package.py` | the four pipeline stages |
| `cli/tiling.py` | `tiling pmtiles`, `merge-mbtiles`, `union-mbtiles`, `stitch-512` |
| `cli/gsi.py` | `gsi-collect`, `gsi-pack`, `gsi-fetch` |
| `cli/sources.py` | `mpc-fetch`, `copernicus-layers` |
| `cli/serve.py` | `serve` and the Range-aware HTTP handler |
| `cli/overlay.py` | `build`, `prefetch`, `split-plan` |
| `cli/main.py` | `main()`, `.env` loading, subcommand registration |

Order: `checks.py` first, then one command group per commit, and finally replace the `if` chain in `main()` with `set_defaults(handler=...)`.

### Tests

Tests move with the code in the same commit. In particular:

- Tests that monkeypatch a helper must patch it in the module that looks it up. For example `_refresh_hls_scene_urls` calls `append_sas_token`; once it lives in `hls_mosaic.py`, the patch target is `planetarble.processing.hls_mosaic.append_sas_token`.
- Tests that import private helpers from `processing.manager` import them from their new module.
- When dispatch moves to `set_defaults(handler=...)`, `test_cli_dispatch.py` checks the handler bound to each parsed command instead of stubbing `_handle_*` names.
- Golden command files must not change during a move. A golden diff means the move changed behaviour.

## Out of scope for the split

These change behaviour, so each gets its own commit after the split, with a test that shows the change:

- `_slugify` is implemented three times (processing, cli, acquisition/copernicus).
- `_is_valid_raster` exists in both processing and cli with different semantics: the processing version returns `False` on a GDAL exception, the cli version enables `gdal.UseExceptions()` and can raise on a corrupt file.
- `_handle_process` contains the GSI orthophoto fetch block twice.
- Splitting `ProcessingManager` itself (about 950 lines after phase 1) into per-source classes is a larger design change. Decide after phase 1.

## Consequences

- No file over about 950 lines, and each module has one reason to change.
- Import paths of private helpers change. They were never public API, and the only in-repo user outside tests (`overlay/resolve.py`) is updated.
- `planetarble.processing.ProcessingManager` and the `planetarble` console entry point keep their current import paths.

# ADR 0001: Global floor plus AOI overlay architecture

- Status: Accepted (implemented; steps 1-4 done)
- Date: 2026-06-04
- Deciders: yuiseki

## Implementation status

- Step 1 (done): the declarative front end. `planetarble.overlay` provides the unified `AOI` type (with `buffer_km`), the `Overlay` / `BaseSpec` / `PipelineSpec` parser (`parse_pipeline_spec`), the `SourceAdapter` protocol plus `SOURCE_REGISTRY` (zoom ceilings mirroring SOURCE.md), and `validate_pipeline_spec` (rejects oversampling). Pure Python, no GDAL or network. Examples: `configs/overlays/atami-example.yaml` (tightly scoped) and `configs/overlays/disaster-example.yaml`. The existing acquire/process/tile/package CLI is untouched.
- Step 2a (done): `resolve_aoi` turns an AOI into a search bbox plus an optional OGR geometry (pure for bbox/miniplanet/buffer, GDAL for natural_earth/geojson/land_only), verified on a GDAL host.
- Step 2b (done): concrete adapters and a factory (`get_adapter`). Each adapter implements the resolution contract (`name`, `native_max_zoom`): BMNG varies by resolution (z8 for 500m, z6 for 2km), OpenAerialMap is per-item with the registry value as an upper guard, the rest report their SOURCE.md ceiling. `plan` / `build_raster` declare the contract and are wired in step 3.
- Step 3a (done): the orchestrator control flow. `build_planet(spec, executor, ...)` validates the spec, builds the base, then for each overlay composites a stack over that overlay's footprint from all lower sources (base + earlier overlays + this one) with overzoom fill, merging onto the running planet in declared order. The heavy work lives behind a `PlanetExecutor` protocol so the flow is unit tested without GDAL.
- Step 3b (done): the concrete `DefaultPlanetExecutor` and the `planetarble build` CLI. It re-encodes the cached global base to webp, builds each overlay COG (OpenAerialMap via its adapter, HLS via HLSMosaicPlanner + ProcessingManager with Fmask cloud masking and the brightened display stretch), tiles to MBTiles, overzoom-composites the stack, merges, and packages. Verified on Atami: `planetarble build --spec configs/overlays/atami-example.yaml --base-mbtiles <floor>` reproduces the planet end to end (z0-18, every layer opaque, no holes) in ~5 minutes with the base reused.
- Step 4 (done): the OpenAerialMap data path. `planetarble.acquisition.openaerialmap` queries OAM, selects finest-GSD items, downloads whole COGs into a cache, and warps the local files into an AOI COG; verified on Atami (56.8MB COG in 24s, local warp in 77s, vs an unfinished /vsicurl warp). The whole-COG cache means re-tiling never re-fetches.

### Overzoom-fill compositing (resolved an open question)

The "raster level vs tile level" composition question is settled in favour of tile level with overzoom fill. `composite_overzoom` (in `planetarble.tiling.mbtiles`) builds each output tile over the AOI by stacking, per source bottom-to-top, that source's tile or an upscaled ancestor, so the finest source is always on top and lower sources fill underneath with no holes (BMNG > z8 is upscaled to fill under HLS/OAM). Overlays are tiled with transparent nodata (`-dstalpha`) so their footprints composite cleanly. MBTiles store TMS rows, so the helpers take XYZ coordinates and convert at the SQL boundary. High zooms are bounded to the AOI footprint to keep tile counts feasible; the global floor stays low zoom.

### Known limitations / follow-ups

- `build_base` reuses a prebuilt global base MBTiles passed via `--base-mbtiles`; it does not yet build the floor from scratch.
- `composite_overzoom` can emit fully-transparent tiles at the AOI edge; skipping them would shrink output and let the viewer overzoom.
- Overlay tiles are 256px and upscaled to the base's 512px during compositing; tiling overlays at 512px would avoid the upscale.

## Context

Planetarble can reproduce a fully open global raster basemap at the highest available resolution. That reproducibility is the project's headline achievement and stays a primary selling point. In practice, though, almost nobody both wants and can afford to reproduce the entire planet at maximum zoom: a global HLS ZL10 build alone moves on the order of 0.75 to 1.0 TB (measured at 407,072 land tiles) and a z12 global PMTiles is about 17 GB.

What most users actually want is a planet that is covered everywhere, with high resolution only over the area they care about. A municipality wants its own city sharp and the rest of the world good enough for context. A disaster response team wants fresh high resolution imagery over the affected region, dropped on top of a global base, today.

Today the pipeline already supports the building blocks for this, but they are not unified:

- A global floor exists (BMNG 500m at z8, BMNG 2km at z6), verified end to end.
- `tiling merge-mbtiles` already overlays regional tiles onto a base (HLS over BMNG, 17 GB artifacts produced).
- Area of interest (AOI) selection exists, but per source and inconsistently: `hls.plan_regions`, `sentinel2.plan_regions`, `gsi_orthophotos.bbox`, `copernicus.bbox`.
- `configs/profiles/*.yaml` are single-source recipes, not multi-source compositions.
- SOURCE.md already documents the intended staged composition (global low zoom plus region-limited high zoom via merge), so the docs already lean this way.

The missing piece is a single declarative way to say "this global floor, plus these AOIs from these sources at these zooms, merged into one planet".

## Decision

Adopt a global floor plus AOI overlay model as the primary way to build a planet, expressed in one config file and executed by one orchestrator.

A pipeline config declares:

1. a `base` source that guarantees global coverage (BMNG by default, optionally HLS for a higher global floor),
2. an ordered list of `overlays`, each pairing an AOI with a source and a zoom range,
3. shared `ocean` and `output` settings.

The orchestrator builds the base, builds each overlay independently, merges the overlays onto the base in declared order (later overlays win), and packages a single planet PMTiles.

The full global maximum resolution build remains supported and documented as the "reproduce the whole planet" path: it is simply the special case of a base whose own source is the high resolution source with no AOI restriction (or the miniplanet shard campaign).

### Proposed config schema

```yaml
# pipeline-disaster.yaml
base:
  source: bmng              # global floor; guarantees full coverage
  resolution: "500m"
  max_zoom: 8

ocean:
  enabled: true             # ETOPO bathymetry hillshade for the base

overlays:
  # later entries are composited on top of earlier ones
  - name: japan_hls
    source: hls
    aoi:
      natural_earth: { dataset: admin_0, where: "adm0_a3='JPN'" }
      land_only: true
    max_zoom: 11

  - name: noto_oam
    source: openaerialmap
    aoi:
      bbox: [136.6, 37.0, 137.4, 37.6]
    source_options:          # source-specific query knobs
      start_date: "2024-01-01"
      end_date: "2024-03-31"
    min_zoom: 8
    max_zoom: 18

output:
  name: planet_disaster_2024
```

AOI is a single reusable type accepting any one of: `bbox`, `natural_earth` (dataset plus WHERE), `miniplanet` (id), or `geojson` (path), optionally with `land_only`. This is the union of what the per source configs accept today, so existing selections map onto it without loss.

### AOI selectors versus miniplanets

Miniplanets are the global-coverage sharding strategy: when the goal is to reproduce the whole planet, the 18 balanced shards split the work evenly and resumably. They are not the right tool when the AOI is already known. For a targeted build you name the AOI directly and fetch nothing more than needed.

Concretely, a "sharpen Atami" planet is not a miniplanet problem. It is:

- `base`: BMNG, global floor
- an HLS overlay whose AOI is `natural_earth` narrowed to just Kanagawa and Shizuoka (not all of Japan, let alone a whole miniplanet)
- an OpenAerialMap overlay whose AOI is a `bbox` (or `geojson`) around the city of Atami

So `miniplanet` is one AOI selector among four, appropriate mainly for the global case; `bbox` / `natural_earth` / `geojson` are how targeted builds express intent. See `configs/overlays/atami-example.yaml` (tightly scoped) and `configs/overlays/disaster-example.yaml` (national context plus a city overlay).

### Buffered footprints for heavy sources

AOI carries an optional `buffer_km`. HLS is the heaviest source to fetch (one STAC search and composite per land ZL10 tile), so a context HLS overlay should derive its footprint from the same target geometry as the high-resolution overlay, expanded by a buffer, rather than from an administrative boundary. Atami is the far-eastern tip of Shizuoka, so selecting `natural_earth` Shizuoka would fetch a whole prefecture of HLS to surround one city. Instead the Atami example gives the HLS overlay the same bbox as the OpenAerialMap overlay plus `buffer_km: 20`. Natural Earth selection stays available, but for heavy sources a buffered target AOI is preferred. The buffer is applied at geometry-resolution time (a later step); step 1 only carries the field.

Administrative boundaries are not just oversized, they can be wildly misleading. Resolving `natural_earth` admin_1 "Tokyo" with land_only against ne_10m_land yields a bbox of roughly (138.9, 24.2, 154.0, 35.9): Tokyo prefecture legally includes the Ogasawara and Izu islands and Minamitorishima, so it spans to latitude 24 and longitude 154, thousands of kilometres of mostly ocean. The buffered Atami target bbox resolves to a tight (138.8, 34.9, 139.3, 35.3) instead. This is the concrete reason heavy sources should avoid admin boundaries as AOIs.

### Source adapter interface

Each source becomes a pluggable adapter behind one protocol, so adding a source (OpenAerialMap being the first new one) does not touch the orchestrator:

```text
SourceAdapter:
  name: str
  native_max_zoom(aoi) -> int          # advertised resolution ceiling (see SOURCE.md)
  plan(aoi, zoom_range) -> Plan        # enumerate work (tiles / scenes / footprints)
  build_raster(plan, workspace) -> Path  # produce a COG or MBTiles for the AOI
```

Existing per source code (HLS planner and manifest builder, Sentinel-2 builder, GSI fetch, BMNG mosaic) is refactored to sit behind this protocol rather than being rewritten. A registry maps `source:` strings to adapters.

### Orchestrator flow

1. Build the base: acquire, process, tile to `base.max_zoom`, producing the base MBTiles.
2. For each overlay in order: resolve the AOI, run the source adapter's `plan` then `build_raster`, then tile to the overlay's zoom range, producing an overlay MBTiles.
3. Merge each overlay onto the running base with `merge-mbtiles` (reusing the existing tool), in declared order.
4. Package the merged MBTiles into `output.name` PMTiles plus TileJSON.

This is the same orchestration identified as the remaining Phase 2 work in `docs/operations/miniplanets-plan-regions.md` (build many shards, merge into one planet), generalized from "miniplanet shards of one source" to "AOIs of arbitrary sources".

### Validation against the source matrix

The orchestrator refuses or warns when an overlay requests a zoom above its source's advertised ceiling in SOURCE.md (for example z18 from 30 m HLS), so a config cannot silently oversample. This keeps SOURCE.md as the single source of truth for resolution limits.

## OpenAerialMap adapter (the motivating new source)

OpenAerialMap (OAM) publishes open, often very recent, high resolution aerial and drone orthophotos, which makes it the natural source for disaster response planets. The adapter queries the OAM API by bbox and date to enumerate imagery footprints, selects the relevant COGs, and warps them into an AOI COG that the tiler takes to high zoom. It is implemented as just another `SourceAdapter`, so a disaster planet is the `pipeline-disaster.yaml` above: a global BMNG floor, a national HLS overlay for context, and a city scale OAM overlay over the affected area.

OAM resolution varies per item, so the maximum usable zoom is per item rather than a fixed source constant. For example HOTOSM OAM item `60e5afbe5bc2dc00058bbe06` (Atami) reaches roughly z20. The adapter derives the real ceiling from the item's ground sample distance; the `openaerialmap` entry in `SOURCE_REGISTRY` (z22) is only an upper guard so validation does not reject legitimate high zoom requests.

## Migration and compatibility

- The existing per source `plan_regions` / `bbox` configs keep working; they are recognized as a legacy front end that internally maps to a single overlay. No existing recipe breaks.
- `configs/profiles/*.yaml` (single source recipes) remain valid and become the simplest case of the new model.
- The unified schema ships alongside the current one first; the orchestrator is additive (a new `build` command or similar), not a rewrite of `acquire` / `process` / `tile` / `package`.

## Consequences

Positive:

- One config file describes a complete custom planet, matching how the majority of users actually think (covered everywhere, sharp where it matters).
- New sources are additive (implement one adapter), and disaster response becomes a config, not a code change.
- The headline "reproduce the whole planet at max resolution" capability is preserved as a documented special case.

Negative or open:

- Larger config surface and a new orchestrator to build, test, and document.
- Source adapter refactor touches code paths that currently work; it must be done behind tests, source by source, without regressing the existing CLI.
- Overlay seams (edge blending, differing radiometry between BMNG, HLS, Sentinel-2, OAM) are a quality concern the merge step does not address today.
- OAM licensing and attribution vary per image; the adapter must carry per scene attribution into the output credits.

## Open questions

- Should overlays compose at the raster level (mosaic before tiling) or only at the tile level (current `merge-mbtiles`)? Tile level is simpler and already works; raster level allows blending.
- How are AOIs that span the antimeridian or polar caps handled uniformly across adapters?
- Where does a prebuilt "global floor" artifact fit, so users can attach only their AOI overlay without rebuilding the base?

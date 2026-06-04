# ADR 0001: Global floor plus AOI overlay architecture

- Status: Proposed
- Date: 2026-06-04
- Deciders: yuiseki

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

OpenAerialMap (OAM) publishes open, often very recent, high resolution aerial and drone orthophotos, which makes it the natural source for disaster response planets. The adapter queries the OAM API by bbox and date to enumerate imagery footprints, selects the relevant COGs, and warps them into an AOI COG that the tiler takes to high zoom (commonly z18 or higher). It is implemented as just another `SourceAdapter`, so a disaster planet is the `pipeline-disaster.yaml` above: a global BMNG floor, a national HLS overlay for context, and a city scale OAM overlay over the affected area.

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

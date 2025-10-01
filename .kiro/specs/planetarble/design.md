# Design Overview

Planetarble is a focused pipeline that transforms three open datasets into a single `planet_{YYYY}_{max_zoom_level}z.pmtiles` archive suitable for offline use. The architecture is intentionally streamlined: each phase outputs the minimum artifacts required to satisfy the project scope while avoiding optional complexity.

## System Phases

```
Data Sources → Acquisition → Preprocessing → Tile Generation → Packaging & Delivery
```

### 1. Data Sources
- **NASA BMNG (2004)** — base natural-color imagery.
- **GEBCO Global Grid** — bathymetry/topography grid for hillshading oceans.
- **Natural Earth 10 m** — land/ocean masks for blending and empty-tile pruning.

### 2. Acquisition
- Maintain an asset catalog describing dataset names, URLs, expected sizes, and license notes.
- Download each asset with retry logic; compute SHA256 hashes.
- Produce `MANIFEST.json` summarizing the final file locations and checksums.

### 3. Preprocessing
- Ensure BMNG imagery is in EPSG:4326 with correct RGB band order.
- Apply light color correction (≤5 % saturation) when needed to improve contrast.
- Derive a GEBCO hillshade (azimuth 315°, altitude 45°) and blend into ocean pixels at 10–20 % opacity.
- Generate land/ocean masks from Natural Earth to guide blending and to skip empty ocean tiles later.
- Optionally store intermediary rasters as Cloud Optimized GeoTIFFs to accelerate downstream reads.

### 4. Tile Generation
- Reproject the blended raster to EPSG:3857, clipping at ±85.0511° latitude.
- Create an XYZ tile pyramid covering zoom levels `0–10` with 256 px tiles.
- Encode tiles uniformly as JPEG (quality 75–85) or WebP.
- Package the pyramid as MBTiles (or an equivalent tilestore) in preparation for PMTiles conversion.
- Record tile metadata (bounds, center, minzoom, maxzoom, attribution) for later reuse.

### 5. Packaging & Delivery
- Run the PMTiles CLI to convert MBTiles output into `planet_{YYYY}_{max_zoom_level}z.pmtiles`, leveraging built-in deduplication.
- Write `planet_{YYYY}_{max_zoom_level}z.tilejson.json` using the recorded metadata and embed the same information in the PMTiles archive.
- Generate `LICENSE_AND_CREDITS.txt` from the asset catalog, ensuring NASA, GEBCO, and Natural Earth attributions.
- Bundle `MANIFEST.json`, TileJSON, and PMTiles file as the primary deliverable set.
- Provide a lightweight HTML viewer (MapLibre GL + pmtiles protocol) for optional offline inspection and document usage of `pmtiles serve` for HTTP endpoints.

## Logging & Configuration
- Each phase should emit structured logs (JSON or key-rich text) so that pipeline progress is easy to audit.
- Configuration resides in simple YAML/JSON documents controlling input/output directories, zoom range, and tile encoding options.

## Verification
- Minimum verification includes sampling random tiles at multiple zoom levels, running `pmtiles verify`, and confirming the presence of required metadata fields.
- Additional QA (automated visual comparisons, performance benchmarking) is recorded as optional future work.

## Extensibility Notes (Optional)
- The pipeline can accept alternative open imagery (e.g., MODIS MCD43A4, VIIRS) once acquisition and preprocessing adapters are added.
- Extending to zoom levels `11–12` primarily impacts storage and processing time; no architectural changes are required, but configuration defaults should guard against accidental overcommit.

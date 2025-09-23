# Requirements

## 1. Project Scope

### 1.1 Goals
- Produce a single `world_YYYY.pmtiles` file containing a global raster basemap built solely from open data.
- Serve XYZ (`{z}/{x}/{y}`) 256 px tiles in EPSG:3857 for zoom levels `0–10` (optionally extendable to `12` when resources allow).
- Enable fully offline (air-gapped) usage via direct PMTiles access or by exposing an HTTP endpoint that serves the same tiles.

### 1.2 Non-Goals
- Ultra-high-resolution tiling at zoom `≥13`.
- Dependencies on proprietary imagery, cloud billing, or CDN services.
- Redistribution of commercial/composite imagery such as EOX assets.
- Automating future yearly refresh workflows (treated as a later enhancement).

## 2. Mandatory Artifacts
- `world_YYYY.pmtiles` — PMTiles archive storing raster tiles in JPEG or WebP format.
- `world_YYYY.tilejson.json` — TileJSON metadata (bounds, center, minzoom, maxzoom, attribution, format).
- `LICENSE_AND_CREDITS.txt` — Attributions for NASA, GEBCO, and Natural Earth sources with redistribution notes.
- `MANIFEST.json` — Source inventory that records URLs, SHA256 checksums, file sizes, and generation parameters.
- (Optional) Minimal HTML viewer using MapLibre GL + pmtiles protocol for local verification.

## 3. Data Sources
- **NASA Blue Marble Next Generation (BMNG, 2004)** — Prefer the 500 m/pixel (86 400×43 200) composite, fall back to the 2 km/pixel version when necessary. Attribution: NASA Earth Observatory/NASA.
- **GEBCO Global Grid (latest available year)** — 15″ bathymetry/topography grid. Attribution: GEBCO Compilation Group.
- **Natural Earth (10 m land, ocean, coastline layers)** — Public domain masks for transparency handling and empty-tile pruning.

All downloads must be checksum-verified and recorded in the manifest. Licensing guidance goes into `LICENSE_AND_CREDITS.txt`.

## 4. Pipeline Requirements

### 4.1 Acquisition
- Provide a catalog of the required files (dataset name, URL, expected size, hash placeholder).
- Automate downloads with retry and checksum verification.

### 4.2 Preprocessing
- Confirm or assign EPSG:4326 on BMNG rasters; adjust band order if required.
- Apply light color normalization to BMNG imagery (e.g., ≤5 % saturation boost) when beneficial.
- Generate GEBCO hillshade (azimuth 315°, altitude 45°) and blend into oceans at 10–20 % opacity.
- Create land/ocean masks from Natural Earth data to assist blending and to skip empty ocean tiles.
- Optionally produce Cloud Optimized GeoTIFFs to speed reprojection and tiling.

### 4.3 Tile Generation
- Reproject the blended raster into EPSG:3857 with latitude clipping at ±85.0511°.
- Build a tile pyramid for zoom levels `0–10` using 256 px XYZ tiles.
- Choose JPEG (quality 75–85) or WebP for encoding; use a single format consistently.
- Generate an MBTiles (or equivalent) intermediate artifact prior to PMTiles conversion.

### 4.4 Packaging & Distribution
- Convert MBTiles output to PMTiles using the PMTiles CLI to benefit from deduplication.
- Embed TileJSON metadata in both the PMTiles archive and standalone `.tilejson.json` file.
- Produce `LICENSE_AND_CREDITS.txt` and `MANIFEST.json` alongside the PMTiles file.
- Support offline viewing either via `pmtiles serve` or the optional HTML viewer.

## 5. Operational Considerations
- The build process must run with only local tools (GDAL ≥3.x and PMTiles CLI) after data download.
- Provide clear logging for each pipeline stage.
- Document manual verification steps (sample tile checks, pmtiles verify).
- Keep configuration values (paths, zoom levels, output format) adjustable via simple YAML/JSON configs.

## 6. Optional Enhancements (Reference Only)
- Extend zoom coverage to `z=12` when storage budgets allow.
- Explore alternative open imagery sources (e.g., MODIS MCD43A4, VIIRS) for future refresh cycles.
- Automate visual QA or performance benchmarking as the project matures.

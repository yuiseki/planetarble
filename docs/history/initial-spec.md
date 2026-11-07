# Initial specification (September 2025)

This is the original product overview and spec, written with Kiro when the project started (2025-09-23) and kept here unchanged as a record. It describes the first scope: a global BMNG + GEBCO + Natural Earth basemap at zoom 0-10 in a single PMTiles file. The project has since grown (HLS, Sentinel-2, GSI, Copernicus and OpenAerialMap sources, AOI overlays, uv / CI tooling), so where this differs from the code, the code, README.md and the ADRs in docs/adr/ are authoritative. The unchecked boxes in the implementation plan were never updated; the work they list is done.

## Product overview

Planetarble is a comprehensive open-source solution for generating global planetary imagery tiles from open data sources. The name combines "planet" and "Blue Marble" (NASA's famous Earth satellite imagery), representing the project's mission to make planetary-scale satellite imagery accessible to everyone through completely open data and tools.

### Key Characteristics
- **Complete Open Source Stack**: Uses only NASA Blue Marble Next Generation (2004), GEBCO bathymetry, and Natural Earth data
- **Single-File Distribution**: Packages global imagery into a single PMTiles file (14-56GB) for easy deployment
- **Air-Gap Capable**: Designed for offline environments with no internet dependencies after initial build
- **Web-Standard Compatible**: Generates Web Mercator tiles (EPSG:3857) compatible with MapLibre GL and standard web mapping libraries
- **Reproducible Pipeline**: Fully automated build process using GDAL and PMTiles CLI
- **Global Coverage**: Provides complete Earth imagery from zoom levels 0-10 (optionally 0-12)

### Target Users

#### Primary Target
- **Interactive Web Map Visualization Developers**: Building web applications with MapLibre GL, Leaflet, or similar libraries who need self-hosted satellite imagery tiles

#### Technical Skill Requirements

##### For Consumers (Map Display)
- Basic knowledge of MapLibre GL JS or similar web mapping libraries
- Understanding of PMTiles integration with web mapping frameworks
- No specialized geospatial expertise required

##### For Producers (Tile Generation)
- Advanced knowledge of geospatial technologies: GeoTIFF, COG, GDAL, PMTiles
- Experience with cloud storage solutions: S3, GCS, Cloudflare
- Understanding of raster data processing and tile generation workflows

#### Secondary Users
- **Organizations requiring data sovereignty**: Companies and agencies needing complete control over their mapping infrastructure
- **Cost-conscious developers**: Teams seeking to eliminate per-request API fees and traffic-based billing
- **Air-gapped environments**: Secure facilities, remote locations, or offline-first applications (extreme use case)

### Core Value Proposition

#### For Developers
- **Zero Vendor Lock-in**: Complete independence from proprietary mapping services
- **Predictable Costs**: One-time processing cost instead of per-request API fees
- **Full Control**: Customize quality, format, and zoom levels for specific needs
- **Legal Clarity**: Clear open data licensing without usage restrictions

#### For Organizations
- **Security Compliance**: Deploy in air-gapped environments without external dependencies
- **Data Sovereignty**: Host and control your own global mapping data
- **Cost Efficiency**: Eliminate ongoing API costs for high-volume applications
- **Reliability**: No network dependencies or service outages for core functionality

#### For the Community
- **Democratized Access**: Proves that anyone can create Earth-scale imagery with open tools
- **Educational Resource**: Demonstrates modern geospatial data processing techniques
- **Foundation for Innovation**: Provides base layer for specialized mapping applications
- **Open Science**: Promotes reproducible research with documented methodologies

### Technical Innovation
- **Hybrid Data Fusion**: Combines satellite imagery with bathymetric hillshading for enhanced ocean visualization
- **Efficient Packaging**: Uses PMTiles format for optimized storage and serving
- **Quality Optimization**: Balances visual quality with file size through configurable compression
- **Modular Architecture**: Separates data acquisition, processing, tiling, and deployment phases

### Use Cases
- Offline mapping applications for remote locations
- Educational tools for geography and Earth sciences
- Research platforms requiring reproducible basemaps
- Government and military applications in secure environments
- Custom cartographic projects with global scope
- Backup mapping solutions for critical infrastructure

## Requirements

### 1. Project Scope

#### 1.1 Goals
- Produce a single `planet_{YYYY}_{max_zoom_level}z.pmtiles` file containing a global raster basemap built solely from open data.
- Serve XYZ (`{z}/{x}/{y}`) 256 px tiles in EPSG:3857 for zoom levels `0–10` (optionally extendable to `12` when resources allow).
- Enable fully offline (air-gapped) usage via direct PMTiles access or by exposing an HTTP endpoint that serves the same tiles.

#### 1.2 Non-Goals
- Ultra-high-resolution tiling at zoom `≥13`.
- Dependencies on proprietary imagery, cloud billing, or CDN services.
- Redistribution of commercial/composite imagery such as EOX assets.
- Automating future yearly refresh workflows (treated as a later enhancement).

### 2. Mandatory Artifacts
- `planet_{YYYY}_{max_zoom_level}z.pmtiles` — PMTiles archive storing raster tiles in JPEG or WebP format.
- `planet_{YYYY}_{max_zoom_level}z.tilejson.json` — TileJSON metadata (bounds, center, minzoom, maxzoom, attribution, format).
- `LICENSE_AND_CREDITS.txt` — Attributions for NASA, GEBCO, and Natural Earth sources with redistribution notes.
- `MANIFEST.json` — Source inventory that records URLs, SHA256 checksums, file sizes, and generation parameters.
- (Optional) Minimal HTML viewer using MapLibre GL + pmtiles protocol for local verification.

### 3. Data Sources
- **NASA Blue Marble Next Generation (BMNG, 2004)** — Prefer the 500 m/pixel (86 400×43 200) composite, fall back to the 2 km/pixel version when necessary. Attribution: NASA Earth Observatory/NASA.
- **GEBCO Global Grid (latest available year)** — 15″ bathymetry/topography grid. Attribution: GEBCO Compilation Group.
- **Natural Earth (10 m land, ocean, coastline layers)** — Public domain masks for transparency handling and empty-tile pruning.

All downloads must be checksum-verified and recorded in the manifest. Licensing guidance goes into `LICENSE_AND_CREDITS.txt`.

### 4. Pipeline Requirements

#### 4.1 Acquisition
- Provide a catalog of the required files (dataset name, URL, expected size, hash placeholder).
- Automate downloads with retry and checksum verification.

#### 4.2 Preprocessing
- Confirm or assign EPSG:4326 on BMNG rasters; adjust band order if required.
- Apply light color normalization to BMNG imagery (e.g., ≤5 % saturation boost) when beneficial.
- Generate GEBCO hillshade (azimuth 315°, altitude 45°) and blend into oceans at 10–20 % opacity.
- Create land/ocean masks from Natural Earth data to assist blending and to skip empty ocean tiles.
- Optionally produce Cloud Optimized GeoTIFFs to speed reprojection and tiling.

#### 4.3 Tile Generation
- Reproject the blended raster into EPSG:3857 with latitude clipping at ±85.0511°.
- Build a tile pyramid for zoom levels `0–10` using 256 px XYZ tiles.
- Choose JPEG (quality 75–85) or WebP for encoding; use a single format consistently.
- Generate an MBTiles (or equivalent) intermediate artifact prior to PMTiles conversion.

#### 4.4 Packaging & Distribution
- Convert MBTiles output to PMTiles using the PMTiles CLI to benefit from deduplication.
- Embed TileJSON metadata in both the PMTiles archive and standalone `.tilejson.json` file.
- Produce `LICENSE_AND_CREDITS.txt` and `MANIFEST.json` alongside the PMTiles file.
- Support offline viewing either via `pmtiles serve` or the optional HTML viewer.

### 5. Operational Considerations
- The build process must run with only local tools (GDAL ≥3.x and PMTiles CLI) after data download.
- Provide clear logging for each pipeline stage.
- Document manual verification steps (sample tile checks, pmtiles verify).
- Keep configuration values (paths, zoom levels, output format) adjustable via simple YAML/JSON configs.

### 6. Optional Enhancements (Reference Only)
- Extend zoom coverage to `z=12` when storage budgets allow.
- Explore alternative open imagery sources (e.g., MODIS MCD43A4, VIIRS) for future refresh cycles.
- Automate visual QA or performance benchmarking as the project matures.

## Design

Planetarble is a focused pipeline that transforms three open datasets into a single `planet_{YYYY}_{max_zoom_level}z.pmtiles` archive suitable for offline use. The architecture is intentionally streamlined: each phase outputs the minimum artifacts required to satisfy the project scope while avoiding optional complexity.

### System Phases

```
Data Sources → Acquisition → Preprocessing → Tile Generation → Packaging & Delivery
```

#### 1. Data Sources
- **NASA BMNG (2004)** — base natural-color imagery.
- **GEBCO Global Grid** — bathymetry/topography grid for hillshading oceans.
- **Natural Earth 10 m** — land/ocean masks for blending and empty-tile pruning.

#### 2. Acquisition
- Maintain an asset catalog describing dataset names, URLs, expected sizes, and license notes.
- Download each asset with retry logic; compute SHA256 hashes.
- Produce `MANIFEST.json` summarizing the final file locations and checksums.

#### 3. Preprocessing
- Ensure BMNG imagery is in EPSG:4326 with correct RGB band order.
- Apply light color correction (≤5 % saturation) when needed to improve contrast.
- Derive a GEBCO hillshade (azimuth 315°, altitude 45°) and blend into ocean pixels at 10–20 % opacity.
- Generate land/ocean masks from Natural Earth to guide blending and to skip empty ocean tiles later.
- Optionally store intermediary rasters as Cloud Optimized GeoTIFFs to accelerate downstream reads.

#### 4. Tile Generation
- Reproject the blended raster to EPSG:3857, clipping at ±85.0511° latitude.
- Create an XYZ tile pyramid covering zoom levels `0–10` with 256 px tiles.
- Encode tiles uniformly as JPEG (quality 75–85) or WebP.
- Package the pyramid as MBTiles (or an equivalent tilestore) in preparation for PMTiles conversion.
- Record tile metadata (bounds, center, minzoom, maxzoom, attribution) for later reuse.

#### 5. Packaging & Delivery
- Run the PMTiles CLI to convert MBTiles output into `planet_{YYYY}_{max_zoom_level}z.pmtiles`, leveraging built-in deduplication.
- Write `planet_{YYYY}_{max_zoom_level}z.tilejson.json` using the recorded metadata and embed the same information in the PMTiles archive.
- Generate `LICENSE_AND_CREDITS.txt` from the asset catalog, ensuring NASA, GEBCO, and Natural Earth attributions.
- Bundle `MANIFEST.json`, TileJSON, and PMTiles file as the primary deliverable set.
- Provide a lightweight HTML viewer (MapLibre GL + pmtiles protocol) for optional offline inspection and document usage of `pmtiles serve` for HTTP endpoints.

### Logging & Configuration
- Each phase should emit structured logs (JSON or key-rich text) so that pipeline progress is easy to audit.
- Configuration resides in simple YAML/JSON documents controlling input/output directories, zoom range, and tile encoding options.

### Verification
- Minimum verification includes sampling random tiles at multiple zoom levels, running `pmtiles verify`, and confirming the presence of required metadata fields.
- Additional QA (automated visual comparisons, performance benchmarking) is recorded as optional future work.

### Extensibility Notes (Optional)
- The pipeline can accept alternative open imagery (e.g., MODIS MCD43A4, VIIRS) once acquisition and preprocessing adapters are added.
- Extending to zoom levels `11–12` primarily impacts storage and processing time; no architectural changes are required, but configuration defaults should guard against accidental overcommit.

## Implementation plan

- [ ] 1. Prepare acquisition assets
  - Draft the asset catalog with dataset metadata, URLs, and license notes
  - Implement download scripts with retry + SHA256 verification
  - Emit `MANIFEST.json` after successful downloads

- [ ] 2. Build preprocessing routines
  - Normalize BMNG imagery (SRS, color tweak, band order)
  - Generate GEBCO hillshade and blend into oceans (10–20 % opacity)
  - Create Natural Earth land/ocean masks for blending and tile pruning

- [ ] 3. Reproject and tile
  - Reproject the blended raster to EPSG:3857 with ±85.0511° clipping
  - Produce XYZ tiles for zoom levels 0–10 (256 px) and store as MBTiles
  - Encode tiles as JPEG (quality 75–85) or WebP consistently

- [ ] 4. Package deliverables
  - Convert MBTiles output to `planet_{YYYY}_{max_zoom_level}z.pmtiles` using the PMTiles CLI
  - Produce `planet_{YYYY}_{max_zoom_level}z.tilejson.json`, `LICENSE_AND_CREDITS.txt`, and update `MANIFEST.json`
  - Document usage of `pmtiles serve` and the optional HTML viewer for offline inspection

- [ ] 5. Implement configuration & logging
  - Provide YAML/JSON-driven configuration for paths, zoom range, and encoding
  - Add structured logging helpers for each stage and document expected log outputs

- [ ] 6. Validate outputs
  - Run `pmtiles verify` and inspect sample tiles across zoom levels
  - Confirm metadata correctness and attribution files before distribution

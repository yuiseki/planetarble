# Implementation Plan

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

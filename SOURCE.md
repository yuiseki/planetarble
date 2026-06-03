# Planetarble Supported Sources

A summary of every data source planetarble can ingest: ID, asset/collection, formal name, maximum native resolution, and the corresponding maximum Web Mercator zoom level. 日本語版: [SOURCE.ja.md](SOURCE.ja.md)

The ID column is the pipeline-side name (a `processing.tile_source` value or config block name). The asset / collection column lists the concrete identifiers (STAC collections, `assets.yaml` asset ids, product ids).

## Zoom level conversion

Ground resolution at the equator for 256px tiles is `156543.03 / 2^z` m/px. For a source with native resolution R, the nominal maximum zoom is `z = log2(156543 / R)` rounded. At latitude φ the same zoom is finer by a factor of cos φ (about 0.82 around Japan at φ≈35°), so practical choices carry a margin of ±1 zoom.

| z | Resolution at equator (m/px) |
|---|---|
| 6 | 2,446 |
| 8 | 611 |
| 11 | 76.4 |
| 12 | 38.2 |
| 14 | 9.55 |
| 18 | 0.597 |

## Satellite and aerial imagery sources (what the tiles look like)

| ID | asset / collection | Formal name | Max resolution | Max zoom |
|---|---|---|---|---|
| `gsi_orthophotos` | `seamlessphoto` / `ort` | GSI Tiles: Zenkoku Saishin Shashin (seamless) / Digital Japan Basic Map orthophoto | Source data 0.2 to 0.4 m class; tiles served up to z18 (z19 in some areas) | **z18** (service limit) |
| `sentinel2` | `sentinel-2-l2a` | Copernicus Sentinel-2 Level-2A (via Microsoft Planetary Computer, `visual` = TCI 10m) | 10 m | **z14** (9.55 m/px) |
| `copernicus` | `copernicus_sentinel2_true_color` (layer: `TRUE_COLOR`) | Copernicus Data Space Ecosystem Sentinel Hub WMS (Sentinel-2 derived) | 10 m | **z14** |
| `hls` | `hls2-s30` / `hls2-l30` | NASA/USGS Harmonized Landsat and Sentinel-2 (HLS) v2.0 S30/L30 | 30 m (nominal) | z12 (computed) / **z11 in practice** |
| `hls` (fallback) | `landsat-c2-l2` | USGS Landsat Collection 2 Level-2 Surface Reflectance | 30 m | z12 |
| `bmng` | `bmng_2004_aug_500m_a1` to `d2` | NASA Blue Marble Next Generation (August 2004, topo+bathy composite, 500m, 8 panels) | 500 m | **z8** (611 m/px) |
| `bmng` | `bmng_2004_aug_2km_global` | NASA Blue Marble Next Generation (August 2004, topo+bathy composite, 2km single frame) | ~2 km | **z6** (2,446 m/px) |
| `modis` | `MCD43A4.061` | MODIS Nadir BRDF-Adjusted Reflectance (NBAR) v6.1 (via AppEEARS) | 500 m | z8 |
| `viirs` | `VNP09GA.002` | VIIRS/NPP Surface Reflectance Daily L2G v2 (via AppEEARS) | 500 m (I bands) / 1 km (M bands) | z7 to z8 |

The HLS operational value of z11 follows the README: compositing lowers the effective resolution below the nominal 30 m, and z12 is served via client-side overscaling.

## Elevation and bathymetry sources (ocean shading and hillshade)

| ID | asset | Formal name | Max resolution | Max zoom |
|---|---|---|---|---|
| `ocean` | `etopo_2022_15s_bedrock_cog` | NOAA ETOPO 2022 Global Relief Model (15 arc-second bedrock, CC0) | 15 arcsec ≈ 464 m | ~z8 to z9 |
| (legacy BMNG stage) | `gebco_latest_grid` | GEBCO 2024 Grid (GEBCO Compilation Group, NetCDF) | 15 arcsec ≈ 464 m | ~z8 to z9 |

Note: the config `ocean.source_id` references `etopo_2022_15arcsec_geotiff`, which does not match the `assets.yaml` key `etopo_2022_15s_bedrock_cog` (needs verification).

## Auxiliary sources (non-imagery)

| ID | asset | Formal name | Purpose |
|---|---|---|---|
| (masks and region filters) | `natural_earth_{land,ocean,coastline,admin_0,admin_1}_10m` | Natural Earth 1:10m Physical / Cultural Vectors | Vectors used for land/ocean mask generation, plan_region admin filters, and land_only clipping. Zoom levels do not apply (cartographic guideline around z5) |

## Summary: which sources serve which zoom

| Target max zoom | Usable sources |
|---|---|
| z6 | BMNG 2km |
| z8 | BMNG 500m, MODIS, (VIIRS), ETOPO/GEBCO shading |
| z11 to z12 | HLS v2, Landsat C2 L2 (land only) |
| z14 | Sentinel-2 L2A, Copernicus WMS (land focused) |
| z18 | GSI aerial photos (Japan only) |

The intended composition is staged: global coverage at low zoom (BMNG/HLS plus ETOPO ocean), with region-limited sources (Sentinel-2, GSI) overlaid at high zoom via `merge-mbtiles` (see Regional HLS Planning and Roadmap in the README).

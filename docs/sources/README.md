# Data sources

Every data source planetarble can ingest has one page under `docs/sources/{area}/{dataset}.md`. The front matter of each page is the reference for its pipeline ID, coverage, resolution, maximum Web Mercator zoom, licence and attribution; `SOURCE_REGISTRY` in `src/planetarble/overlay/sources.py` must agree with it (a unit test checks the zoom ceilings). 日本語: [README.ja.md](README.ja.md)

## Zoom level conversion

Ground resolution at the equator for 256 px tiles is `156543.03 / 2^z` m/px. For a source with native resolution R, the nominal maximum zoom is `z = log2(156543 / R)` rounded. At latitude φ the same zoom is finer by a factor of cos φ (about 0.82 around Japan at φ≈35°), so practical choices carry a margin of ±1 zoom.

| z | Resolution at equator (m/px) |
|---|---|
| 6 | 2,446 |
| 8 | 611 |
| 11 | 76.4 |
| 12 | 38.2 |
| 14 | 9.55 |
| 18 | 0.597 |

## Imagery

| Area | Dataset | ID | Resolution | Max zoom | Licence |
|---|---|---|---|---|---|
| global | [NASA Blue Marble Next Generation](global/bmng.md) | `bmng` | 500 m / ~2 km | z8 / z6 | NASA, credit required |
| global | [HLS v2.0](global/hls.md) | `hls` | 30 m | z12 (z11 in practice) | NASA open |
| global | [Landsat C2 L2](global/landsat-c2-l2.md) | `landsat-c2-l2` | 30 m | z12 | public domain |
| global | [Sentinel-2 L2A](global/sentinel2.md) | `sentinel2` | 10 m | z14 | Copernicus open |
| global | [Copernicus WMS](global/copernicus.md) | `copernicus` | 10 m | z14 | Copernicus open (account) |
| global | [MODIS MCD43A4](global/modis.md) | `modis` | 500 m | z8 | NASA open |
| global | [VIIRS VNP09GA](global/viirs.md) | `viirs` | 500 m / 1 km | z8 | NASA open |
| worldwide (scattered) | [OpenAerialMap](global/openaerialmap.md) | `openaerialmap` | varies, sub-metre | z22 (per item) | CC BY 4.0 |
| Japan | [GSI orthophotos](japan/gsi-orthophotos.md) | `gsi_orthophotos` | 0.2 to 0.4 m | z18 | PDL 1.0 (CC BY compatible) |

## Elevation, bathymetry and vectors

| Area | Dataset | Used for | Licence |
|---|---|---|---|
| global | [NOAA ETOPO 2022](global/etopo.md) | ocean colour and hillshade (`ocean`) | CC0 1.0 |
| global | [GEBCO 2024](global/gebco.md) | legacy BMNG hillshade | public domain |
| global | [Natural Earth 1:10m](global/natural-earth.md) | land/ocean masks, region filters | public domain |

## Considered and not adopted

| Dataset | Why not |
|---|---|
| Maxar Open Data (disaster imagery) | CC BY-NC 4.0: no commercial redistribution |
| NAIP on AWS (`s3://naip-*`) | requester pays; the same imagery is free on Microsoft Planetary Computer |
| JAXA ALOS AVNIR-2 ORI (Japan, 10 m, 2006 to 2011) | download needs an Earthdata login; JAXA terms require prior contact for commercial use |

## Which sources serve which zoom

| Target max zoom | Usable sources |
|---|---|
| z6 | BMNG 2 km |
| z8 | BMNG 500 m, MODIS, (VIIRS), ETOPO/GEBCO shading |
| z11 to z12 | HLS v2, Landsat C2 L2 (land only) |
| z14 | Sentinel-2 L2A, Copernicus WMS |
| z18 and above | GSI (Japan), OpenAerialMap (where imagery exists) |

The intended composition is staged: global coverage at low zoom (BMNG/HLS plus ETOPO ocean), with region-limited sources overlaid at high zoom (see ADR 0001 and ADR 0003).

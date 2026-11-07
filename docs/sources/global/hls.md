---
id: hls
title: NASA/USGS Harmonized Landsat and Sentinel-2 (HLS) v2.0 S30/L30
status: supported (overlay and legacy pipeline)
coverage: global land
resolution: 30 m nominal
max_zoom: 12
license: NASA Earth science data, open (terms not re-checked for this page)
attribution: "HLS v2.0 (NASA/USGS)"
access: Microsoft Planetary Computer STAC (hls2-s30, hls2-l30) with SAS tokens
assets:
  - hls2-s30
  - hls2-l30
---
# Harmonized Landsat and Sentinel-2 (HLS)

Computed ceiling z12; z11 is used in practice because temporal compositing lowers the effective resolution below the nominal 30 m, and z12 is served by client-side overscaling. Falls back to Landsat Collection 2 Level-2 where HLS has no scenes.

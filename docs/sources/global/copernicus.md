---
id: copernicus
title: Copernicus Data Space Ecosystem Sentinel Hub WMS (Sentinel-2 derived)
status: supported (legacy pipeline)
coverage: global land
resolution: 10 m
max_zoom: 14
license: Copernicus Sentinel data; Copernicus Data Space Ecosystem terms
attribution: "Contains modified Copernicus Sentinel data [year]"
access: WMS with a Copernicus Data Space Ecosystem account (credentials required)
assets:
  - copernicus_sentinel2_true_color (layer TRUE_COLOR)
---
# Copernicus Data Space Ecosystem WMS

Requires credentials (see `.env.example`). Tiles are fetched per layer and georeferenced with `tile_bounds_3857`.

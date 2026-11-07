---
id: ocean
title: NOAA ETOPO 2022 Global Relief Model (15 arc-second bedrock)
status: supported (ocean shading)
coverage: global
resolution: 15 arc-seconds (~464 m)
max_zoom: 9
license: CC0 1.0
attribution: "ETOPO 2022 Global Relief Model (NOAA NCEI, 2022)"
access: single global COG (self-hosted mirror; NOAA distributes 15x15 degree tiles)
assets:
  - etopo_2022_15s_bedrock_cog
---
# NOAA ETOPO 2022

Used for ocean depth colouring and hillshade. The original Stanford NatCap GCS cache returns 403, so the first asset URL is a self-hosted mirror; `ocean.source_id` can point at a custom asset.

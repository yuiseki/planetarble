---
id: openaerialmap
title: OpenAerialMap
status: supported (overlay)
coverage: worldwide, scattered footprints
resolution: varies per image, often sub-metre
max_zoom: 22
license: CC BY 4.0 per image (provider named in the image metadata)
attribution: "OpenAerialMap contributors (CC BY 4.0)"
access: OAM API search; whole COGs cached under data/cache/oam
assets:
  - per-item COG
---
# OpenAerialMap

Works anywhere OAM has imagery: give an overlay `source: openaerialmap` and a bbox. The registry ceiling z22 is only an upper guard; the adapter reads the real ceiling from the selected item's ground sample distance. The image provider (for example HOTOSM, NOAA) is in each item's metadata; add it to the overlay's `attribution` when publishing.

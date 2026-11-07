---
id: cog
title: Any openly licensed Cloud Optimized GeoTIFF, named by URL
status: supported (overlay)
coverage: wherever the named COGs are
resolution: that of the COGs
max_zoom: 22
license: that of the COGs; the overlay must state the credit
attribution: ""
access: HTTP(S) range reads of the given URLs, no login
---

# Generic COG source

For imagery without a searchable index, such as statewide or park-wide mosaics published as single COGs. The spec names the URLs and must give an `attribution` (the build refuses the spec otherwise); the registry ceiling z22 is only an upper guard, so set `max_zoom` to what the imagery supports.

```yaml
overlays:
  - name: montpelier
    source: cog
    aoi: {bbox: [-72.59, 44.25, -72.56, 44.27]}
    attribution: "Imagery: Vermont Center for Geographic Information (VCGI)"
    source_options:
      urls: ["https://vtopendata-prd.s3.amazonaws.com/Imagery/2022_30cm_LeafOFF_4Band.tif"]
      bands: [1, 2, 3]          # default
      # nodata: 0               # treat 0 as transparent
      # stretch: {low: 0, high: 4000, gamma: 0.8}   # for 16-bit imagery
    min_zoom: 14
    max_zoom: 19
```

Known COG mosaics that fit (each has its own page): [Banff National Park 2025](../canada/banff-national-park.md), [Vermont statewide orthoimagery](../united-states/vermont-orthoimagery.md).

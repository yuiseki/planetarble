---
id: naip
title: USDA National Agriculture Imagery Program (NAIP), via Microsoft Planetary Computer
status: supported (overlay)
coverage: contiguous United States
resolution: 0.3 to 1 m (0.6 m for most states and years)
max_zoom: 18
license: public domain (US Government work)
attribution: "NAIP imagery courtesy of USDA Farm Service Agency"
access: Planetary Computer STAC (collection naip) and anonymous SAS token, no login; COG range reads
assets:
  - naip (asset image, 4 bands RGBN, Byte)
---

# NAIP

Leaf-on aerial imagery flown state by state every two to three years since 2010; Planetary Computer holds 2010 to 2023. Each item is one quarter quad (about 3.75' x 3.75') for one acquisition, a 4-band (red, green, blue, near infrared) Byte COG in the local UTM zone. The copy on AWS (`s3://naip-*`) is requester pays, so planetarble reads the Planetary Computer copy.

```yaml
overlays:
  - name: sf_naip
    source: naip
    aoi: {bbox: [-122.52, 37.70, -122.35, 37.83]}
    source_options: {year: 2022}   # optional; default keeps the newest per quarter quad
    min_zoom: 14
    max_zoom: 18
```

The adapter keeps RGB, reprojects to EPSG:3857 (an AOI may span UTM zones) and leaves values as they are. Neighbouring quarter quads from different years can differ in colour; pin `year` for a uniform look. Checked on 2026-11-07 over San Francisco.

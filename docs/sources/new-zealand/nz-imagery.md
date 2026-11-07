---
id: nz-imagery
title: New Zealand aerial and satellite imagery (Toitū Te Whenua LINZ)
status: supported (overlay)
coverage: New Zealand (per survey; national rural cover plus urban surveys)
resolution: 0.05 to 10 m, per survey
max_zoom: 21
license: CC BY 4.0 for the surveys planetarble accepts (licence and licensor are read from each STAC collection)
attribution: "Imagery: Toitū Te Whenua Land Information New Zealand and the survey licensors (CC BY 4.0)"
access: static STAC on the public S3 bucket s3://nz-imagery over HTTPS, no login; COG range reads
assets:
  - one STAC collection per survey (rgb and rgbnir, EPSG:2193), one COG item per map sheet
---

# New Zealand imagery

Toitū Te Whenua Land Information New Zealand publishes the country's publicly owned aerial and satellite imagery, down to 5 cm in some urban areas, as a static STAC catalog ([AWS registry](https://registry.opendata.aws/nz-imagery/), root `https://nz-imagery.s3.ap-southeast-2.amazonaws.com/catalog.json`, 895 collections on 2026-11-07). Each collection is one survey with its own licensor (usually a council), extent and capture dates; each item is a map sheet COG in NZTM2000 (EPSG:2193).

```yaml
overlays:
  - name: wellington
    source: nz-imagery
    aoi: {bbox: [174.76, -41.30, 174.80, -41.27]}
    # optional: pin a survey by its slug, e.g. wellington_2021_0.075m
    source_options: {collection: wellington_2021_0.075m}
    min_zoom: 14
    max_zoom: 20
```

Without `collection`, the adapter takes the RGB surveys that intersect the AOI, keeps current, completed photography (urban, rural and satellite; not scanned historical or draft surveys), and picks the finest among the surveys flown within `max_age_years` (default 5) of the newest one, then the newest of those, so a city's 7.5 cm urban survey wins over a 0.2 m regional survey flown a year later. It uses one survey per overlay; add overlays to combine surveys. Only CC BY 4.0 surveys are accepted. The credit written to the planet names the chosen survey and its licensor, e.g. "Imagery: Wellington 0.075m Urban Aerial Photos (2021), Wellington City Council, via Toitū Te Whenua Land Information New Zealand (CC BY 4.0)".

Planning reads the root catalog, every RGB collection (about 760) and the chosen survey's items, caching each under `data/cache/nz-imagery/` by checksum. On 2026-11-07 the first plan for central Wellington took about two minutes and a re-plan two seconds.

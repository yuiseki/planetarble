---
id: esa-worldcover
title: ESA WorldCover 10m annual composites (Sentinel-2 RGBNIR)
status: supported (overlay)
coverage: global land (1 x 1 degree tiles; none over open sea)
resolution: 10 m
max_zoom: 14
license: CC BY 4.0
attribution: "© ESA WorldCover project {year} / Contains modified Copernicus Sentinel data ({year}) processed by ESA WorldCover consortium"
access: public S3 bucket s3://esa-worldcover-s2 over HTTPS, no login; COG range reads
assets:
  - rgbnir/2020 (v100)
  - rgbnir/2021 (v200)
---

# ESA WorldCover Sentinel-2 RGBNIR composites

Yearly cloud-free median composites of Sentinel-2 bands B04, B03, B02 and B08 at 10 m, made by the ESA WorldCover project for 2020 and 2021 ([AWS registry](https://registry.opendata.aws/esa-worldcover-vito-composites/), [dataset README](https://github.com/ESA-WorldCover/esa-worldcover-datasets)). Each 1 x 1 degree tile is a 12000 x 12000 px COG in EPSG:4326, named by its south-west corner, e.g. `rgbnir/2021/N35/ESA_WorldCover_10m_2021_v200_N35E139_S2RGBNIR.tif`. Values are UInt16 reflectance x 10000 with 0 as nodata.

Use it as a 10 m layer over any land AOI:

```yaml
overlays:
  - name: kanto_10m
    source: esa-worldcover
    aoi: {bbox: [139.0, 35.0, 140.5, 36.5]}
    source_options: {year: 2021}   # optional: stretch_max (default 2000), gamma (default 0.7)
    min_zoom: 12
    max_zoom: 14
```

The adapter reads only the AOI window of each tile and stretches reflectance 0 to 0.2 to 8 bit with gamma 0.7 (a natural look over cities; raise `stretch_max` for bright desert or snow). The attribution `{year}` follows `source_options.year`. Checked on 2026-11-07 over Tokyo (N35E139).

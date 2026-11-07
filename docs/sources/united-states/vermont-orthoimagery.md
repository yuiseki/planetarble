---
id: cog:vermont-orthoimagery
title: Vermont statewide orthoimagery COGs (VCGI)
status: usable via source cog
coverage: Vermont, United States
resolution: 0.15 or 0.3 m (leaf-off), by year
max_zoom: 19
license: public domain with attribution
attribution: "Imagery: Vermont Center for Geographic Information (VCGI)"
access: one statewide COG per year and resolution on the public S3 bucket vtopendata-prd, no login
assets:
  - https://vtopendata-prd.s3.amazonaws.com/Imagery/2022_30cm_LeafOFF_4Band.tif
  - https://vtopendata-prd.s3.amazonaws.com/Imagery/2023_15cm_LeafOFF_4Band.tif
---

# Vermont orthoimagery

The Vermont Center for Geographic Information publishes statewide leaf-off orthoimagery as one COG per year and resolution (`Imagery/YYYY_15cm_LeafOFF_4Band.tif`, `..._30cm_...`), back to 1994 for panchromatic ([AWS registry](https://registry.opendata.aws/vt-opendata/), [folder README](https://vtopendata-prd.s3.amazonaws.com/Imagery/0A-README.txt)). The files are tens to hundreds of GB, JPEG-compressed with WebP overviews, in NAD83(2011) / Vermont; range reads fetch only the AOI. The README warns that treating background 0 as transparent shows JPEG edge artefacts, so `nodata: 0` leaves some fringes. Use with `source: cog` and the attribution above. Checked on 2026-11-07 (2022 30 cm file).

# ADR 0003: COG overlay sources beyond Japan, and attribution in built planets

- Status: Accepted (in progress)
- Date: 2026-11-07
- Deciders: yuiseki

## Implementation status

- Step 0, source documentation (done): SOURCE.md / SOURCE.ja.md split into one page per dataset under `docs/sources/{area}/{dataset}.md` with machine-readable front matter; a test checks every registry source has a page with the same zoom ceiling
- Step 1, attribution (done): SourceInfo.attribution (a template filled from source_options), overlay `attribution:` override, credits collected by build_planet and written to the planet metadata
- Step 2, shared COG warp (done): `overlay/cogwarp.py`
- Step 3, `esa-worldcover` (done): verified over Tokyo; sea tiles (404) are skipped at planning
- Step 4, `naip` (done): verified over San Francisco Civic Center (2022, 0.6 m)
- Step 5, `nz-imagery` (done): verified over central Wellington (2025 urban survey, 7.5 cm); credits decided while building (survey and licensor) flow into the planet attribution
- Step 6, generic `cog`: not started

## Context

ADR 0001 made overlays pluggable, but only OpenAerialMap, HLS and Sentinel-2 are wired into `planetarble build`, and high-resolution imagery outside OAM's scattered footprints was limited to Japan (GSI). A survey of the AWS Open Data Registry, the GeoAI-master-skills dataset references and cng-data-antigravity (2026-11-07) found openly licensed, login-free imagery that fits the same model:

| Source | Coverage | Resolution | Licence | Layout |
|---|---|---|---|---|
| ESA WorldCover S2 RGBNIR annual composite (`s3://esa-worldcover-s2`) | global | 10 m | CC BY 4.0 | 1 degree COG tiles named by the south-west corner, UInt16 reflectance x 10000 |
| NAIP on Microsoft Planetary Computer (`naip`) | contiguous US | 0.3 to 1 m | public domain | STAC search, COG per quarter quad, SAS-signed |
| New Zealand imagery (`s3://nz-imagery`) | New Zealand | 5 cm to 10 m | per survey (STAC collection), mostly CC BY 4.0 | static STAC, one collection per survey, EPSG:2193 |
| Canada, Vermont, Kentucky, Colorado aerial imagery on AWS | parts of North America | 15 to 50 cm | OGL Canada, public domain, CC0 | individual COGs or statewide mosaics, no common index |

Rejected: Maxar Open Data (CC BY-NC 4.0), NAIP on AWS (requester pays), ALOS AVNIR-2 ORI (login required; JAXA terms need prior contact for commercial use).

Separately, built planets carry no attribution at all: overlays are tiled with `attribution=""`, so even the CC BY OpenAerialMap imagery is published without credit.

## Decision

1. Attribution. Every registry source has a default attribution string; an overlay may override it with `attribution:` in the spec (required for the generic `cog` source). The orchestrator collects the base and overlay attributions in order, de-duplicated, and the executor writes them to the planet MBTiles metadata before PMTiles conversion, so `pmtiles show` and TileJSON consumers see them.
2. Shared COG warp. New sources only differ in how they find COG URLs for an AOI. A shared helper builds one `gdalwarp` command over `/vsicurl/` URLs (HTTP range reads, no whole-file download), clipped to the AOI, reprojected to EPSG:3857, with band selection and optional scaling to Byte, and an alpha band so nodata is transparent when composited.
3. Sources:
   - `esa-worldcover`: tile URLs computed from the AOI; bands 1-3 (B04, B03, B02) scaled from reflectance to Byte; ceiling z14.
   - `naip`: STAC search on Planetary Computer, newest acquisition per quarter quad, SAS token per collection (existing `fetch_sas_token`); bands 1-3; ceiling z18.
   - `nz-imagery`: walk the static STAC root, keep RGB collections whose extent intersects the AOI, choose the finest then newest, collect its intersecting items; licence and licensor read from the collection; ceiling z21.
   - `cog`: `source_options.urls` lists COG URLs; `attribution` is mandatory; bands and scaling optional; ceiling set per overlay (validated against the registry value z22).
4. Documentation. Each source gets `docs/sources/{area}/{dataset}.md` (area: global, japan, united-states, new-zealand, canada, ...) whose front matter records ID, coverage, resolution, max_zoom, licence, attribution and access; the indexes `docs/sources/README.md` and `README.ja.md` link every page and list sources that were considered and rejected.
5. OpenAerialMap is unchanged (it already works worldwide); it gains its default attribution.

## Consequences

- Planets can be sharp outside Japan without new download infrastructure.
- `/vsicurl/` reads depend on the remote staying up during a build; WorldCover and NAIP are large static stores, so this is acceptable. OAM keeps its whole-COG cache.
- Licences differ per overlay; the attribution list in the output is the record of what was used. Sources needing prior contact or non-commercial terms stay out of the registry.

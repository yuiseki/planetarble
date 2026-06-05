# Microsoft Planetary Computer rate limiting (observed behaviour)

When `planetarble process` / `planetarble build` stream imagery from Microsoft
Planetary Computer (MPC) — HLS COGs, Sentinel-2 L2A TCI, Landsat — the limiting
factor is **asset download throughput**, not the STAC search. This note records
how MPC's rate limiting actually behaves so future runs are planned accordingly.

## The key finding: MPC throttles by slowing down, not by blocking

MPC does **not** hard-block or reliably return HTTP 429 when you pull many large
assets. Instead it **throttles throughput**: the SAS-signed blob download drops
to a crawl for a few minutes, then recovers on its own. Downloads keep making
progress the whole time — they just get slow, then fast again. So a run never
"fails" from rate limiting; it just takes longer, and a naive timeout-based
guess at "it's stuck" would be wrong.

### Measured example (2026-06-05, `japan-sentinel2-build.yaml`)

Downloading a single Sentinel-2 L2A `*_TCI_10m.tif` (~313 MB) from MPC blob
storage, sampled once per minute:

| time     | speed      | note                          |
|----------|------------|-------------------------------|
| 14:13–15 | 420–730 KiB/s | normal                     |
| 14:16    | **68 KiB/s**  | throttled (ETA jumped to >1h) |
| 14:17    | 70 KiB/s   | throttled                     |
| 14:18    | 78 KiB/s   | throttled                     |
| 14:19    | 98 KiB/s   | recovering                    |
| 14:20    | 267 KiB/s  | recovering                    |
| 14:21–22 | 395–494 KiB/s | back to normal             |

A ~4-minute throttle window down to ~70 KiB/s, bracketed by ~400–700 KiB/s
normal throughput. No 429s, no broken connections — pure speed shaping.

## Why downloads dominate, and why caching matters

- Sentinel-2 `visual` (TCI) assets are whole MGRS-tile rasters (~300–600 MB
  each); HLS bands + Fmask are smaller but numerous. The pipeline downloads the
  **whole asset**, not just the AOI window, so per-AOI cost is dominated by TCI
  size × number of mosaic scenes (`sentinel2.mosaic_max_scenes`, default 3).
- Assets are cached under `data/cache/<source>/assets/`. Re-tiling, re-running,
  or building an overlapping AOI reuses the cache and never re-fetches — so the
  throttle is only paid once per unique asset. `aria2c` makes the downloads
  resumable, so a throttle window is never lost work.

## Throttle-interrupted downloads leave truncated cache files

A download that is killed (or the process exits) mid-throttle leaves a
**partial asset** in the cache next to an `aria2c` control file
(`<asset>.aria2`). The next run sees the file *exists* and reuses it, then dies
at read time with `TIFFReadEncodedTile() failed` / `IReadBlock failed`. Seen
2026-06-05: `T54SUE_20240705..._TCI_10m.tif` was 26 MB (vs a ~370 MB median) and
had a `.aria2` marker; the expanded-Tokyo mosaic selected that scene and crashed.

Detect and clean before reuse:

```bash
A=<data>/cache/sentinel2/assets/sentinel-2-l2a
# incomplete downloads (definitive): remove the partial file + its marker
find "$A" -name '*.aria2' | while read m; do rm -f "$m" "${m%.aria2}"; done
```

Size outliers alone are *not* a reliable signal: a TCI of a mostly-ocean / edge
MGRS granule (e.g. Sendai's `T54SVH`) is legitimately small (~40 MB) with no
`.aria2` marker. Trust the `.aria2` marker, not the size. (A future improvement
would verify asset integrity on cache reuse instead of trusting existence.)

## Operational guidance

- **Plan for slow windows, not failures.** Don't kill a run that has dropped to
  ~70 KiB/s; it is still progressing and will recover, usually within a few
  minutes. Only intervene if throughput stays floored for much longer than a
  single asset would take.
- **Grow coverage incrementally.** Add a few AOIs per run, let the cache fill,
  and space runs out if you are pulling many fresh MGRS tiles back to back.
- **Keep `mosaic_max_scenes` modest** (default 3): each extra scene is another
  whole-asset download for marginal mosaic benefit (the S2 path is a VRT mosaic,
  not a median, so more scenes mostly add download cost).
- **Future optimisation:** windowed download (fetch only the AOI bbox via
  `/vsicurl` + `gdalwarp -te`, or a Range-based COG read) would cut transfer
  dramatically for small AOIs, at the cost of losing the whole-asset cache that
  makes re-tiling free. Worth it for many small disjoint AOIs; not for repeated
  builds over the same area.

## Aggregate cost of a 4-AOI build (2026-06-05, `japan-sentinel2-build.yaml`)

BMNG floor + four land-only Sentinel-2 z14 city overlays (Tokyo 23 wards,
Sendai, Hiroshima, Morioka), Tokyo's assets already cached:

| metric | value |
|--------|-------|
| wall-clock | **~88 min** (dominated by throttled TCI downloads) |
| throttle snapshots (2-digit KiB/s) | 771 progress prints |
| mosaic scenes per AOI | 3 (`mosaic_max_scenes`), chosen lowest-cloud from 22–34 candidates the STAC search returned |
| fresh TCI assets pulled | ~9 (3 new cities × 3 scenes, some MGRS tiles shared) |
| `data/cache/sentinel2` growth | 18 GB → 20 GB (**+2 GB**) |
| `/data` total growth | 948 GB → 951 GB (**+3 GB**) |
| output PMTiles | ~199 MB (z0–14) |

**Takeaway: disk is not the constraint, wall-clock is.** Four city AOIs at z14
cost only ~3 GB on disk but ~88 minutes, almost all of it waiting out MPC
throttle windows on ~330 MB TCI downloads. Budget time, not space, and grow
coverage a few AOIs per run.

See also: `docs/operations/hls-japan-plan.md` for HLS-specific incremental
planning, and the Sentinel-2 recipe `configs/profiles/sentinel2-tokyo-z14.yaml`.

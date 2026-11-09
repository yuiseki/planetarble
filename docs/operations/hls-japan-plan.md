# Japan HLS Plan Strategy

This note describes a reproducible, rate-limit-friendly strategy for building HLS coverage over Japan.

## 1) Prefer deterministic regions

Use `hls.plan_regions` to split the work into deterministic chunks and process them one at a time.
For Japan, the most reliable split is **Natural Earth admin_1 prefectures**.

To list available prefecture names (for exact `where` clauses):

```bash
ogrinfo /vsizip/path/to/ne_10m_admin_1_states_provinces.zip \
  -sql "SELECT name FROM ne_10m_admin_1_states_provinces WHERE adm0_a3='JPN'"
```

## 2) Example plan_regions (prefecture-level)

Use one region per prefecture to keep each run small and resumable:

```yaml
hls:
  plan_regions:
    - name: "tokyo"
      natural_earth:
        dataset: "admin_1"
        where: "adm0_a3='JPN' AND name='Tokyo'"
      land_only: true
    - name: "osaka"
      natural_earth:
        dataset: "admin_1"
        where: "adm0_a3='JPN' AND name='Ōsaka'"
      land_only: true
```

Note: spellings must match Natural Earth exactly (for example `Ōsaka`, `Kyōto` and `Hyōgo` carry macrons). Use the `ogrinfo` query above to confirm names. `configs/profiles/hls-regional-z11.yaml` already defines the prefectures of Kanto, Chubu and Kinki as `<prefecture>_land` regions.

## 3) Suggested execution order

Start with smaller regions to validate the pipeline, then move to larger ones:

1. Tokyo, Osaka, Kanagawa, Aichi
2. Remaining Kanto and Kansai prefectures
3. Chubu, Chugoku, Shikoku
4. Tohoku, Hokkaido, Kyushu, Okinawa

This order provides early feedback while steadily increasing coverage.

## 4) Rate-limit mitigation

- Run **one region at a time**; avoid parallel processes.
- Keep `hls.cache_ttl_days` high and avoid `--force` unless necessary.
- Prefer a single seasonal window for Japan (e.g., April–October) to reduce requests.
- If you hit 403s, pause and resume later: cached assets are reused and corrupted files are quarantined automatically.

## 5) Operational workflow

Relative directories in a config resolve beside the config file, so copy the profile into your workspace first and run from there.

```bash
cp configs/profiles/hls-regional-z11.yaml workspace/ && cd workspace
planetarble acquire --config hls-regional-z11.yaml --plan-region tokyo_land
planetarble process --config hls-regional-z11.yaml --plan-region tokyo_land
planetarble tile --config hls-regional-z11.yaml --plan-region tokyo_land --min-zoom 11 --max-zoom 11
```

Repeat per prefecture, then merge MBTiles and package.

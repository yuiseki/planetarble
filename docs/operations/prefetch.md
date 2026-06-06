# Prefetch: warming the Sentinel-2 cache (and riding out MPC outages)

`planetarble prefetch` downloads the imagery a spec needs into the asset cache
**without tiling**, so a later `planetarble build` over the same AOIs is
download-free (just tiling, ~hours instead of days). Downloads are the slow,
flaky, throttle-bound part (see `mpc-rate-limits.md`); separating them lets you
warm the cache unattended (overnight / while the line is idle) and build later.

## Command

```bash
planetarble prefetch --spec configs/overlays/japan-prefetch.yaml \
  --pace-min 60 --pace-max 300 \
  --throttle-floor 150 --cooldown-min 600 --cooldown-max 900 \
  --recovery-wait 1800 --max-recovery-rounds 6
```

- It iterates the spec's overlays and, for each `sentinel2` overlay, fetches the
  `mosaic_max_scenes` lowest-cloud scenes' assets into
  `data/cache/sentinel2/assets/`. Non-sentinel2 overlays are skipped.
- `--dry-run` lists the overlays that would be fetched and exits (no network).
- Idempotent and resumable: cached assets are skipped (`hits`); a partial
  download resumes via `aria2c --continue` (the `.aria2` control file — **do not
  delete it**, see `mpc-rate-limits.md`).

### Pacing (throttle-aware)

MPC throttles by slowing down, not blocking. After each tile, prefetch waits:

- a short random **jitter** (`--pace-min`/`--pace-max`) when throughput was
  healthy,
- a longer random **cooldown** (`--cooldown-min`/`--cooldown-max`) when the tile
  came down below `--throttle-floor` KiB/s (treated as throttled),
- nothing when the tile was fully cached (no bytes fetched).

## Resilience: two layers (this is the watcher, in code)

A long unattended prefetch *will* hit MPC hiccups. Two built-in layers handle
them so the run does not need babysitting:

1. **Per-request retry** (in the STAC client): a transient
   `APIError: request exceeded the maximum allowed time` or client timeout on a
   single search is retried `sentinel2.max_retries` times with backoff.
2. **Recovery rounds** (`--max-recovery-rounds`, default 6): when MPC has a
   *broad* outage and a whole overlay still fails after its per-request retries,
   the still-failed overlays are retried in later rounds, waiting
   `--recovery-wait` seconds (default 1800) between rounds for MPC to recover.
   Already-fetched overlays are cached, so each round only re-attempts the
   failures. Set `--max-recovery-rounds 1` to disable (fail fast).

This is the in-tool equivalent of the earlier external "wait for MPC, then
resume" shell watcher — no external babysitter needed.

### Telling an MPC outage apart from a heavy query

If a search times out, check whether it is *your query* or *MPC*: run the same
search with a narrower date range / no cloud filter, and a known-good AOI. If
**all** of them still fail at the same ~30 s, it is a broad MPC STAC outage (the
server-side gateway timeout), not query cost — wait it out (recovery rounds do
this). Observed 2026-06-06: every query failed at ~30 s for ~30 min, then
recovered; narrowing the query did not help, confirming it was MPC-side.

## Running it unattended

A dedicated `tmux` session survives SSH drops and is inspectable:

```bash
tmux new-session -d -s planetarble-prefetch \
  "cd <repo> && PYTHONPATH=src python -u -m planetarble.cli.main prefetch \
     --spec configs/overlays/japan-prefetch.yaml --max-recovery-rounds 12 \
     > /tmp/prefetch.log 2>&1"
# inspect:  tmux attach -t planetarble-prefetch   /   tail -f /tmp/prefetch.log
```

For scheduled warming, a nightly cron of the same command is safe: it is
idempotent (cached AOIs skip fast) and resumable, so it only fetches what is
still missing.

> z-t gotcha: never `pkill -f "...prefetch"` to stop it — the pattern matches
> pkill's own command line and kills your SSH session. Use the bracket trick:
> `pkill -f "[c]li.main prefetch"`, or `tmux kill-session -t planetarble-prefetch`.

## Priority spec

`configs/overlays/japan-prefetch.yaml` warms 10 priority population centres
(Osaka, Kyoto, Kobe, Nagoya, Kanazawa, Niigata, Okayama, Fukuoka, Kumamoto,
Nagasaki) at `mosaic_max_scenes=3`. Each AOI is a city-core box kept inside one
MGRS granule so ≥3 low-cloud scenes cover it; Osaka/Kyoto/Kobe share tile
T53SNU, so their TCIs are cached once. Once warm, build these AOIs download-free.

See also: `docs/operations/mpc-rate-limits.md`.

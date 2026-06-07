# GSI seamlessphoto collection node (uv)

How to turn any host into a GSI tile-collection node and split the work across
several hosts/IPs (so no single IP hammers GSI). Uses [uv](https://docs.astral.sh/uv/)
for a reproducible environment — no system pip or conda required.

## Why multiple hosts

High zooms are large (z17 ≈ 7M tiles, z18 ≈ 28M). Splitting the country into
four **Quadrans** regions (UNopenGIS/7#909) lets several collaborators each
collect one region, from a different IP, and then merge the pieces. PMTiles
cannot be merged, so we keep everything in **MBTiles** until the final convert.

## Setup (one-time, per host)

```sh
# install uv if absent (standalone installer, no pip needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/yuiseki/planetarble && cd planetarble
uv venv --python 3.13
uv pip install -e .          # installs deps from pyproject (pinned in uv.lock)

uv run planetarble gsi-collect --help   # sanity check
```

Only `requests` + stdlib `sqlite3` are needed for collection — GDAL is **not**
required on a collection node (it is only used for raster processing / tiling).

## Collect one Quadrans region, straight into MBTiles

```sh
# e.g. this host takes "west"; resume-safe (existing tiles in the mbtiles are skipped)
uv run planetarble gsi-collect \
  --layer seamlessphoto --zoom-min 17 --zoom-max 18 \
  --quadrans west \
  --mbtiles /path/on/fast/disk/seamlessphoto_z17-18_west.mbtiles \
  --workers 10 \
  --name "GSI seamlessphoto z17-18 west" \
  --attribution "国土地理院 シームレス空中写真 (GSI seamlessphoto) CC BY 4.0"
```

- `--quadrans {north,east,south,west}` filters the mokuroku catalog to your
  region (classified by each tile's centre; see `tiling/quadrans.py`).
- `--mbtiles` writes tiles directly into the archive — no millions of small
  files, resumable. Put it on a fast disk (NVMe); spinning disks are painfully
  slow for the sqlite write + any later read.
- Inspect the split first with `--dry-run` to see per-zoom counts for your region.

### Region balance (z17+z18, seamlessphoto)

| region | tiles | GB |
|---|--:|--:|
| north | 8.47M | 128 |
| east  | 11.63M | 176 |
| west  | 8.54M | 131 |
| south | 6.59M | 94 |

`east` (Tokyo) is heaviest, `south` (Kyushu) lightest. Pair regions to balance
two collaborators, e.g. `east+south` vs `north+west`.

## Merge the pieces and publish

```sh
# union the disjoint regional archives in one pass (on the host doing the merge)
uv run planetarble tiling union-mbtiles \
  --inputs north.mbtiles east.mbtiles west.mbtiles south.mbtiles \
  --out all_z17-18.mbtiles

# convert once, then slice cheaply with pmtiles extract
pmtiles convert all_z17-18.mbtiles all_z17-18.pmtiles
pmtiles extract all.pmtiles z18.pmtiles --minzoom 18 --maxzoom 18
pmtiles extract all.pmtiles tokyo.pmtiles --bbox 139.5,35.5,140.0,35.9
```

Receivers can also `pmtiles extract` directly from a remote URL over HTTP range,
pulling only the zooms/area they need — no client-side merge required.

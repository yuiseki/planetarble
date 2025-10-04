# PMTiles Tiling Pipeline

The `planetarble tiling pmtiles` command converts a geospatial raster (GeoTIFF, COG, etc.) into a PMTiles archive using a three-stage toolchain:

1. **GDAL** `gdal raster tile` reprojects and slices the raster directly into a Web Mercator XYZ directory.
2. **mb-util** repackages the XYZ directory into an MBTiles file while embedding metadata.
3. **go-pmtiles** converts the MBTiles archive into a PMTiles container and optionally runs integrity checks.

This approach avoids the massive in-memory VRT used by the legacy pipeline and keeps each step streaming-friendly, enabling much faster builds on multi-core hosts.

## Requirements

- Python 3.10+
- GDAL ≥ 3.11 with the `gdal raster tile` subcommand
- `mb-util` CLI on the `PATH`
- `pmtiles` (go-pmtiles) CLI on the `PATH`
- Adequate temporary storage for tiled outputs; the command creates a working directory under `--out/tmp`

## Usage

```shell
planetarble tiling pmtiles \
  --input data/planet_blend_2024.tif \
  --out output/pmtiles \
  --min-zoom 0 \
  --max-zoom 12 \
  --format jpg \
  --quality 80 \
  --resampling cubic \
  --name "Planetarble 2024" \
  --attribution "Imagery: NASA BMNG + GEBCO" \
  --bounds-mode auto
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--input` | Source raster (GeoTIFF/COG) to tile. |
| `--out` | Destination directory for intermediate and final artifacts. |
| `--min-zoom` / `--max-zoom` | Tile pyramid zoom range (defaults to config values). |
| `--format` | Tile format (`png`, `jpg`, or `webp`). |
| `--quality` | JPEG/WEBP compression quality (ignored for PNG). |
| `--resampling` | Resampling kernel for GDAL tiling (`cubic`, `lanczos`, etc.). |
| `--name` / `--attribution` | Metadata embedded in MBTiles/PMTiles headers. |
| `--bounds-mode` | `auto` (derive from raster) or `global` (Web Mercator envelope). |
| `--no-deduplication` | Disable PMTiles deduplication during conversion (reduces RAM usage). |
| `--cluster` | Run `pmtiles cluster` post-conversion to optimise internal layout. |
| `--temp-dir` | Optional scratch directory (defaults to `<out>/tmp`). |
| `--dry-run` | Print commands without executing them. |

## Outputs

Running the command produces:

- A temporary XYZ directory under `<temp_dir>/<basename>_{zrange}_zxy/`
- An intermediate MBTiles archive in `<temp_dir>/<basename>_{zrange}.mbtiles`
- The final PMTiles archive in `<out>/<basename>_{zrange}.pmtiles`
- A `metadata.json` file persisted alongside the XYZ tiles for mb-util ingestion

## Tuning Tips

- Set `GDAL_NUM_THREADS` and `GDAL_CACHEMAX` via `ProcessingConfig` to match available CPU and RAM. The default values (`ALL_CPUS`, `50%`) aim for aggressive parallelism without exhausting memory.
- JPEG is fastest for opaque imagery. Prefer WEBP for sharper visual quality at the cost of encoding time. Use PNG only when transparency is required.
- For extremely large rasters, mount the temporary directory on fast NVMe storage to minimise I/O stalls.
- If `pmtiles convert` runs out of memory, retry with `--no-deduplication` (deduplication is the most memory-intensive step).
- The pipeline retries `gdal raster tile` with `bilinear` resampling if the requested kernel fails; adjust `--resampling` manually for additional control.

## Verification

After conversion, the command automatically executes `pmtiles verify` and prints the PMTiles header (`pmtiles show --header-json`). You can also run:

```shell
pmtiles tile output/pmtiles/planet_2024_0-12.pmtiles 2 1 1 > tile.jpg
pmtiles serve output/pmtiles
```

to inspect individual tiles or view the tileset in MapLibre GL via the built-in static server.

## Limitations

- Requires GDAL 3.11+; earlier releases do not ship the `gdal raster tile` subcommand.
- Metadata bounds default to the raster extent in WGS84; for cropped datasets ensure reprojected bounds suit your application.
- Very large zoom ranges (≥12) will generate tens of millions of tiles; plan temporary disk usage accordingly.
- `bounds-mode` currently supports only `auto` and `global`. Future work may add manual bounds or mask-aware clipping.

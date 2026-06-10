"""Utilities for manipulating MBTiles archives."""

from __future__ import annotations

import io
import queue
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, Mapping, Optional, Set, Tuple

_PIL_SAVE_FORMAT = {"webp": "WEBP", "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}


def iter_xyz_dir(tile_dir: Path) -> Iterator[Tuple[int, int, int, str]]:
    """Yield ``(z, x, y, ext)`` for every ``z/x/y.ext`` tile under ``tile_dir``.

    Non-numeric entries (e.g. a ``metadata.json`` sidecar or a stray file) are
    skipped, so the directory can hold things other than tiles.
    """
    tile_dir = Path(tile_dir)
    for zdir in tile_dir.iterdir():
        if not zdir.is_dir() or not zdir.name.isdigit():
            continue
        z = int(zdir.name)
        for xdir in zdir.iterdir():
            if not xdir.is_dir() or not xdir.name.isdigit():
                continue
            x = int(xdir.name)
            for yfile in xdir.iterdir():
                stem, _, ext = yfile.name.partition(".")
                if not stem.isdigit():
                    continue
                yield z, x, int(stem), ext


def _init_mbtiles(conn: sqlite3.Connection) -> None:
    # WAL + relaxed sync: this is a bulk write of millions of small blobs; we
    # favour throughput over crash-durability (the source dir is the truth and
    # the ingest is restartable via INSERT OR REPLACE).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (name text, value text)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tiles "
        "(zoom_level integer, tile_column integer, tile_row integer, tile_data blob)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS tile_index "
        "ON tiles (zoom_level, tile_column, tile_row)"
    )


def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    if conn.execute("UPDATE metadata SET value=? WHERE name=?", (value, key)).rowcount == 0:
        conn.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (key, value))


def _load_existing_keys(conn: sqlite3.Connection) -> Set[Tuple[int, int, int]]:
    """Return the set of XYZ ``(z, x, y)`` tiles already stored (TMS -> XYZ)."""
    keys: Set[Tuple[int, int, int]] = set()
    for z, x, row in conn.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"):
        keys.add((z, x, (1 << z) - 1 - row))
    return keys


class MbtilesSink:
    """Thread-safe tile sink writing straight into an MBTiles archive.

    Worker threads call the sink with ``(z, x, y, content)``; a single dedicated
    writer thread drains a bounded queue and commits batched
    ``INSERT OR REPLACE`` statements, so sqlite only ever sees one writer. This
    lets a parallel downloader write directly into MBTiles with no intermediate
    ``z/x/y`` files (no millions of inodes, no read-back pass).

    Pre-loaded existing keys (``contains``) make re-runs resumable. Use as a
    context manager so the writer thread is started and flushed deterministically.
    """

    def __init__(
        self,
        mbtiles_path: Path,
        *,
        tile_format: str = "jpg",
        batch_size: int = 10000,
        metadata: Optional[Mapping[str, str]] = None,
        queue_maxsize: int = 20000,
    ) -> None:
        self.path = Path(mbtiles_path)
        self.tile_format = tile_format
        self.batch_size = batch_size
        self.metadata = dict(metadata or {})
        self._q: "queue.Queue[Optional[Tuple[int, int, int, bytes]]]" = queue.Queue(maxsize=queue_maxsize)
        self._existing: Set[Tuple[int, int, int]] = set()
        self._thread: Optional[threading.Thread] = None
        self.written = 0

    def __enter__(self) -> "MbtilesSink":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # init schema + snapshot existing keys on the calling thread, then hand
        # the file to a dedicated writer thread (sqlite connections are per-thread)
        conn = sqlite3.connect(str(self.path))
        try:
            _init_mbtiles(conn)
            conn.commit()
            self._existing = _load_existing_keys(conn)
        finally:
            conn.close()
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()
        return self

    def contains(self, z: int, x: int, y: int) -> bool:
        return (z, x, y) in self._existing

    def __call__(self, z: int, x: int, y: int, content: bytes) -> None:
        self._q.put((z, x, y, content))

    def _writer(self) -> None:
        conn = sqlite3.connect(str(self.path))
        batch = []

        def flush() -> None:
            if not batch:
                return
            conn.executemany(
                "INSERT OR REPLACE INTO tiles "
                "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                batch,
            )
            conn.commit()
            self.written += len(batch)
            batch.clear()

        while True:
            item = self._q.get()
            if item is None:
                flush()
                break
            z, x, y, content = item
            batch.append((z, x, (1 << z) - 1 - y, content))
            if len(batch) >= self.batch_size:
                flush()

        _set_metadata(conn, "format", self.tile_format)
        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        if min_zoom is not None:
            _set_metadata(conn, "minzoom", str(min_zoom))
        if max_zoom is not None:
            _set_metadata(conn, "maxzoom", str(max_zoom))
        for key, value in self.metadata.items():
            _set_metadata(conn, key, str(value))
        conn.commit()
        conn.close()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._q.put(None)
        if self._thread is not None:
            self._thread.join()


def download_xyz_to_mbtiles(
    triplets: Iterable[Tuple[int, int, int]],
    *,
    mbtiles_path: Path,
    template: str,
    ext: str = "jpg",
    tile_format: Optional[str] = None,
    workers: int = 10,
    batch_size: int = 10000,
    metadata: Optional[Mapping[str, str]] = None,
    **download_kwargs,
):
    """Download ``(z, x, y)`` tiles straight into an MBTiles (no intermediate files).

    Wires a :class:`MbtilesSink` into ``download_xyz_tiles``: parallel workers
    fetch over the network and a single writer thread persists batched tiles.
    Resumable — tiles already in the archive are skipped. Returns the downloader
    stats. ``**download_kwargs`` are forwarded to ``download_xyz_tiles``.
    """
    from planetarble.acquisition.tiles import download_xyz_tiles

    fmt = tile_format or ext
    with MbtilesSink(
        mbtiles_path, tile_format=fmt, batch_size=batch_size, metadata=metadata
    ) as sink:
        stats = download_xyz_tiles(
            triplets,
            template=template,
            ext=ext,
            workers=workers,
            sink=sink,
            is_cached=sink.contains,
            **download_kwargs,
        )
    return stats


def ingest_xyz_dir(
    tile_dir: Path,
    mbtiles_path: Path,
    *,
    tile_format: str = "jpg",
    batch_size: int = 10000,
    metadata: Optional[Mapping[str, str]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> int:
    """Ingest a ``z/x/y.ext`` directory into an MBTiles archive (create or append).

    Tiles are stored TMS (``tile_row = 2**z - 1 - y``) per the MBTiles spec,
    batched with ``executemany`` and ``INSERT OR REPLACE`` so re-ingesting is
    idempotent and re-running picks up new tiles. ``minzoom``/``maxzoom`` are
    recomputed from the full table after writing; ``format`` plus any extra
    ``metadata`` keys are set. Returns the number of tiles written.

    Unlike ``mb-util`` this emits no per-tile logging and uses one batched
    transaction stream, so packing millions of tiles is markedly faster.
    """
    tile_dir = Path(tile_dir)
    mbtiles_path = Path(mbtiles_path)
    mbtiles_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(mbtiles_path))
    try:
        _init_mbtiles(conn)
        written = 0
        batch = []
        for z, x, y, _ext in iter_xyz_dir(tile_dir):
            data = (tile_dir / str(z) / str(x) / f"{y}.{_ext}").read_bytes()
            tms_row = (1 << z) - 1 - y
            batch.append((z, x, tms_row, data))
            if len(batch) >= batch_size:
                conn.executemany(
                    "INSERT OR REPLACE INTO tiles "
                    "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                    batch,
                )
                conn.commit()
                written += len(batch)
                batch = []
                if on_progress is not None:
                    on_progress(written)
        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO tiles "
                "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                batch,
            )
            conn.commit()
            written += len(batch)
            if on_progress is not None:
                on_progress(written)

        _set_metadata(conn, "format", tile_format)
        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        if min_zoom is not None:
            _set_metadata(conn, "minzoom", str(min_zoom))
        if max_zoom is not None:
            _set_metadata(conn, "maxzoom", str(max_zoom))
        for key, value in (metadata or {}).items():
            _set_metadata(conn, key, str(value))
        conn.commit()
    finally:
        conn.close()
    return written


def merge_mbtiles(
    base_path: Path,
    overlay_path: Path,
    *,
    destination: Optional[Path] = None,
) -> Path:
    """Copy base MBTiles and overlay tiles from another MBTiles archive."""

    if not base_path.exists():
        raise FileNotFoundError(f"Base MBTiles not found: {base_path}")
    if not overlay_path.exists():
        raise FileNotFoundError(f"Overlay MBTiles not found: {overlay_path}")
    output_path = destination or base_path.with_name(f"{base_path.stem}_merged{base_path.suffix}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    shutil.copy2(base_path, output_path)

    with sqlite3.connect(str(output_path)) as conn:
        conn.execute(f"ATTACH DATABASE '{overlay_path}' AS overlay")
        conn.execute(
            """
            DELETE FROM tiles
            WHERE (zoom_level, tile_column, tile_row) IN (
                SELECT zoom_level, tile_column, tile_row FROM overlay.tiles
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data)
            SELECT zoom_level, tile_column, tile_row, tile_data
            FROM overlay.tiles
            """
        )
        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        meta_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone()
        if meta_exists:
            for key, value in (("minzoom", min_zoom), ("maxzoom", max_zoom)):
                if value is None:
                    continue
                updated = conn.execute(
                    "UPDATE metadata SET value=? WHERE name=?",
                    (str(value), key),
                ).rowcount
                if updated == 0:
                    conn.execute(
                        "INSERT INTO metadata (name, value) VALUES (?, ?)",
                        (key, str(value)),
                    )
        conn.commit()
        conn.execute("DETACH DATABASE overlay")
    return output_path


def union_mbtiles(
    inputs: "Iterable[Path]",
    destination: Path,
    *,
    tile_format: Optional[str] = None,
    metadata: Optional[Mapping[str, str]] = None,
    chunk_size: int = 50_000,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Path:
    """Union several MBTiles archives into one (for disjoint pieces, e.g. Quadrans).

    The **first** input is copied verbatim as the base (a fast file copy, no
    row-by-row SQL), then every remaining input is appended in ``chunk_size``-row
    transactions. ``INSERT OR IGNORE`` keeps it safe even if pieces happen to
    overlap — the first input wins, exactly as before. Two properties matter at
    scale (hundreds of GB):

    * **No re-insert of the biggest set** — pass the largest piece first and its
      tiles arrive via ``shutil.copy2`` instead of millions of INSERTs.
    * **Bounded journal** — each chunk commits under ``journal_mode=DELETE``, so
      the rollback journal never grows past one chunk (a single giant
      transaction would balloon the journal to ~output size and lose everything
      on failure). The source pieces remain the truth, so the run is restartable.

    ``minzoom``/``maxzoom`` are recomputed from the result; ``format`` and
    ``name``/``attribution``/``bounds`` are carried from the first input unless
    overridden via ``tile_format`` / ``metadata``. ``on_progress`` (if given) is
    called with the running inserted-row count after each committed chunk.
    """
    inputs = [Path(p) for p in inputs]
    if not inputs:
        raise ValueError("union_mbtiles requires at least one input")
    for p in inputs:
        if not p.exists():
            raise FileNotFoundError(f"MBTiles not found: {p}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    base_meta: Dict[str, str] = {}
    c0 = sqlite3.connect(str(inputs[0]))
    try:
        if c0.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone():
            base_meta = dict(c0.execute("SELECT name, value FROM metadata"))
    finally:
        c0.close()

    # Copy the first input as the base instead of re-inserting it row by row.
    shutil.copy2(inputs[0], destination)

    conn = sqlite3.connect(str(destination))
    try:
        # The copied base already carries the schema, but a foreign archive might
        # lack the unique index OR IGNORE relies on; ensure it (idempotent on ours).
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (name text, value text)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tiles "
            "(zoom_level integer, tile_column integer, tile_row integer, tile_data blob)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS tile_index "
            "ON tiles (zoom_level, tile_column, tile_row)"
        )
        # Bound the journal to one chunk; sources are the truth so durability is moot.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=OFF")

        inserted = 0
        for src in inputs[1:]:
            # Read from a separate connection so committing the writer never
            # disturbs the open source cursor.
            rconn = sqlite3.connect(str(src))
            try:
                cur = rconn.execute(
                    "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
                )
                while True:
                    batch = cur.fetchmany(chunk_size)
                    if not batch:
                        break
                    conn.executemany(
                        "INSERT OR IGNORE INTO tiles "
                        "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                        batch,
                    )
                    conn.commit()
                    inserted += len(batch)
                    if on_progress is not None:
                        on_progress(inserted)
            finally:
                rconn.close()

        _set_metadata(conn, "format", tile_format or base_meta.get("format", "jpg"))
        for key in ("name", "attribution", "bounds"):
            if key in base_meta:
                _set_metadata(conn, key, base_meta[key])
        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        if min_zoom is not None:
            _set_metadata(conn, "minzoom", str(min_zoom))
        if max_zoom is not None:
            _set_metadata(conn, "maxzoom", str(max_zoom))
        for key, value in (metadata or {}).items():
            _set_metadata(conn, key, str(value))
        conn.commit()
    finally:
        conn.close()
    return destination


def composite_mbtiles(
    base_path: Path,
    overlay_path: Path,
    destination: Path,
    *,
    tile_format: str = "webp",
    quality: int = 85,
) -> Path:
    """Alpha-composite an overlay MBTiles over a base, painting finer over coarser.

    Unlike ``merge_mbtiles`` (which replaces whole tile blobs), this decodes each
    tile and composites the overlay over the base per pixel, so an overlay's
    transparent nodata lets the base show through and its imagery paints over
    the base only where it actually has data. Every tile is re-encoded to
    ``tile_format`` so the output is uniform (required for a single PMTiles
    tile type). Overlay tiles with no base counterpart (deeper zooms) are
    inserted as-is.
    """
    from PIL import Image  # local import; Pillow only needed for compositing

    if not base_path.exists():
        raise FileNotFoundError(f"Base MBTiles not found: {base_path}")
    if not overlay_path.exists():
        raise FileNotFoundError(f"Overlay MBTiles not found: {overlay_path}")
    pil_format = _PIL_SAVE_FORMAT.get(tile_format.lower())
    if pil_format is None:
        raise ValueError(f"Unsupported tile_format: {tile_format}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.copy2(base_path, destination)

    def _encode(image) -> bytes:
        buf = io.BytesIO()
        if pil_format == "JPEG":
            image = image.convert("RGB")
        image.save(buf, format=pil_format, quality=quality)
        return buf.getvalue()

    with sqlite3.connect(str(destination)) as conn:
        conn.execute(f"ATTACH DATABASE '{overlay_path}' AS overlay")
        overlay_tiles: Dict[Tuple[int, int, int], bytes] = {
            (z, x, y): data
            for z, x, y, data in conn.execute(
                "SELECT zoom_level, tile_column, tile_row, tile_data FROM overlay.tiles"
            )
        }

        canonical_size: Optional[Tuple[int, int]] = None
        updates = []
        for z, x, y, bdata in conn.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
        ):
            base_img = Image.open(io.BytesIO(bdata)).convert("RGBA")
            if canonical_size is None:
                canonical_size = base_img.size
            key = (z, x, y)
            odata = overlay_tiles.pop(key, None)
            if odata is not None:
                ov_img = Image.open(io.BytesIO(odata)).convert("RGBA")
                # tile sizes can differ (e.g. 512 base vs 256 overlay); match the base
                if ov_img.size != base_img.size:
                    ov_img = ov_img.resize(base_img.size, Image.Resampling.LANCZOS)
                out_img = Image.alpha_composite(base_img, ov_img)
            else:
                out_img = base_img
            updates.append((z, x, y, _encode(out_img)))

        # overlay-only tiles (deeper zooms with no base) inserted re-encoded,
        # normalized to the planet's canonical tile size
        for (z, x, y), odata in overlay_tiles.items():
            ov_img = Image.open(io.BytesIO(odata)).convert("RGBA")
            if canonical_size is not None and ov_img.size != canonical_size:
                ov_img = ov_img.resize(canonical_size, Image.Resampling.LANCZOS)
            updates.append((z, x, y, _encode(ov_img)))

        # ``updates`` is the complete final tile set (every base tile re-encoded
        # plus overlay-only tiles), so rewrite the table rather than relying on
        # a unique constraint for INSERT OR REPLACE.
        conn.execute("DELETE FROM tiles")
        conn.executemany(
            "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
            updates,
        )

        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone():
            for key, value in (("format", tile_format), ("minzoom", min_zoom), ("maxzoom", max_zoom)):
                if value is None:
                    continue
                if conn.execute("UPDATE metadata SET value=? WHERE name=?", (str(value), key)).rowcount == 0:
                    conn.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.execute("DETACH DATABASE overlay")
    return destination


def _xyz_tile(lon: float, lat: float, z: int) -> Tuple[int, int]:
    import math

    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    lat = max(min(lat, 85.0511287798066), -85.0511287798066)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(x, n - 1)), max(0, min(y, n - 1))


def fetch_tile_overzoom(conn, z: int, x: int, y: int, *, tile_size: int = 256, tms: bool = True):
    """Return tile (z,x,y) as an RGBA image, upscaling an ancestor if absent.

    Coordinates are XYZ; MBTiles store TMS rows (row = 2**z - 1 - y) by default,
    converted at the SQL boundary. Returns None when no ancestor has data, so
    callers can leave a hole for the viewer to overzoom instead of baking a
    transparent tile.
    """
    from PIL import Image

    for zz in range(z, -1, -1):
        d = z - zz
        ax, ay = x >> d, y >> d
        srow = ((1 << zz) - 1 - ay) if tms else ay
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (zz, ax, srow),
        ).fetchone()
        if row is None:
            continue
        img = Image.open(io.BytesIO(row[0])).convert("RGBA")
        if d == 0:
            return img if img.size == (tile_size, tile_size) else img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
        factor = 1 << d
        w, h = img.size
        sub_w, sub_h = w // factor, h // factor
        ox = (x - (ax << d)) * sub_w
        oy = (y - (ay << d)) * sub_h
        crop = img.crop((ox, oy, ox + sub_w, oy + sub_h))
        return crop.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
    return None


def composite_overzoom(
    sources,
    destination: Path,
    *,
    aoi_bbox: Tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
    tile_format: str = "webp",
    quality: int = 85,
    tile_size: int = 256,
    tms: bool = True,
) -> Path:
    """Build a stacked planet over an AOI, filling lower sources by overzoom.

    ``sources`` are mbtiles paths ordered bottom to top (e.g. BMNG, HLS, OAM).
    For every output tile in the AOI at each zoom, each source contributes its
    tile or an upscaled ancestor, composited in order, so the finest source is
    on top and lower sources fill underneath (no holes). High zooms are bounded
    to the AOI bbox to keep tile counts feasible.
    """
    from PIL import Image

    pil_format = _PIL_SAVE_FORMAT.get(tile_format.lower())
    if pil_format is None:
        raise ValueError(f"Unsupported tile_format: {tile_format}")
    conns = [sqlite3.connect(str(p)) for p in sources]
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        out = sqlite3.connect(str(destination))
        out.execute("CREATE TABLE metadata (name text, value text)")
        out.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob)")
        out.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")

        minx, miny, maxx, maxy = aoi_bbox
        written = 0
        for z in range(min_zoom, max_zoom + 1):
            x0, y0 = _xyz_tile(minx, maxy, z)  # NW
            x1, y1 = _xyz_tile(maxx, miny, z)  # SE
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    composed = None
                    for conn in conns:
                        layer = fetch_tile_overzoom(conn, z, x, y, tile_size=tile_size, tms=tms)
                        if layer is None:
                            continue
                        composed = layer if composed is None else Image.alpha_composite(composed, layer)
                    if composed is None or composed.getextrema()[3][1] == 0:
                        continue  # no data / fully transparent -> leave hole for overzoom
                    buf = io.BytesIO()
                    save_img = composed.convert("RGB") if pil_format == "JPEG" else composed
                    save_img.save(buf, format=pil_format, quality=quality)
                    srow = ((1 << z) - 1 - y) if tms else y
                    out.execute(
                        "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                        (z, x, srow, buf.getvalue()),
                    )
                    written += 1
        for key, value in (("format", tile_format), ("minzoom", min_zoom), ("maxzoom", max_zoom)):
            out.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (key, str(value)))
        out.commit()
        out.close()
    finally:
        for conn in conns:
            conn.close()
    return destination


def stitch_to_512(
    source: Path,
    destination: Path,
    *,
    tile_format: str = "jpg",
    quality: int = 90,
    background: Tuple[int, int, int] = (0, 0, 0),
    src_tile_size: int = 256,
    metadata: Optional[Mapping[str, str]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Path:
    """Build a double-resolution (512px) tile pyramid from a 256px source.

    Each output tile ``(z, x, y)`` is the 2x2 mosaic of the source tiles
    ``(z+1, 2x|2x+1, 2y|2y+1)``: output zoom ``z`` is drawn from source zoom
    ``z+1``, so a 256px source spanning ``[zmin..zmax]`` yields a 512px pyramid
    spanning ``[zmin-1 .. zmax-1]`` (e.g. GSI 256px z2..z18 -> 512px z1..z17,
    matching the "256px culture is one zoom finer" convention). This is the
    repackaging that lets consumers use the default ``tileSize: 512`` directly.

    Coordinates are XYZ; MBTiles store TMS rows (``row = 2**z - 1 - y``).
    Children are placed north-up: the western child (``x`` even) fills the left
    half, the northern child (``y`` even) the top half. Missing children leave
    their quadrant filled with ``background`` (JPEG carries no transparency).
    Stitching decodes and re-encodes, so it is lossy relative to the source
    JPEGs — unavoidable when merging four tiles into one image.

    Memory is bounded: tiles are processed one parent-column strip at a time, so
    a planet-scale z18 source never loads wholesale. ``format`` /
    ``minzoom`` / ``maxzoom`` are written; ``name`` / ``attribution`` /
    ``bounds`` / ``center`` are carried from the source unless overridden via
    ``metadata``.
    """
    from PIL import Image  # local import; Pillow only needed for stitching

    pil_format = _PIL_SAVE_FORMAT.get(tile_format.lower())
    if pil_format is None:
        raise ValueError(f"Unsupported tile_format: {tile_format}")
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"MBTiles not found: {source}")
    out_size = 2 * src_tile_size
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    def _encode(img) -> bytes:
        buf = io.BytesIO()
        if pil_format == "JPEG":
            img = img.convert("RGB")
        img.save(buf, format=pil_format, quality=quality)
        return buf.getvalue()

    rconn = sqlite3.connect(str(source))
    conn = sqlite3.connect(str(destination))
    try:
        _init_mbtiles(conn)
        conn.execute("PRAGMA synchronous=OFF")
        zr = rconn.execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles").fetchone()
        written = 0
        out_min = None
        out_max = None
        if zr and zr[0] is not None:
            szmin, szmax = zr
            for sz in range(szmin, szmax + 1):
                oz = sz - 1
                if oz < 0:
                    continue  # source zoom 0 has no 512 parent
                n = 1 << sz
                pcols = [
                    r[0]
                    for r in rconn.execute(
                        "SELECT DISTINCT tile_column >> 1 FROM tiles WHERE zoom_level=? ORDER BY 1",
                        (sz,),
                    )
                ]
                for ox in pcols:
                    strip: Dict[int, Dict[Tuple[int, int], bytes]] = {}
                    for sx, srow, blob in rconn.execute(
                        "SELECT tile_column, tile_row, tile_data FROM tiles "
                        "WHERE zoom_level=? AND (tile_column=? OR tile_column=?)",
                        (sz, 2 * ox, 2 * ox + 1),
                    ):
                        y = (n - 1) - srow  # TMS -> XYZ
                        strip.setdefault(y >> 1, {})[(sx & 1, y & 1)] = blob
                    for oy, kids in strip.items():
                        canvas = Image.new("RGB", (out_size, out_size), background)
                        for (dx, dy), blob in kids.items():
                            im = Image.open(io.BytesIO(blob)).convert("RGB")
                            if im.size != (src_tile_size, src_tile_size):
                                im = im.resize((src_tile_size, src_tile_size), Image.Resampling.LANCZOS)
                            canvas.paste(im, (dx * src_tile_size, dy * src_tile_size))
                        orow = ((1 << oz) - 1) - oy
                        conn.execute(
                            "INSERT OR REPLACE INTO tiles "
                            "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                            (oz, ox, orow, _encode(canvas)),
                        )
                        written += 1
                        if on_progress is not None and written % 1000 == 0:
                            conn.commit()
                            on_progress(written)
                    conn.commit()
                out_min = oz if out_min is None else min(out_min, oz)
                out_max = oz if out_max is None else max(out_max, oz)

        _set_metadata(conn, "format", tile_format)
        if out_min is not None:
            _set_metadata(conn, "minzoom", str(out_min))
            _set_metadata(conn, "maxzoom", str(out_max))
        if rconn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone():
            srcmeta = dict(rconn.execute("SELECT name, value FROM metadata"))
            for key in ("name", "attribution", "bounds", "center"):
                if key in srcmeta:
                    _set_metadata(conn, key, srcmeta[key])
        for key, value in (metadata or {}).items():
            _set_metadata(conn, key, str(value))
        conn.commit()
    finally:
        rconn.close()
        conn.close()
    return destination

"""Parallel, block-aware XYZ tile downloader.

Downloads raster XYZ tiles into a `z/x/y.ext` directory with moderate
concurrency, resumable (existing tiles are skipped), and polite to the server:
on a 403/429/503 (the source rate-limiting us) all workers cool down for a
window before retrying. Tiles stream through a bounded queue so a catalog of
tens of millions of entries downloads in constant memory.

The HTTP getter is injectable so the control flow (skip / stats / cool-down) is
unit tested without the network or real sleeps.
"""

from __future__ import annotations

import queue
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from planetarble.logging import get_logger

LOGGER = get_logger(__name__)

Triplet = Tuple[int, int, int]


@dataclass
class TileDownloadStats:
    ok: int = 0
    cached: int = 0
    http_404: int = 0
    blocked: int = 0  # transient 403/429/503 hits (then cooled down + retried)
    error: int = 0
    failed: int = 0
    downloaded_bytes: int = 0


def tile_path(out_dir: Path, z: int, x: int, y: int, ext: str) -> Path:
    return Path(out_dir) / str(z) / str(x) / f"{y}.{ext}"


def download_xyz_tiles(
    triplets: Iterable[Triplet],
    *,
    out_dir: Path,
    template: str,
    ext: str = "jpg",
    workers: int = 10,
    timeout: int = 30,
    retries: int = 5,
    cooldown_s: float = 30.0,
    user_agent: str = "planetarble/0.1 (+https://github.com/yuiseki/planetarble)",
    http_get: Optional[Callable[[str, int], object]] = None,
    on_progress: Optional[Callable[[TileDownloadStats], None]] = None,
    report_every: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
) -> TileDownloadStats:
    """Download ``triplets`` (z,x,y) from ``template`` into ``out_dir`` in parallel."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = TileDownloadStats()
    lock = threading.Lock()
    pause_until = [0.0]  # shared cool-down deadline when the server blocks us
    thread_local = threading.local()

    def _default_get(url: str, to: int):
        if not hasattr(thread_local, "session"):
            import requests

            thread_local.session = requests.Session()
            thread_local.session.headers.update({"User-Agent": user_agent})
        return thread_local.session.get(url, timeout=to)

    getter = http_get or _default_get

    def fetch(t: Triplet) -> None:
        z, x, y = t
        path = tile_path(out_dir, z, x, y, ext)
        if path.exists() and path.stat().st_size > 0:
            with lock:
                stats.cached += 1
            return
        url = template.format(z=z, x=x, y=y)
        for attempt in range(retries):
            wait = pause_until[0] - time.monotonic()
            if wait > 0:
                sleep(min(wait, cooldown_s) + random.random())
            try:
                resp = getter(url, timeout)
                code = resp.status_code
                if code == 200:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(resp.content)
                    with lock:
                        stats.ok += 1
                        stats.downloaded_bytes += len(resp.content)
                    return
                if code == 404:
                    with lock:
                        stats.http_404 += 1
                    return
                if code in (403, 429, 503):
                    with lock:
                        stats.blocked += 1
                        pause_until[0] = time.monotonic() + cooldown_s
                    sleep((2 ** attempt) + random.random())
                    continue
                with lock:
                    stats.error += 1
                return
            except Exception:  # noqa: BLE001 - network hiccup: retry
                with lock:
                    stats.error += 1
                sleep((2 ** attempt) + random.random())
        with lock:
            stats.failed += 1

    q: "queue.Queue[Optional[Triplet]]" = queue.Queue(maxsize=workers * 8)

    def worker() -> None:
        while True:
            item = q.get()
            try:
                if item is None:
                    return
                fetch(item)
            finally:
                q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for th in threads:
        th.start()

    rstop = threading.Event()

    def reporter() -> None:
        while not rstop.wait(report_every):
            on_progress(stats)  # type: ignore[misc]

    rep = threading.Thread(target=reporter, daemon=True)
    if on_progress is not None:
        rep.start()

    for t in triplets:  # bounded queue -> streams in constant memory
        q.put(t)
    for _ in threads:
        q.put(None)
    for th in threads:
        th.join()
    rstop.set()
    if on_progress is not None:
        on_progress(stats)
    return stats

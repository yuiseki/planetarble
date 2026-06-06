"""GSI mokuroku catalog parsing.

GSI publishes a per-layer tile catalog `mokuroku.csv.gz` listing every tile that
actually exists, one per line:

    z/x/y.ext,mtime_epoch,size_bytes,md5hex

Driving downloads from the catalog means we fetch only existing tiles — no 404
probing over ocean / uncovered areas (for seamlessphoto, ~75% of a naive z18
bbox would be misses). The catalog is large (tens of millions of lines), so the
public API is a streaming line iterator; parsing one line is a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Optional


@dataclass(frozen=True)
class MokurokuEntry:
    z: int
    x: int
    y: int
    ext: str
    mtime: int
    size: int
    md5: str


def parse_mokuroku_line(line: str) -> Optional[MokurokuEntry]:
    """Parse one mokuroku CSV line; return None for blank/malformed lines."""
    line = line.strip()
    if not line:
        return None
    parts = line.split(",")
    if len(parts) < 3:
        return None
    seg = parts[0].split("/")
    if len(seg) != 3:
        return None
    y_str, _, ext = seg[2].partition(".")
    try:
        z = int(seg[0])
        x = int(seg[1])
        y = int(y_str)
        mtime = int(parts[1])
        size = int(parts[2])
    except ValueError:
        return None
    md5 = parts[3] if len(parts) > 3 else ""
    return MokurokuEntry(z=z, x=x, y=y, ext=ext or "", mtime=mtime, size=size, md5=md5)


def iter_mokuroku_lines(
    lines: Iterable[str], *, zoom_min: int = 0, zoom_max: int = 24
) -> Iterator[MokurokuEntry]:
    """Yield parsed entries within [zoom_min, zoom_max], skipping bad lines.

    Streams over ``lines`` (e.g. a decompressed mokuroku file object), so the
    whole catalog never has to be held in memory.
    """
    for line in lines:
        entry = parse_mokuroku_line(line)
        if entry is not None and zoom_min <= entry.z <= zoom_max:
            yield entry

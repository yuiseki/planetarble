"""The serve subcommand: a PMTiles server with Range support plus a simple viewer."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from planetarble.config import load_config
from planetarble.logging import get_logger

from .checks import _resolve_config_path

LOGGER = get_logger(__name__)


def _handle_serve(args: argparse.Namespace) -> int:
    if args.pmtiles is None and not args.region:
        raise SystemExit("--pmtiles or --region must be provided")
    if args.pmtiles is not None and args.region:
        raise SystemExit("--pmtiles and --region are mutually exclusive")

    if args.pmtiles is not None:
        pmtiles_path = args.pmtiles.resolve()
    else:
        cfg = load_config(_resolve_config_path(args.config))
        region = args.region
        distribution_dir = (cfg.output_dir / "distribution").resolve()
        region_variants = [region]
        if region.endswith("_land"):
            region_variants.append(region[: -len("_land")])
        candidates: list[Path] = []
        for variant in region_variants:
            pattern = f"planet_{cfg.processing.gebco_year}_*z_{variant}_hls.pmtiles"
            candidates.extend(distribution_dir.glob(pattern))
        if not candidates:
            pmtiles_name = f"planet_{cfg.processing.gebco_year}_{cfg.processing.max_zoom}z_{region}_hls.pmtiles"
            pmtiles_path = (distribution_dir / pmtiles_name).resolve()
        else:
            def zoom_key(path: Path) -> int:
                match = re.search(r"_(\d+)z_", path.name)
                return int(match.group(1)) if match else -1

            pmtiles_path = max(candidates, key=zoom_key).resolve()
    center = None
    if args.region:
        try:
            # Imported only to probe that GDAL is available; the except branch
            # falls back to no centre.
            from osgeo import ogr  # type: ignore  # noqa: F401

            from planetarble.acquisition.hls import load_region_geometry
        except Exception:
            center = None
        else:
            cfg = load_config(_resolve_config_path(args.config))
            region_name = args.region
            region_config = None
            for item in cfg.hls.plan_regions:
                if item.name == region_name:
                    region_config = item
                    break
            if region_config is not None:
                geometry = load_region_geometry(region_config, data_dir=cfg.data_dir)
                if geometry is not None:
                    envelope = geometry.GetEnvelope()
                    center = ((envelope[0] + envelope[1]) / 2, (envelope[2] + envelope[3]) / 2)
    if not pmtiles_path.exists():
        raise SystemExit(f"PMTiles file not found: {pmtiles_path}")

    viewer_root = args.viewer_root.resolve()
    if not viewer_root.exists():
        raise SystemExit(f"Viewer directory not found: {viewer_root}")

    tiles_host = args.host
    ui_port = args.ui_port
    tiles_port = args.tiles_port
    if tiles_port != ui_port:
        LOGGER.warning(
            "serve uses a single HTTP server; ignoring tiles-port",
            extra={"tiles_port": tiles_port, "ui_port": ui_port},
        )

    distribution_dir = pmtiles_path.parent
    viewer_target = distribution_dir / "viewer"
    viewer_target.mkdir(parents=True, exist_ok=True)
    source_index = viewer_root / "index.html"
    if not source_index.exists():
        raise SystemExit(f"Viewer index not found: {source_index}")
    shutil.copy2(source_index, viewer_target / "index.html")

    bind_host = tiles_host if tiles_host != "0.0.0.0" else "localhost"
    ui_url = f"http://{bind_host}:{ui_port}/viewer/"
    pmtiles_url = f"http://{bind_host}:{ui_port}/{pmtiles_path.name}"

    LOGGER.info(
        "serve starting",
        extra={"pmtiles": str(pmtiles_path), "viewer": str(viewer_target)},
    )
    LOGGER.info("serve commands", extra={"ui": f"python -m planetarble serve {tiles_host}:{ui_port}"})
    viewer_url = f"{ui_url}?pmtiles={pmtiles_url}"
    if center is not None:
        viewer_url = f"{viewer_url}#8/{center[1]}/{center[0]}"
    LOGGER.info("serve urls", extra={"viewer": viewer_url, "pmtiles": pmtiles_url})
    LOGGER.info("open viewer: %s", viewer_url)

    try:
        if args.open:
            import webbrowser

            webbrowser.open(f"{ui_url}?pmtiles={pmtiles_url}")
        handler = functools.partial(_RangeRequestHandler, directory=str(distribution_dir))
        httpd = http.server.ThreadingHTTPServer((tiles_host, ui_port), handler)
        LOGGER.info("serve http", extra={"address": f"{tiles_host}:{ui_port}"})
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("serve interrupted; shutting down")
    finally:
        try:
            httpd.shutdown()  # type: ignore[name-defined]
        except Exception:
            pass
    return 0


class _RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files with HTTP range support for PMTiles."""

    def send_head(self):  # type: ignore[override]
        path = self.translate_path(self.path)
        if not os.path.exists(path):
            return super().send_head()
        if os.path.isdir(path):
            return super().send_head()

        f = None
        try:
            f = open(path, "rb")
            fs = os.fstat(f.fileno())
            size = fs.st_size
            range_header = self.headers.get("Range")
            if range_header:
                start, end = self._parse_range(range_header, size)
                if start is None:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return None
                self.send_response(206)
                self.send_header("Content-Type", self.guess_type(path))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                f.seek(start)
                self.wfile.write(f.read(end - start + 1))
                f.close()
                return None

            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return f
        except OSError:
            if f:
                f.close()
            self.send_error(404, "File not found")
            return None

    def _parse_range(self, header: str, size: int) -> tuple[Optional[int], int]:
        if not header.startswith("bytes="):
            return None, 0
        range_spec = header.split("=", 1)[1]
        if "," in range_spec:
            range_spec = range_spec.split(",", 1)[0]
        start_str, end_str = range_spec.split("-", 1)
        if start_str == "":
            length = int(end_str)
            start = max(size - length, 0)
            end = size - 1
            return start, end
        start = int(start_str)
        end = int(end_str) if end_str else size - 1
        if start >= size:
            return None, 0
        end = min(end, size - 1)
        return start, end


def register_serve(subcommands: argparse._SubParsersAction) -> None:
    serve = subcommands.add_parser("serve", help="Serve PMTiles with a simple web viewer")
    serve.set_defaults(handler=_handle_serve)
    serve.add_argument("--pmtiles", type=Path, default=None, help="Path to the PMTiles archive")
    serve.add_argument("--region", type=str, default=None, help="Region name to resolve PMTiles from distribution")
    serve.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON) for region resolution",
    )
    serve.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    serve.add_argument("--tiles-port", type=int, default=8080, help="Port for pmtiles server")
    serve.add_argument("--ui-port", type=int, default=8081, help="Port for the viewer UI")
    serve.add_argument(
        "--viewer-root",
        type=Path,
        default=Path("src/planetarble/viewer"),
        help="Directory containing viewer assets",
    )
    serve.add_argument("--open", action="store_true", help="Open the viewer URL in a browser")

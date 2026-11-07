"""Single-source fetch / inspection subcommands: mpc-fetch, copernicus-layers."""

from __future__ import annotations

import argparse
from pathlib import Path

from planetarble.acquisition import (
    CopernicusCredentialsMissing,
    MPCError,
    fetch_true_color_tile,
    get_available_layers,
)
from planetarble.logging import get_logger

LOGGER = get_logger(__name__)


def _handle_copernicus_layers(args: argparse.Namespace) -> int:
    from planetarble.acquisition import CopernicusAccessError, CopernicusAuthError

    try:
        layers = get_available_layers(
            instance_id=args.instance_id,
            use_credentials=not args.no_credentials,
        )
    except CopernicusCredentialsMissing as exc:
        LOGGER.error(str(exc))
        return 1
    except (CopernicusAuthError, CopernicusAccessError) as exc:
        LOGGER.error("Unable to list Copernicus layers: %s", exc)
        return 1

    if not layers:
        LOGGER.warning("No layers found for the specified Copernicus instance")
        return 0

    for name, title in layers:
        print(f"{name}\t{title}")
    return 0


def _handle_mpc_fetch(args: argparse.Namespace) -> int:
    try:
        summary = fetch_true_color_tile(
            lat=args.lat,
            lon=args.lon,
            width_m=args.width_m,
            height_m=args.height_m,
            output_path=args.output,
            max_cloud=args.max_cloud,
            start_datetime=args.start_datetime,
            end_datetime=args.end_datetime,
            gdal_translate=args.gdal_translate,
            dry_run=args.dry_run,
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except MPCError as exc:
        LOGGER.error("MPC fetch failed: %s", exc)
        return 1

    LOGGER.info("mpc fetch complete", extra=summary)
    if args.dry_run:
        print(summary)
    return 0


def register_mpc_fetch(subcommands: argparse._SubParsersAction) -> None:
    mpc_fetch = subcommands.add_parser(
        "mpc-fetch",
        help="Download a Sentinel-2 true color clip via Microsoft Planetary Computer",
    )
    mpc_fetch.set_defaults(handler=_handle_mpc_fetch)
    mpc_fetch.add_argument("--lat", type=float, required=True, help="Latitude of the target point")
    mpc_fetch.add_argument("--lon", type=float, required=True, help="Longitude of the target point")
    mpc_fetch.add_argument(
        "--width-m",
        type=float,
        default=500.0,
        help="Clip width in meters (default: 500)",
    )
    mpc_fetch.add_argument(
        "--height-m",
        type=float,
        default=500.0,
        help="Clip height in meters (default: 500)",
    )
    mpc_fetch.add_argument(
        "--max-cloud",
        type=float,
        default=None,
        help="Maximum acceptable cloud cover percentage",
    )
    mpc_fetch.add_argument(
        "--start",
        dest="start_datetime",
        default=None,
        help="ISO8601 start datetime filter (inclusive)",
    )
    mpc_fetch.add_argument(
        "--end",
        dest="end_datetime",
        default=None,
        help="ISO8601 end datetime filter (inclusive)",
    )
    mpc_fetch.add_argument(
        "--output",
        type=Path,
        default=Path("mpc_true_color.tif"),
        help="Output GeoTIFF path (default: mpc_true_color.tif)",
    )
    mpc_fetch.add_argument(
        "--gdal-translate",
        default="gdal_translate",
        help="gdal_translate executable name (default: gdal_translate)",
    )
    mpc_fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing GDAL",
    )


def register_copernicus_layers(subcommands: argparse._SubParsersAction) -> None:
    copernicus_layers = subcommands.add_parser(
        "copernicus-layers",
        help="List available Copernicus WMS layers for the configured instance",
    )
    copernicus_layers.set_defaults(handler=_handle_copernicus_layers)
    copernicus_layers.add_argument(
        "--instance-id",
        default=None,
        help="Override COPERNICUS_INSTANCE_ID for listing layers",
    )
    copernicus_layers.add_argument(
        "--no-credentials",
        action="store_true",
        help="Do not use client credentials when fetching capabilities",
    )

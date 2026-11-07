"""The overlay pipeline subcommands: build, prefetch, split-plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from planetarble.acquisition import split_plan_by_miniplanet
from planetarble.config import load_config

from .checks import _resolve_config_path, _resolve_hls_plan_path


def _handle_build(args: argparse.Namespace) -> int:
    import yaml

    from planetarble.overlay import parse_pipeline_spec
    from planetarble.overlay.executor import DefaultPlanetExecutor
    from planetarble.overlay.orchestrator import build_planet

    cfg = load_config(_resolve_config_path(args.config))
    spec = parse_pipeline_spec(yaml.safe_load(args.spec.read_text(encoding="utf-8")))
    work_dir = (args.work_dir or (cfg.output_dir / "build")).resolve()
    executor = DefaultPlanetExecutor(
        spec,
        cfg,
        data_dir=cfg.data_dir,
        work_dir=work_dir,
        base_mbtiles=args.base_mbtiles.resolve(),
        tile_size=args.tile_size,
    )
    result = build_planet(spec, executor, data_dir=cfg.data_dir, strict=not args.no_strict)
    print(f"built planet: {result.planet}")
    print(f"  base: {result.base_mbtiles}")
    for s in result.stacks:
        print(f"  stack: {s}")
    return 0


def _handle_prefetch(args: argparse.Namespace) -> int:
    import random
    import time

    import yaml

    from planetarble.overlay import parse_pipeline_spec
    from planetarble.overlay.executor import DefaultPlanetExecutor
    from planetarble.prefetch import (
        PrefetchPacing,
        prefetch_planet,
        prefetch_wait_seconds,
    )

    cfg = load_config(_resolve_config_path(args.config))
    spec = parse_pipeline_spec(yaml.safe_load(args.spec.read_text(encoding="utf-8")))
    s2_overlays = [o for o in spec.overlays if o.source == "sentinel2"]

    if args.dry_run:
        print(f"prefetch (dry-run): {len(s2_overlays)} sentinel2 overlay(s) would be fetched")
        for o in s2_overlays:
            scenes = (o.source_options or {}).get("mosaic_max_scenes", "config-default")
            print(f"  {o.name}: bbox={tuple(o.aoi.bbox) if o.aoi.bbox else o.aoi} scenes<={scenes}")
        return 0

    pacing = PrefetchPacing(
        throttle_floor_kibps=args.throttle_floor,
        jitter_min_s=args.pace_min, jitter_max_s=args.pace_max,
        cooldown_min_s=args.cooldown_min, cooldown_max_s=args.cooldown_max,
    )
    work_dir = (args.work_dir or (cfg.output_dir / "build")).resolve()
    # base_mbtiles is unused for prefetch (no compositing); pass the spec path as a placeholder
    executor = DefaultPlanetExecutor(
        spec, cfg, data_dir=cfg.data_dir, work_dir=work_dir, base_mbtiles=args.spec.resolve(),
    )

    def pacer(stats) -> None:
        wait = prefetch_wait_seconds(stats.downloaded_bytes, stats.elapsed_seconds, pacing, random.uniform)
        print(
            f"  {stats.overlay}: downloaded={stats.downloaded_count} "
            f"({stats.downloaded_bytes/1e6:.0f}MB) hits={stats.hit_count} "
            f"in {stats.elapsed_seconds:.0f}s -> wait {wait:.0f}s"
        )
        if wait > 0:
            time.sleep(wait)

    def on_error(ov, exc) -> None:
        print(f"  ERROR {ov.name}: {exc}")

    import time as _time

    def on_recovery_wait(round_index, n_failed, wait_s) -> None:
        print(
            f"  MPC appears down: {n_failed} overlay(s) failed in round {round_index}; "
            f"waiting {wait_s:.0f}s for recovery, then retrying (cached ones skip)"
        )

    results = prefetch_planet(
        spec, executor, pacer=pacer,
        on_skip=lambda ov: print(f"  skip {ov.name} (source={ov.source})"),
        on_error=on_error,
        recovery_wait_s=args.recovery_wait,
        max_rounds=args.max_recovery_rounds,
        on_recovery_wait=on_recovery_wait,
        sleeper=_time.sleep,
    )
    total_mb = sum(r.downloaded_bytes for r in results) / 1e6
    done_names = {r.overlay for r in results}
    failed = [o.name for o in s2_overlays if o.name not in done_names]
    print(f"prefetch done: {len(results)}/{len(s2_overlays)} sentinel2 overlay(s) ok, {total_mb:.0f}MB downloaded")
    if failed:
        print(f"  {len(failed)} still failing after {args.max_recovery_rounds} round(s): {', '.join(failed)} -- re-run later (cached ones skip fast)")
    return 0


def _handle_split_plan(args: argparse.Namespace) -> int:
    cfg = load_config(_resolve_config_path(args.config))
    plan_path = (args.plan.resolve() if args.plan else _resolve_hls_plan_path(cfg, None))
    if not plan_path.exists():
        raise SystemExit(f"Plan not found: {plan_path}")
    out_dir = (args.out.resolve() if args.out else (cfg.data_dir / "plans" / "shards"))
    shards = split_plan_by_miniplanet(plan_path, out_dir)
    print(f"split {plan_path} into {len(shards)} miniplanet shard(s) under {out_dir}")
    for key in sorted(shards):
        print(f"  {key}: {shards[key]}")
    return 0


def register_build(subcommands: argparse._SubParsersAction) -> None:
    build = subcommands.add_parser(
        "build",
        help="Build a custom planet from an AOI overlay spec (ADR 0001)",
    )
    build.add_argument("--spec", type=Path, required=True, help="AOI overlay pipeline spec (YAML)")
    build.add_argument("--config", type=Path, default=None, help="Base pipeline config (defaults to configs/base/pipeline.yaml)")
    build.add_argument("--base-mbtiles", type=Path, required=True, help="Prebuilt global base MBTiles (the floor)")
    build.add_argument("--work-dir", type=Path, default=None, help="Scratch dir for intermediates (default: output/build)")
    build.add_argument("--tile-size", type=int, default=512)
    build.add_argument("--no-strict", action="store_true", help="Warn instead of failing on zoom-ceiling violations")


def register_prefetch(subcommands: argparse._SubParsersAction) -> None:
    prefetch = subcommands.add_parser(
        "prefetch",
        help="Download-only: warm the Sentinel-2 asset cache for a spec's AOIs (no tiling)",
    )
    prefetch.add_argument("--spec", type=Path, required=True, help="AOI overlay pipeline spec (YAML)")
    prefetch.add_argument("--config", type=Path, default=None, help="Base pipeline config (defaults to configs/base/pipeline.yaml)")
    prefetch.add_argument("--work-dir", type=Path, default=None, help="Scratch dir (default: output/build)")
    prefetch.add_argument("--pace-min", type=float, default=60.0, help="Min inter-tile wait (s) when throughput was healthy")
    prefetch.add_argument("--pace-max", type=float, default=300.0, help="Max inter-tile wait (s) when throughput was healthy")
    prefetch.add_argument("--throttle-floor", type=float, default=150.0, help="KiB/s below which the last tile counts as throttled")
    prefetch.add_argument("--cooldown-min", type=float, default=600.0, help="Min cooldown (s) after a throttled tile")
    prefetch.add_argument("--cooldown-max", type=float, default=900.0, help="Max cooldown (s) after a throttled tile")
    prefetch.add_argument("--recovery-wait", type=float, default=1800.0, help="Seconds to wait between recovery rounds during a broad MPC outage")
    prefetch.add_argument("--max-recovery-rounds", type=int, default=6, help="Max rounds re-attempting failed overlays (1 = no recovery wait); rides out an MPC STAC outage")
    prefetch.add_argument("--dry-run", action="store_true", help="List the Sentinel-2 overlays that would be prefetched and exit")


def register_split_plan(subcommands: argparse._SubParsersAction) -> None:
    split_plan = subcommands.add_parser(
        "split-plan",
        help="Split a global HLS plan into one ndjson shard per miniplanet",
    )
    split_plan.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    split_plan.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="Explicit plan ndjson to split (defaults to the global HLS plan)",
    )
    split_plan.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for shards (defaults to data_dir/plans/shards)",
    )

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import __version__
from .regions import load_regions
MODE_NAMES = ["bootstrap", "daily", "fast", "weekly"]


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(description="SismoAI World Cloud")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--regions", default="config/world_regions.json")

    r = sub.add_parser("run-shard")
    r.add_argument("--regions", default="config/world_regions.json")
    r.add_argument("--mode", choices=MODE_NAMES, required=True)
    r.add_argument("--shard-index", type=int, required=True)
    r.add_argument("--shard-count", type=int, required=True)
    r.add_argument("--state-dir", required=True)
    r.add_argument("--runtime-root", required=True)
    r.add_argument("--output-root", required=True)

    a = sub.add_parser("aggregate")
    a.add_argument("--regions", default="config/world_regions.json")
    a.add_argument("--collected-results", required=True)
    a.add_argument("--state-results")
    a.add_argument("--docs", required=True)
    a.add_argument("--mode", default="unknown")

    args = p.parse_args(argv)
    if args.command == "validate":
        meta, regions = load_regions(Path(args.regions))
        emit({"status": "OK", "version": __version__, "regions": len(regions), "model_version": meta.get("model_version")})
        return 0
    if args.command == "run-shard":
        if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
            raise SystemExit("Índice/cantidad de shards inválidos")
        from .runner import run_shard
        out = run_shard(
            regions_path=Path(args.regions), mode=args.mode,
            shard_index=args.shard_index, shard_count=args.shard_count,
            state_dir=Path(args.state_dir), runtime_root=Path(args.runtime_root),
            output_root=Path(args.output_root),
        )
        emit(out)
        return 0
    if args.command == "aggregate":
        from .site import build_world
        world = build_world(
            regions_path=Path(args.regions),
            collected_results_dir=Path(args.collected_results),
            state_results_dir=Path(args.state_results) if args.state_results else None,
            docs_dir=Path(args.docs), mode=args.mode,
        )
        emit({k: world[k] for k in ("generated_at", "operation_mode", "regions_configured", "regions_operational", "regions_public_valid")})
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

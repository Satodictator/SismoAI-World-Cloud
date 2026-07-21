from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .regions import load_regions
from .site import build_world


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    meta, regions = load_regions(repo / "config" / "world_regions.json")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rr = root / "results"
        rr.mkdir()
        for i, region in enumerate(regions):
            (rr / f"{region.id}.json").write_text(json.dumps({
                "status": "OK", "generated_at": "2026-01-01T00:00:00Z",
                "region": {"id": region.id, "name": region.name, "group": region.group, "bbox": region.bbox},
                "current": {"iedc_provisional": float(i), "iedc_public": None, "public_valid": False,
                            "state": "NORMAL", "confidence": .5, "coverage": .5, "data_quality": .8,
                            "baseline_progress": 1, "available_families": 1, "family_scores": {"seismic": i}, "reasons": []},
                "sources": [], "counts": {}, "latest_backtest": [], "latest_events": [], "errors": [],
            }), encoding="utf-8")
        docs = root / "docs"
        world = build_world(regions_path=repo / "config" / "world_regions.json",
                            collected_results_dir=rr, docs_dir=docs, mode="selftest")
        checks = {
            "region_catalog": len(regions) >= 30,
            "unique_regions": len({r.id for r in regions}) == len(regions),
            "world_json": (docs / "data" / "world.json").exists(),
            "dashboard": (docs / "index.html").exists(),
            "manifest": (docs / "data" / "manifest.json").exists(),
            "ranking": len(world["ranking"]) == len(regions),
        }
        ok = all(checks.values())
        print(json.dumps({"status": "OK" if ok else "FAILED", "checks": checks}, ensure_ascii=False, indent=2))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
        historical = root / "historical_summary.json"
        historical.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": "2026-01-01T00:00:00Z",
            "run_status": "OK",
            "state": "BUILDING_HISTORY",
            "catalog": {
                "target_start": "1973-01-01", "cursor": "1980-01-01",
                "events": 1000, "months_complete": 84, "months_total": 600,
                "progress": 0.14,
            },
            "sources": [], "context_controls": [], "patterns": [],
            "pattern_policy": {
                "research_only": True, "modifies_iedc": False, "activates_alerts": False,
            },
            "scientific_notice": "Prueba histórica aislada.",
        }), encoding="utf-8")
        previous_world = root / "history" / "world"
        previous_world.mkdir(parents=True)
        (previous_world / "2025.json").write_text(json.dumps({
            "generated_at": "2025-12-31T00:00:00Z",
            "ranking": [{
                "region_id": region.id,
                "region_name": region.name,
                "state": "NORMAL",
                "iedc_provisional": 0,
            } for region in regions],
        }, ensure_ascii=False), encoding="utf-8")
        previous_bulletins = root / "history" / "bulletins"
        previous_bulletins.mkdir(parents=True)
        docs = root / "docs"
        world = build_world(regions_path=repo / "config" / "world_regions.json",
                            collected_results_dir=rr,
                            historical_summary_path=historical,
                            previous_world_dir=previous_world,
                            previous_bulletins_dir=previous_bulletins,
                            docs_dir=docs, mode="selftest")
        dashboard_text = (docs / "index.html").read_text(encoding="utf-8")
        bulletin = json.loads((docs / "data" / "bulletin.json").read_text(encoding="utf-8"))
        checks = {
            "region_catalog": len(regions) >= 30,
            "unique_regions": len({r.id for r in regions}) == len(regions),
            "world_json": (docs / "data" / "world.json").exists(),
            "dashboard": (docs / "index.html").exists(),
            "historical_json": (docs / "data" / "historical.json").exists(),
            "historical_panel": "Laboratorio histórico" in dashboard_text,
            "notification_panel": "Avisos privados" in dashboard_text,
            "bulletin_json": (docs / "data" / "bulletin.json").exists(),
            "bulletin_archive": (docs / "data" / "bulletins" / "index.json").exists(),
            "bulletin_panel": "Boletín SismoAI" in dashboard_text,
            "bulletin_audio": "speechSynthesis" in dashboard_text,
            "multilingual": len(bulletin.get("messages") or {}) >= 10,
            "no_official_alert": bulletin.get("official_alert") is False,
            "manifest": (docs / "data" / "manifest.json").exists(),
            "ranking": len(world["ranking"]) == len(regions),
        }
        ok = all(checks.values())
        print(json.dumps({"status": "OK" if ok else "FAILED", "checks": checks}, ensure_ascii=False, indent=2))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

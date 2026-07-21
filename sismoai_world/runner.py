from __future__ import annotations

import json
import os
import shutil
import sqlite3
import traceback
import zipfile
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mam.dtrg_research_v2 import SCIENTIFIC_NOTICE as REGIONAL_NOTICE
from mam.dtrg_research_v2.analytics import calculate_history, current
from mam.dtrg_research_v2.backtest import run_backtest
from mam.dtrg_research_v2.config import ResearchConfig
from mam.dtrg_research_v2.db import default_db, initialize, rows, upsert_source
from mam.dtrg_research_v2.sources import (
    ingest_gnss,
    ingest_goes,
    ingest_insar_catalog,
    ingest_local_insar,
    ingest_usgs,
)
from mam.dtrg_research_v2.util import safe_json, utcnow

from . import SCIENTIFIC_NOTICE, __version__
from .regions import WorldRegion, load_regions


MODE_PLAN = {
    "fast": {
        # In an existing regional state, only a short overlap is fetched.
        # usgs_days is used only when the state is empty.
        "usgs_days": 400,
        "usgs_overlap_days": 8,
        "gnss_years": 0,
        "goes_hours": 8,
        "insar_days": 0,
        "calculate_days": 400,
        "backtest_years": 0,
        "goes_sampling_minutes": 30,
    },
    "daily": {
        "usgs_days": 730,
        "usgs_overlap_days": 15,
        "gnss_years": 2,
        "goes_hours": 30,
        "insar_days": 14,
        "calculate_days": 450,
        "backtest_years": 0,
        "goes_sampling_minutes": 60,
    },
    "weekly": {
        "usgs_days": 365 * 5,
        "usgs_overlap_days": 21,
        "gnss_years": 5,
        "goes_hours": 24 * 7,
        "insar_days": 180,
        "calculate_days": 365 * 3,
        "backtest_years": 3,
        "goes_sampling_minutes": 120,
    },
    "bootstrap": {
        # A two-year initial load is large enough to start operating while
        # keeping the first free cloud execution within reasonable bounds.
        # The weekly operation extends the state to five years.
        "usgs_days": 365 * 2,
        "usgs_overlap_days": 15,
        "gnss_years": 1,
        "goes_hours": 24,
        "insar_days": 30,
        "calculate_days": 450,
        "backtest_years": 1,
        "goes_sampling_minutes": 60,
    },
}


def _write_region_config(region_root: Path, region: WorldRegion) -> ResearchConfig:
    cfg = ResearchConfig(
        region_id=region.id,
        min_lat=region.min_lat,
        max_lat=region.max_lat,
        min_lon=region.min_lon,
        max_lon=region.max_lon,
        min_magnitude=region.min_magnitude,
        baseline_days=180,
        score_window_days=14,
        history_years=5,
        gnss_history_years=5,
        max_gnss_stations=region.max_gnss_stations,
        goes_recent_hours=12,
        goes_sampling_minutes=60,
        insar_catalog_days=180,
        insar_auto_download_max=1,
        backtest_horizon_days=7,
        backtest_event_magnitude=region.event_magnitude,
        public_min_confidence=0.75,
        public_min_families=3,
        public_min_backtest_positives=10,
        public_min_auc=0.58,
        public_max_brier_ratio=0.98,
        family_weights={
            "seismic": 0.50,
            "gnss": 0.22,
            "insar": 0.18,
            "goes_lightning_control": 0.10,
        },
        earthdata_token=os.environ.get("EARTHDATA_TOKEN", "").strip(),
    )
    path = region_root / "config" / "dtrg_research.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _restore_region(state_dir: Path, runtime_root: Path, region: WorldRegion) -> Path:
    region_root = runtime_root / "regions" / region.id
    if region_root.exists():
        shutil.rmtree(region_root)
    region_root.mkdir(parents=True, exist_ok=True)
    archive = state_dir / f"{region.id}.zip"
    if archive.exists():
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(region_root)
        except Exception:
            # A corrupt state archive must not prevent a new clean calculation.
            shutil.rmtree(region_root, ignore_errors=True)
            region_root.mkdir(parents=True, exist_ok=True)
    return region_root


def _compact_region(region_root: Path, output_state_dir: Path, region_id: str) -> Path:
    output_state_dir.mkdir(parents=True, exist_ok=True)
    db = default_db(region_root)
    if db.exists():
        try:
            with sqlite3.connect(str(db), timeout=60) as c:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.execute("VACUUM")
        except Exception:
            pass
    for p in region_root.rglob("*"):
        if p.is_dir() and p.name in {"cache", "__pycache__"}:
            shutil.rmtree(p, ignore_errors=True)
    archive = output_state_dir / f"{region_id}.zip"
    tmp = archive.with_suffix(".zip.tmp")
    if tmp.exists():
        tmp.unlink()
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in (Path("config/dtrg_research.json"), Path("mam/mam_data.sqlite")):
            src = region_root / rel
            if src.exists():
                zf.write(src, rel.as_posix())
    tmp.replace(archive)
    return archive


def _source_disabled(db: Path, source: str, reason: str) -> None:
    upsert_source(db, source, "NOT_APPLICABLE", records=0, coverage=0.0, quality=0.0,
                  message=reason, details={"reason": reason}, success=False)


def _latest_events(db: Path, limit: int = 25) -> list[dict[str, Any]]:
    return rows(db, """
        SELECT event_id,event_time,magnitude,depth_km,latitude,longitude,place,url
        FROM dtrg_r_events ORDER BY event_time DESC LIMIT ?
    """, (limit,))


def _event_bounds(db: Path) -> tuple[date | None, date | None]:
    try:
        with sqlite3.connect(str(db), timeout=60) as c:
            row = c.execute(
                "SELECT MIN(substr(event_time,1,10)), MAX(substr(event_time,1,10)) FROM dtrg_r_events"
            ).fetchone()
        oldest = date.fromisoformat(row[0]) if row and row[0] else None
        newest = date.fromisoformat(row[1]) if row and row[1] else None
        return oldest, newest
    except Exception:
        return None, None


def _ingest_usgs_incremental(region_root: Path, db: Path, cfg: ResearchConfig,
                             plan: dict[str, Any], mode: str, today: date) -> dict[str, Any]:
    oldest, newest = _event_bounds(db)
    target_start = today - timedelta(days=int(plan["usgs_days"]))
    overlap = int(plan.get("usgs_overlap_days", 14))
    operations: list[dict[str, Any]] = []

    # Fill missing older history during weekly/bootstrap runs. This is done
    # only when needed; normal fast runs never re-download years of history.
    if oldest and oldest > target_start and mode in {"weekly", "bootstrap"}:
        older_end = oldest - timedelta(days=1)
        if target_start <= older_end:
            operations.append({
                "kind": "historical_backfill",
                "start": target_start,
                "end": older_end,
                "result": ingest_usgs(region_root, db, cfg, target_start, older_end),
            })

    if newest:
        recent_start = max(target_start, newest - timedelta(days=overlap))
    else:
        recent_start = target_start
    operations.append({
        "kind": "recent_incremental",
        "start": recent_start,
        "end": today,
        "result": ingest_usgs(region_root, db, cfg, recent_start, today),
    })
    received = sum(int((op.get("result") or {}).get("records_received") or 0) for op in operations)
    statuses = [str((op.get("result") or {}).get("status") or "UNKNOWN") for op in operations]
    return {
        "status": "OK" if statuses and all(x == "OK" for x in statuses) else "DEGRADED",
        "records_received": received,
        "oldest_before": oldest.isoformat() if oldest else None,
        "newest_before": newest.isoformat() if newest else None,
        "operations": operations,
    }


def _counts(db: Path) -> dict[str, int]:
    tables = [
        "dtrg_r_events", "dtrg_r_gnss_stations", "dtrg_r_gnss_obs", "dtrg_r_goes_files",
        "dtrg_r_goes_daily", "dtrg_r_insar_scenes", "dtrg_r_insar_obs", "dtrg_r_features",
        "dtrg_r_scores", "dtrg_r_backtests",
    ]
    out: dict[str, int] = {}
    with sqlite3.connect(str(db), timeout=60) as c:
        for table in tables:
            try:
                out[table] = int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                out[table] = 0
    return out


def run_region(*, region: WorldRegion, mode: str, state_dir: Path, runtime_root: Path,
               output_state_dir: Path, output_results_dir: Path) -> dict[str, Any]:
    if mode not in MODE_PLAN:
        raise ValueError(f"Modo desconocido: {mode}")
    plan = MODE_PLAN[mode]
    started = utcnow()
    region_root = _restore_region(state_dir, runtime_root, region)
    cfg = _write_region_config(region_root, region)
    cfg.goes_sampling_minutes = int(plan["goes_sampling_minutes"])
    db = default_db(region_root)
    initialize(db)
    results: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    today = date.today()
    now = datetime.now(timezone.utc)

    def guarded(name: str, fn):
        try:
            results[name] = fn()
        except Exception as exc:
            errors.append({"source": name, "error": str(exc), "traceback": traceback.format_exc(limit=8)})
            results[name] = {"status": "ERROR", "error": str(exc)}

    guarded("usgs", lambda: _ingest_usgs_incremental(
        region_root, db, cfg, plan, mode, today,
    ))

    if region.gnss and int(plan["gnss_years"]) > 0:
        guarded("gnss", lambda: ingest_gnss(
            region_root, db, cfg,
            today - timedelta(days=365 * int(plan["gnss_years"])),
        ))
    elif not region.gnss:
        _source_disabled(db, "NGL_GNSS", "GNSS desactivado para esta macroregión")

    if region.goes and int(plan["goes_hours"]) > 0:
        guarded("goes", lambda: ingest_goes(
            region_root, db, cfg,
            now - timedelta(hours=int(plan["goes_hours"])), now,
        ))
    elif not region.goes:
        _source_disabled(db, "NOAA_GOES_GLM", "Fuera de la cobertura regional configurada para GOES-GLM")

    if region.insar_catalog and int(plan["insar_days"]) > 0:
        guarded("insar_catalog", lambda: ingest_insar_catalog(
            region_root, db, cfg,
            now - timedelta(days=int(plan["insar_days"])), now,
        ))
    elif not region.insar_catalog:
        _source_disabled(db, "ASF_SENTINEL1_CATALOG", "Catálogo InSAR desactivado para esta macroregión oceánica")

    insar_files = []
    insar_folder = region_root / "data" / "insar"
    if insar_folder.exists():
        insar_files = [p for p in insar_folder.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".tif", ".tiff", ".nc"}]
    existing_insar_obs = 0
    try:
        with sqlite3.connect(str(db), timeout=60) as c:
            existing_insar_obs = int(c.execute("SELECT COUNT(*) FROM dtrg_r_insar_obs").fetchone()[0])
    except sqlite3.Error:
        existing_insar_obs = 0
    if insar_files:
        guarded("insar_local", lambda: ingest_local_insar(region_root, db))
    elif existing_insar_obs > 0:
        coverage = min(1.0, existing_insar_obs / 12.0)
        upsert_source(db, "LOCAL_INSAR_PRODUCTS", "OK", records=existing_insar_obs,
                      coverage=coverage, quality=0.8 * coverage,
                      message=f"{existing_insar_obs} observaciones InSAR persistidas en la base regional",
                      details={"persisted_observations": existing_insar_obs}, success=True)
        results["insar_local"] = {"status": "OK_PERSISTED", "processed": existing_insar_obs}
    else:
        guarded("insar_local", lambda: ingest_local_insar(region_root, db))

    guarded("calculation", lambda: calculate_history(
        region_root, db, cfg,
        today - timedelta(days=int(plan["calculate_days"])), today,
    ))

    if int(plan["backtest_years"]) > 0:
        guarded("backtest", lambda: run_backtest(
            db, cfg,
            today - timedelta(days=365 * int(plan["backtest_years"])), today, 50.0,
        ))
        # Recalculate the current day after the gate result is stored.
        guarded("post_backtest_calculation", lambda: calculate_history(region_root, db, cfg, today, today))

    cur = current(db)
    status = "OK" if cur.get("iedc_provisional") is not None else "NO_DATA"
    if errors and status == "OK":
        status = "DEGRADED"
    elif errors:
        status = "ERROR"
    payload = {
        "schema_version": 1,
        "model_version": f"SismoAI-World-Cloud-{__version__}",
        "generated_at": utcnow(),
        "started_at": started,
        "mode": mode,
        "status": status,
        "region": {
            "id": region.id,
            "name": region.name,
            "group": region.group,
            "bbox": region.bbox,
            "min_magnitude": region.min_magnitude,
            "backtest_event_magnitude": region.event_magnitude,
        },
        "current": cur,
        "sources": rows(db, "SELECT * FROM dtrg_r_sources ORDER BY source"),
        "counts": _counts(db),
        "latest_backtest": rows(db, "SELECT * FROM dtrg_r_backtests ORDER BY created_at DESC LIMIT 1"),
        "latest_events": _latest_events(db),
        "operation_results": results,
        "errors": errors,
        "scientific_notice": SCIENTIFIC_NOTICE,
        "regional_notice": REGIONAL_NOTICE,
    }
    output_results_dir.mkdir(parents=True, exist_ok=True)
    (output_results_dir / f"{region.id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _compact_region(region_root, output_state_dir, region.id)
    return payload


def run_shard(*, regions_path: Path, mode: str, shard_index: int, shard_count: int,
              state_dir: Path, runtime_root: Path, output_root: Path) -> dict[str, Any]:
    meta, regions = load_regions(regions_path)
    selected = [r for idx, r in enumerate(regions) if idx % shard_count == shard_index]
    output_state = output_root / "state" / "regions"
    output_results = output_root / "results" / "regions"
    summary = {
        "mode": mode,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "selected": [r.id for r in selected],
        "started_at": utcnow(),
        "results": [],
    }
    for region in selected:
        try:
            result = run_region(
                region=region,
                mode=mode,
                state_dir=state_dir,
                runtime_root=runtime_root,
                output_state_dir=output_state,
                output_results_dir=output_results,
            )
            summary["results"].append({"region_id": region.id, "status": result["status"]})
        except Exception as exc:
            summary["results"].append({"region_id": region.id, "status": "FATAL", "error": str(exc)})
            output_results.mkdir(parents=True, exist_ok=True)
            (output_results / f"{region.id}.json").write_text(json.dumps({
                "schema_version": 1,
                "model_version": f"SismoAI-World-Cloud-{__version__}",
                "generated_at": utcnow(),
                "mode": mode,
                "status": "FATAL",
                "region": {"id": region.id, "name": region.name, "group": region.group, "bbox": region.bbox},
                "current": {"iedc_provisional": None, "state": "NO_DATA", "public_valid": False},
                "errors": [{"error": str(exc), "traceback": traceback.format_exc(limit=12)}],
                "scientific_notice": SCIENTIFIC_NOTICE,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["finished_at"] = utcnow()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / f"shard_{shard_index}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .analytics import calculate_history, current
from .backtest import run_backtest
from .config import ResearchConfig
from .db import audit, connect, default_db, initialize, rows
from .sources import ingest_gnss, ingest_goes, ingest_insar_catalog, ingest_local_insar, ingest_usgs
from .util import safe_json, utcnow


class ResearchEngine:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.cfg = ResearchConfig.load(self.root)
        self.db = default_db(self.root)
        initialize(self.db)

    def source_status(self):
        return rows(self.db, "SELECT * FROM dtrg_r_sources ORDER BY source")

    def update(self, *, history_years: int | None = None, quick: bool = False) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        start_at = utcnow()
        with connect(self.db) as c:
            c.execute("INSERT INTO dtrg_r_runs(run_id,command,started_at,status,details_json) VALUES(?,?,?,?,?)",
                      (run_id, "update", start_at, "RUNNING", "{}"))
        results = {}
        status = "OK"
        try:
            years = 1 if quick else (history_years or self.cfg.history_years)
            today = date.today()
            results["usgs"] = ingest_usgs(self.root, self.db, self.cfg, today - timedelta(days=365 * years), today)
            results["gnss"] = ingest_gnss(self.root, self.db, self.cfg, today - timedelta(days=365 * (1 if quick else self.cfg.gnss_history_years)))
            now = datetime.now(timezone.utc)
            hours = min(2, self.cfg.goes_recent_hours) if quick else self.cfg.goes_recent_hours
            results["goes"] = ingest_goes(self.root, self.db, self.cfg, now - timedelta(hours=hours), now)
            results["insar_catalog"] = ingest_insar_catalog(
                self.root, self.db, self.cfg,
                now - timedelta(days=min(30, self.cfg.insar_catalog_days) if quick else self.cfg.insar_catalog_days), now,
            )
            results["insar_local"] = ingest_local_insar(self.root, self.db)
            calc_start = today - timedelta(days=min(365 * years, self.cfg.baseline_days + 365))
            results["calculation"] = calculate_history(self.root, self.db, self.cfg, calc_start, today)
            audit(self.db, "RESEARCH_UPDATE", "OK", results)
        except Exception as exc:
            status = "ERROR"
            results["fatal_error"] = str(exc)
            audit(self.db, "RESEARCH_UPDATE", "ERROR", results)
        finally:
            with connect(self.db) as c:
                c.execute("UPDATE dtrg_r_runs SET finished_at=?,status=?,details_json=? WHERE run_id=?",
                          (utcnow(), status, safe_json(results), run_id))
        return {"run_id": run_id, "status": status, "results": results, "current": current(self.db)}


    def goes_history(self, days: int = 7, sampling_minutes: int | None = None):
        now = datetime.now(timezone.utc)
        if sampling_minutes is not None:
            self.cfg.goes_sampling_minutes = max(1, int(sampling_minutes))
        result = ingest_goes(self.root, self.db, self.cfg, now - timedelta(days=max(1, int(days))), now)
        calculate_history(self.root, self.db, self.cfg, date.today() - timedelta(days=min(365, max(30, days + self.cfg.baseline_days))), date.today())
        return result

    def insar_update(self, days: int | None = None):
        now = datetime.now(timezone.utc)
        catalog_days = max(1, int(days or self.cfg.insar_catalog_days))
        catalog = ingest_insar_catalog(self.root, self.db, self.cfg, now - timedelta(days=catalog_days), now)
        local = ingest_local_insar(self.root, self.db)
        calculation = calculate_history(
            self.root, self.db, self.cfg,
            date.today() - timedelta(days=min(365, self.cfg.baseline_days + 90)),
            date.today(),
        )
        return {"catalog": catalog, "local": local, "calculation": calculation, "current": current(self.db)}

    def calculate(self, days: int = 365):
        today = date.today()
        return calculate_history(self.root, self.db, self.cfg, today - timedelta(days=days), today)

    def backtest(self, years: int = 3, threshold: float = 50.0):
        today = date.today()
        # Recalculate first so the test uses the current formula and available historical inputs.
        calculate_history(self.root, self.db, self.cfg, today - timedelta(days=365 * years), today)
        result = run_backtest(self.db, self.cfg, today - timedelta(days=365 * years), today, threshold)
        # Recalculate current after backtest; public gate may change but remains conservative.
        calculate_history(self.root, self.db, self.cfg, today, today)
        return result

    def status(self):
        return {
            "current": current(self.db),
            "sources": self.source_status(),
            "counts": self.counts(),
            "latest_backtest": rows(self.db, "SELECT * FROM dtrg_r_backtests ORDER BY created_at DESC LIMIT 1"),
        }

    def counts(self):
        tables = ["dtrg_r_events", "dtrg_r_gnss_stations", "dtrg_r_gnss_obs", "dtrg_r_goes_files",
                  "dtrg_r_goes_daily", "dtrg_r_insar_scenes", "dtrg_r_insar_obs", "dtrg_r_features",
                  "dtrg_r_scores", "dtrg_r_backtests"]
        out = {}
        with connect(self.db) as c:
            for t in tables:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out

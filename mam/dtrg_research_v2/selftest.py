from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from .analytics import calculate_history
from .backtest import run_backtest
from .config import ResearchConfig
from .db import connect, initialize
from .util import robust_z


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "config").mkdir()
        cfg = ResearchConfig(baseline_days=30, score_window_days=3, public_min_backtest_positives=1)
        (root / "config" / "dtrg_research.json").write_text(json.dumps(cfg.__dict__), encoding="utf-8")
        db = root / "test.sqlite"
        initialize(db)
        start = date.today() - timedelta(days=120)
        with connect(db) as c:
            for i in range(120):
                d = start + timedelta(days=i)
                count = 1 + (8 if i > 110 else 0)
                for j in range(count):
                    eid = f"e{i}_{j}"
                    c.execute("INSERT INTO dtrg_r_events(event_id,event_time,latitude,longitude,depth_km,magnitude,raw_json,ingested_at) VALUES(?,?,?,?,?,?,?,?)",
                              (eid, d.isoformat()+"T12:00:00Z", 8.0, -66.0, 10.0, 3.0 + (2.5 if i in (114, 118) and j == 0 else 0), "{}", d.isoformat()))
            c.execute("INSERT INTO dtrg_r_sources(source,status,records,coverage,quality,message,details_json) VALUES('USGS_FDSN','OK',120,1,0.95,'test','{}')")
        result = calculate_history(root, db, cfg, date.today() - timedelta(days=60), date.today())
        bt = run_backtest(db, cfg, date.today() - timedelta(days=60), date.today(), 40)
        checks = {
            "robust_statistics": robust_z(20, list(range(20))) is not None,
            "score_numeric": result["current"]["iedc_provisional"] is not None,
            "provisional_visible": result["current"]["iedc_provisional"] == result["current"]["iedc_raw"],
            "public_separate": "iedc_public" in result["current"],
            "reasons": isinstance(result["current"]["reasons"], list),
            "backtest": bt["samples"] > 0,
        }
        ok = all(checks.values())
        print(json.dumps({"status": "OK" if ok else "FAILED", "checks": checks}, ensure_ascii=False, indent=2))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

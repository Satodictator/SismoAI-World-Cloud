from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .util import safe_json, utcnow

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS dtrg_r_schema(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dtrg_r_runs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE NOT NULL, command TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS dtrg_r_sources(
 source TEXT PRIMARY KEY, status TEXT NOT NULL, last_attempt TEXT, last_success TEXT,
 records INTEGER NOT NULL DEFAULT 0, freshness_hours REAL, coverage REAL NOT NULL DEFAULT 0,
 quality REAL NOT NULL DEFAULT 0, message TEXT, details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS dtrg_r_events(
 event_id TEXT PRIMARY KEY, event_time TEXT NOT NULL, updated_at TEXT, latitude REAL NOT NULL,
 longitude REAL NOT NULL, depth_km REAL, magnitude REAL, mag_type TEXT, place TEXT, url TEXT,
 felt INTEGER, significance INTEGER, raw_json TEXT NOT NULL, ingested_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_dtrg_r_events_time ON dtrg_r_events(event_time);
CREATE TABLE IF NOT EXISTS dtrg_r_gnss_stations(
 station TEXT PRIMARY KEY, latitude REAL, longitude REAL, source_url TEXT, discovered_at TEXT NOT NULL,
 last_observation TEXT, observation_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'DISCOVERED',
 details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS dtrg_r_gnss_obs(
 station TEXT NOT NULL, obs_date TEXT NOT NULL, east_m REAL, north_m REAL, up_m REAL,
 sigma_e REAL, sigma_n REAL, sigma_u REAL, source_url TEXT, ingested_at TEXT NOT NULL,
 PRIMARY KEY(station,obs_date));
CREATE INDEX IF NOT EXISTS idx_dtrg_r_gnss_date ON dtrg_r_gnss_obs(obs_date);
CREATE TABLE IF NOT EXISTS dtrg_r_goes_files(
 object_key TEXT PRIMARY KEY, satellite TEXT NOT NULL, start_time TEXT, end_time TEXT, size_bytes INTEGER,
 status TEXT NOT NULL, flash_count INTEGER, region_flash_count INTEGER, energy_sum REAL,
 source_url TEXT, processed_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS dtrg_r_goes_daily(
 day TEXT PRIMARY KEY, flash_count INTEGER NOT NULL, energy_sum REAL, file_count INTEGER NOT NULL,
 coverage REAL NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dtrg_r_insar_scenes(
 scene_id TEXT PRIMARY KEY, start_time TEXT, stop_time TEXT, platform TEXT, beam_mode TEXT,
 flight_direction TEXT, path_number INTEGER, frame_number INTEGER, url TEXT, geometry_json TEXT,
 status TEXT NOT NULL, raw_json TEXT NOT NULL, ingested_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dtrg_r_insar_obs(
 obs_id TEXT PRIMARY KEY, obs_date TEXT NOT NULL, source_file TEXT NOT NULL, displacement_mm REAL,
 abs_displacement_mm REAL, coherence REAL, valid_pixels INTEGER, quality REAL, details_json TEXT NOT NULL,
 ingested_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_dtrg_r_insar_date ON dtrg_r_insar_obs(obs_date);
CREATE TABLE IF NOT EXISTS dtrg_r_features(
 day TEXT NOT NULL, family TEXT NOT NULL, feature TEXT NOT NULL, value REAL, baseline_median REAL,
 robust_z REAL, score REAL, quality REAL, reason TEXT, details_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(day,family,feature));
CREATE TABLE IF NOT EXISTS dtrg_r_scores(
 calculated_at TEXT NOT NULL, day TEXT PRIMARY KEY, region_id TEXT NOT NULL, iedc_raw REAL,
 iedc_provisional REAL, iedc_public REAL, public_valid INTEGER NOT NULL, state TEXT NOT NULL,
 confidence REAL NOT NULL, coverage REAL NOT NULL, data_quality REAL NOT NULL,
 baseline_progress REAL NOT NULL, available_families INTEGER NOT NULL, reasons_json TEXT NOT NULL,
 family_scores_json TEXT NOT NULL, source_status_json TEXT NOT NULL, scientific_notice TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dtrg_r_backtests(
 run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL,
 horizon_days INTEGER NOT NULL, event_magnitude REAL NOT NULL, threshold REAL NOT NULL,
 positives INTEGER NOT NULL, negatives INTEGER NOT NULL, tp INTEGER NOT NULL, fp INTEGER NOT NULL,
 tn INTEGER NOT NULL, fn INTEGER NOT NULL, precision REAL, recall REAL, specificity REAL,
 f1 REAL, auc REAL, brier REAL, base_brier REAL, false_alarms_per_100_days REAL,
 public_gate_pass INTEGER NOT NULL, metrics_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dtrg_r_audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}');
"""


def default_db(root: Path) -> Path:
    preferred = Path(root) / "mam" / "mam_data.sqlite"
    return preferred


@contextlib.contextmanager
def connect(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path: Path):
    with connect(path) as c:
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO dtrg_r_schema(version,applied_at) VALUES(2,?)", (utcnow(),))
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite quick_check failed")


def upsert_source(path: Path, source: str, status: str, *, records: int = 0, coverage: float = 0.0,
                  quality: float = 0.0, freshness_hours: float | None = None, message: str = "",
                  details: dict[str, Any] | None = None, success: bool = False):
    now = utcnow()
    with connect(path) as c:
        c.execute("""
        INSERT INTO dtrg_r_sources(source,status,last_attempt,last_success,records,freshness_hours,coverage,quality,message,details_json)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source) DO UPDATE SET status=excluded.status,last_attempt=excluded.last_attempt,
          last_success=CASE WHEN excluded.last_success IS NOT NULL THEN excluded.last_success ELSE dtrg_r_sources.last_success END,
          records=excluded.records,freshness_hours=excluded.freshness_hours,coverage=excluded.coverage,
          quality=excluded.quality,message=excluded.message,details_json=excluded.details_json
        """, (source, status, now, now if success else None, int(records), freshness_hours, float(coverage),
              float(quality), message[:1000], safe_json(details or {})))


def audit(path: Path, action: str, status: str, details: dict[str, Any] | None = None):
    with connect(path) as c:
        c.execute("INSERT INTO dtrg_r_audit(at,action,status,details_json) VALUES(?,?,?,?)",
                  (utcnow(), action, status, safe_json(details or {})))


def rows(path: Path, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with connect(path) as c:
        return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]


def one(path: Path, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with connect(path) as c:
        r = c.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

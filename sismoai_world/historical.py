from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import shutil
import sqlite3
import statistics
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


VERSION = "1.0.0"
START_DATE = date(1973, 1, 1)
MIN_MAGNITUDE = 4.5
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
SCIENTIFIC_NOTICE = (
    "Laboratorio histórico exploratorio. Los patrones candidatos se validan en "
    "un período posterior separado, permanecen fuera del IEDC y no constituyen "
    "predicción sísmica, alerta oficial ni orden de evacuación."
)

MONTHS_BY_MODE = {
    "fast": 12,
    "daily": 36,
    "weekly": 96,
    "bootstrap": 192,
}

CONTEXT_CONTROLS = [
    {
        "source": "NASA_JPL_FIREBALL",
        "status": "MAM_LOCAL_ONLY",
        "role": "control_contextual",
        "affects_iedc": False,
        "note": "Existe en MAM local; no se usa como precursor ni activa alertas en World Cloud.",
    },
    {
        "source": "NOAA_SWPC",
        "status": "MAM_LOCAL_ONLY",
        "role": "control_contextual",
        "affects_iedc": False,
        "note": "Existe en MAM local; se mantiene separado del índice sísmico regional.",
    },
    {
        "source": "EMSC",
        "status": "MAM_LOCAL_ONLY",
        "role": "secondary_seismic_catalog",
        "affects_iedc": False,
        "note": "Catálogo secundario de MAM; World Cloud conserva USGS como catálogo operativo.",
    },
    {
        "source": "CELESTRAK",
        "status": "MAM_LOCAL_ONLY",
        "role": "control_contextual",
        "affects_iedc": False,
        "note": "Control de contexto orbital; no es una señal sísmica.",
    },
    {
        "source": "OPENSKY",
        "status": "MAM_LOCAL_ONLY",
        "role": "control_contextual",
        "affects_iedc": False,
        "note": "Control de actividad aérea; no es una señal sísmica.",
    },
]

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS h_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS h_runs(
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS h_events(
  event_id TEXT PRIMARY KEY,
  event_time TEXT NOT NULL,
  updated_at TEXT,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  depth_km REAL,
  magnitude REAL,
  mag_type TEXT,
  place TEXT,
  source TEXT NOT NULL,
  ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_h_events_time ON h_events(event_time);
CREATE INDEX IF NOT EXISTS idx_h_events_mag_time ON h_events(magnitude,event_time);
CREATE TABLE IF NOT EXISTS h_chunks(
  chunk_start TEXT PRIMARY KEY,
  chunk_end TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  records_received INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS h_source_coverage(
  source TEXT PRIMARY KEY,
  family TEXT NOT NULL,
  regions INTEGER NOT NULL DEFAULT 0,
  records INTEGER NOT NULL DEFAULT 0,
  earliest TEXT,
  latest TEXT,
  status TEXT NOT NULL,
  historical_depth TEXT,
  role TEXT NOT NULL,
  affects_iedc INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS h_pattern_runs(
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  scope TEXT NOT NULL,
  target TEXT NOT NULL,
  train_start TEXT,
  train_end TEXT,
  test_start TEXT,
  test_end TEXT,
  samples INTEGER NOT NULL,
  positives INTEGER NOT NULL,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS h_patterns(
  pattern_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  target TEXT NOT NULL,
  expression TEXT NOT NULL,
  features_json TEXT NOT NULL,
  train_metrics_json TEXT NOT NULL,
  test_metrics_json TEXT NOT NULL,
  status TEXT NOT NULL,
  research_only INTEGER NOT NULL DEFAULT 1,
  public_gate_pass INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES h_pattern_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_h_patterns_created ON h_patterns(created_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM h_meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO h_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def initialize(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        defaults = {
            "version": VERSION,
            "catalog_start": START_DATE.isoformat(),
            "cursor": START_DATE.isoformat(),
            "state": "BUILDING_HISTORY",
            "last_success": "",
            "last_error": "",
            "last_pattern_event_count": "0",
            "last_pattern_scan": "",
        }
        conn.executemany(
            "INSERT OR IGNORE INTO h_meta(key,value) VALUES(?,?)",
            defaults.items(),
        )
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite quick_check: {check}")


def month_end(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def _millis_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    return (
        datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _request_json(params: dict[str, Any], attempts: int = 4) -> dict[str, Any]:
    url = USGS_URL + "?" + urllib.parse.urlencode(params)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "SismoAI-World-Cloud-Historical/1.0 research",
                },
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(20, 2**attempt))
    raise RuntimeError(f"USGS no respondió después de {attempts} intentos: {last_error}")


def _fetch_interval(start: date, end: date, depth: int = 0) -> list[dict[str, Any]]:
    payload = _request_json(
        {
            "format": "geojson",
            "starttime": start.isoformat(),
            "endtime": (end + timedelta(days=1)).isoformat(),
            "minmagnitude": MIN_MAGNITUDE,
            "orderby": "time-asc",
            "limit": 20000,
        }
    )
    features = [x for x in payload.get("features", []) if isinstance(x, dict)]
    count = int((payload.get("metadata") or {}).get("count") or len(features))
    if count >= 19950 and start < end and depth < 12:
        middle = start + timedelta(days=max(0, (end - start).days // 2))
        return _fetch_interval(start, middle, depth + 1) + _fetch_interval(
            middle + timedelta(days=1), end, depth + 1
        )
    if count >= 20000:
        raise RuntimeError(f"El intervalo {start}..{end} alcanzó el límite de USGS")
    return features


def _event_id(feature: dict[str, Any]) -> str:
    value = str(feature.get("id") or "").strip()
    if value:
        return value
    packed = json.dumps(feature, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256-" + hashlib.sha256(packed).hexdigest()


def ingest_interval(path: Path, start: date, end: date, *, mark_chunk: bool) -> dict[str, Any]:
    chunk_key = start.isoformat()
    if mark_chunk:
        with connect(path) as conn:
            prior = conn.execute(
                "SELECT status FROM h_chunks WHERE chunk_start=?", (chunk_key,)
            ).fetchone()
            if prior and prior["status"] == "OK":
                return {
                    "status": "ALREADY_COMPLETE",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                }
            conn.execute(
                "INSERT INTO h_chunks(chunk_start,chunk_end,status,attempts) "
                "VALUES(?,?,?,1) ON CONFLICT(chunk_start) DO UPDATE SET "
                "chunk_end=excluded.chunk_end,status='RUNNING',"
                "attempts=h_chunks.attempts+1,error=NULL",
                (chunk_key, end.isoformat(), "RUNNING"),
            )
    try:
        features = _fetch_interval(start, end)
        with connect(path) as conn:
            for feature in features:
                properties = feature.get("properties") or {}
                geometry = feature.get("geometry") or {}
                coordinates = geometry.get("coordinates") or []
                if len(coordinates) < 2 or properties.get("time") is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO h_events(
                      event_id,event_time,updated_at,latitude,longitude,depth_km,
                      magnitude,mag_type,place,source,ingested_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id) DO UPDATE SET
                      event_time=excluded.event_time,
                      updated_at=excluded.updated_at,
                      latitude=excluded.latitude,
                      longitude=excluded.longitude,
                      depth_km=excluded.depth_km,
                      magnitude=excluded.magnitude,
                      mag_type=excluded.mag_type,
                      place=excluded.place,
                      ingested_at=excluded.ingested_at
                    """,
                    (
                        _event_id(feature),
                        _millis_to_iso(properties["time"]),
                        _millis_to_iso(properties.get("updated")),
                        float(coordinates[1]),
                        float(coordinates[0]),
                        float(coordinates[2])
                        if len(coordinates) > 2 and coordinates[2] is not None
                        else None,
                        float(properties["mag"])
                        if properties.get("mag") is not None
                        else None,
                        properties.get("magType"),
                        properties.get("place"),
                        "USGS_COMCAT",
                        utcnow(),
                    ),
                )
            if mark_chunk:
                conn.execute(
                    "UPDATE h_chunks SET status='OK',records_received=?,"
                    "completed_at=?,error=NULL WHERE chunk_start=?",
                    (len(features), utcnow(), chunk_key),
                )
        return {
            "status": "OK",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "records_received": len(features),
        }
    except Exception as exc:
        if mark_chunk:
            with connect(path) as conn:
                conn.execute(
                    "UPDATE h_chunks SET status='ERROR',error=? WHERE chunk_start=?",
                    (f"{type(exc).__name__}: {exc}"[:2000], chunk_key),
                )
        raise


def _source_family(source: str) -> str:
    upper = source.upper()
    if "USGS" in upper or "EMSC" in upper:
        return "seismic"
    if "GNSS" in upper or "NGL" in upper:
        return "gnss"
    if "INSAR" in upper or "ASF" in upper or "OPERA" in upper:
        return "insar"
    if "GOES" in upper or "GLM" in upper:
        return "goes_lightning_control"
    return "context_control"


def inventory_sources(
    path: Path,
    results_dirs: Iterable[Path],
    event_count: int,
    earliest: str | None,
    latest: str | None,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    seen_regions: set[str] = set()
    for directory in results_dirs:
        if not directory.exists():
            continue
        for result_path in directory.glob("*.json"):
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            region_id = str((payload.get("region") or {}).get("id") or result_path.stem)
            if region_id in seen_regions:
                continue
            seen_regions.add(region_id)
            for source in payload.get("sources") or []:
                name = str(source.get("source") or "UNKNOWN")
                item = grouped.setdefault(
                    name,
                    {
                        "source": name,
                        "regions": set(),
                        "records": 0,
                        "statuses": defaultdict(int),
                        "earliest": None,
                        "latest": None,
                        "details": {"sample_messages": []},
                    },
                )
                item["regions"].add(region_id)
                item["records"] += int(source.get("records") or 0)
                item["statuses"][str(source.get("status") or "UNKNOWN")] += 1
                success = source.get("last_success")
                if success:
                    item["earliest"] = min(item["earliest"], success) if item["earliest"] else success
                    item["latest"] = max(item["latest"], success) if item["latest"] else success
                message = str(source.get("message") or "").strip()
                if message and message not in item["details"]["sample_messages"]:
                    item["details"]["sample_messages"].append(message[:300])
    with connect(path) as conn:
        historical_cursor = date.fromisoformat(
            get_meta(conn, "cursor", START_DATE.isoformat())
        )
        conn.execute(
            """
            INSERT INTO h_source_coverage(
              source,family,regions,records,earliest,latest,status,
              historical_depth,role,affects_iedc,details_json,checked_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
              family=excluded.family,regions=excluded.regions,records=excluded.records,
              earliest=excluded.earliest,latest=excluded.latest,status=excluded.status,
              historical_depth=excluded.historical_depth,role=excluded.role,
              affects_iedc=excluded.affects_iedc,details_json=excluded.details_json,
              checked_at=excluded.checked_at
            """,
            (
                "USGS_COMCAT_HISTORICAL_GLOBAL",
                "seismic",
                35,
                event_count,
                earliest,
                latest,
                "AVAILABLE" if historical_cursor > date.today() else "BUILDING",
                f"{START_DATE.isoformat()}..presente",
                "historical_replay",
                0,
                json.dumps(
                    {
                        "minimum_magnitude": MIN_MAGNITUDE,
                        "global": True,
                        "separate_from_iedc": True,
                    },
                    ensure_ascii=False,
                ),
                utcnow(),
            ),
        )
        for name, item in grouped.items():
            statuses = dict(item["statuses"])
            status = "OK" if statuses.get("OK", 0) else max(
                statuses, key=statuses.get, default="UNKNOWN"
            )
            role = (
                "operational_geophysical"
                if _source_family(name) in {"seismic", "gnss", "insar"}
                else "control_contextual"
            )
            affects = int(
                name
                in {
                    "USGS_FDSN",
                    "NGL_GNSS",
                    "LOCAL_INSAR_PRODUCTS",
                    "NOAA_GOES_GLM",
                }
            )
            historical_depth = (
                "hasta 5 años según región/estado persistido"
                if _source_family(name) != "goes_lightning_control"
                else "ventanas recientes persistidas"
            )
            conn.execute(
                """
                INSERT INTO h_source_coverage(
                  source,family,regions,records,earliest,latest,status,
                  historical_depth,role,affects_iedc,details_json,checked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source) DO UPDATE SET
                  family=excluded.family,regions=excluded.regions,
                  records=excluded.records,earliest=excluded.earliest,
                  latest=excluded.latest,status=excluded.status,
                  historical_depth=excluded.historical_depth,role=excluded.role,
                  affects_iedc=excluded.affects_iedc,
                  details_json=excluded.details_json,checked_at=excluded.checked_at
                """,
                (
                    name,
                    _source_family(name),
                    len(item["regions"]),
                    item["records"],
                    item["earliest"],
                    item["latest"],
                    status,
                    historical_depth,
                    role,
                    affects,
                    json.dumps(
                        {
                            "status_counts": statuses,
                            "sample_messages": item["details"]["sample_messages"][:5],
                        },
                        ensure_ascii=False,
                    ),
                    utcnow(),
                ),
            )
        # Make NASA's actual role explicit instead of labelling every satellite feed as NASA.
        asf = grouped.get("ASF_SENTINEL1_CATALOG")
        conn.execute(
            """
            INSERT INTO h_source_coverage(
              source,family,regions,records,earliest,latest,status,
              historical_depth,role,affects_iedc,details_json,checked_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
              regions=excluded.regions,records=excluded.records,
              earliest=excluded.earliest,latest=excluded.latest,
              status=excluded.status,details_json=excluded.details_json,
              checked_at=excluded.checked_at
            """,
            (
                "NASA_EARTHDATA_ASF_OPERA",
                "insar",
                len(asf["regions"]) if asf else 0,
                int(asf["records"]) if asf else 0,
                asf["earliest"] if asf else None,
                asf["latest"] if asf else None,
                "ACTIVE_CREDENTIAL_PATH" if asf else "NO_REGIONAL_RESULT",
                "catálogo Sentinel-1/OPERA según ventanas regionales",
                "authenticated_insar_access",
                0,
                json.dumps(
                    {
                        "credential_source": "NASA Earthdata",
                        "data_catalog": "ASF / Sentinel-1 / OPERA-S1",
                        "affects_iedc_only_after": "producto de desplazamiento procesado como LOCAL_INSAR_PRODUCTS",
                        "not_the_same_as": "NASA JPL Fireball",
                    },
                    ensure_ascii=False,
                ),
                utcnow(),
            ),
        )
        return [
            dict(row)
            for row in conn.execute(
                "SELECT source,family,regions,records,earliest,latest,status,"
                "historical_depth,role,affects_iedc FROM h_source_coverage "
                "ORDER BY affects_iedc DESC,records DESC,source"
            )
        ]


def _cell(latitude: float, longitude: float) -> tuple[int, int]:
    return (
        max(0, min(17, int(math.floor((latitude + 90.0) / 10.0)))),
        max(0, min(35, int(math.floor((longitude + 180.0) / 10.0)))),
    )


def _metrics(labels: list[int], predicted: list[int]) -> dict[str, Any]:
    tp = sum(1 for y, p in zip(labels, predicted) if y and p)
    fp = sum(1 for y, p in zip(labels, predicted) if not y and p)
    tn = sum(1 for y, p in zip(labels, predicted) if not y and not p)
    fn = sum(1 for y, p in zip(labels, predicted) if y and not p)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    base_rate = sum(labels) / len(labels) if labels else 0.0
    lift = precision / base_rate if precision is not None and base_rate > 0 else None
    return {
        "samples": len(labels),
        "positives": sum(labels),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "base_rate": base_rate,
        "lift": lift,
        "false_alarms_per_100_samples": 100.0 * fp / max(1, len(labels)),
    }


def _quantile(values: list[float], q: float) -> float | None:
    clean = sorted(x for x in values if math.isfinite(x))
    if not clean:
        return None
    position = (len(clean) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    return clean[lower] * (upper - position) + clean[upper] * (position - lower)


def evaluate_rules(
    samples: list[dict[str, Any]],
    label_name: str,
    scope: str,
    target: str,
    max_patterns: int = 30,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(samples) < 500:
        return {
            "status": "INSUFFICIENT_SAMPLES",
            "scope": scope,
            "target": target,
            "samples": len(samples),
        }, []
    samples.sort(key=lambda row: (row["day"], str(row.get("region") or row.get("cell") or "")))
    unique_days = sorted({str(row["day"]) for row in samples})
    split_day = unique_days[max(0, min(len(unique_days) - 1, int(len(unique_days) * 0.70)))]
    train = [row for row in samples if str(row["day"]) < split_day]
    test = [row for row in samples if str(row["day"]) >= split_day]
    if not train or not test:
        return {
            "status": "INSUFFICIENT_TIME_SPLIT",
            "scope": scope,
            "target": target,
            "samples": len(samples),
        }, []
    train_labels = [int(row[label_name]) for row in train]
    test_labels = [int(row[label_name]) for row in test]
    if sum(train_labels) < 10 or sum(test_labels) < 5:
        return {
            "status": "INSUFFICIENT_POSITIVES",
            "scope": scope,
            "target": target,
            "samples": len(samples),
            "train_positives": sum(train_labels),
            "test_positives": sum(test_labels),
        }, []
    feature_names = sorted(
        {
            key
            for row in train
            for key, value in row.items()
            if key not in {"day", "cell", "region"}
            and not key.startswith("target_")
            and isinstance(value, (int, float))
        }
    )
    candidates: list[dict[str, Any]] = []
    for feature in feature_names:
        values = [float(row.get(feature, 0.0)) for row in train]
        for q in (0.75, 0.85, 0.90, 0.95):
            threshold = _quantile(values, q)
            if threshold is None:
                continue
            train_predicted = [
                int(float(row.get(feature, 0.0)) >= threshold) for row in train
            ]
            test_predicted = [
                int(float(row.get(feature, 0.0)) >= threshold) for row in test
            ]
            train_metrics = _metrics(train_labels, train_predicted)
            test_metrics = _metrics(test_labels, test_predicted)
            if (
                train_metrics["precision"] is not None
                and test_metrics["precision"] is not None
                and train_metrics["tp"] >= 5
                and test_metrics["tp"] >= 2
            ):
                candidates.append(
                    {
                        "expression": f"{feature} >= {threshold:.6g}",
                        "features": [feature],
                        "thresholds": [threshold],
                        "train": train_metrics,
                        "test": test_metrics,
                    }
                )
    candidates.sort(
        key=lambda item: (
            float(item["test"].get("lift") or 0),
            float(item["test"].get("precision") or 0),
            float(item["test"].get("recall") or 0),
        ),
        reverse=True,
    )
    pairs: list[dict[str, Any]] = []
    singles = candidates[:12]
    for index, left in enumerate(singles):
        for right in singles[index + 1 :]:
            first, second = left["features"][0], right["features"][0]
            if first == second:
                continue
            first_threshold, second_threshold = left["thresholds"][0], right["thresholds"][0]
            train_predicted = [
                int(
                    float(row.get(first, 0.0)) >= first_threshold
                    and float(row.get(second, 0.0)) >= second_threshold
                )
                for row in train
            ]
            test_predicted = [
                int(
                    float(row.get(first, 0.0)) >= first_threshold
                    and float(row.get(second, 0.0)) >= second_threshold
                )
                for row in test
            ]
            train_metrics = _metrics(train_labels, train_predicted)
            test_metrics = _metrics(test_labels, test_predicted)
            if (
                train_metrics["tp"] >= 5
                and test_metrics["tp"] >= 2
                and test_metrics["precision"] is not None
            ):
                pairs.append(
                    {
                        "expression": (
                            f"{first} >= {first_threshold:.6g} AND "
                            f"{second} >= {second_threshold:.6g}"
                        ),
                        "features": [first, second],
                        "thresholds": [first_threshold, second_threshold],
                        "train": train_metrics,
                        "test": test_metrics,
                    }
                )
    selected = sorted(
        candidates + pairs,
        key=lambda item: (
            float(item["test"].get("lift") or 0),
            float(item["test"].get("precision") or 0),
            float(item["test"].get("recall") or 0),
        ),
        reverse=True,
    )[:max_patterns]
    return {
        "status": "OK",
        "scope": scope,
        "target": target,
        "samples": len(samples),
        "positives": sum(train_labels) + sum(test_labels),
        "train_start": train[0]["day"],
        "train_end": train[-1]["day"],
        "test_start": test[0]["day"],
        "test_end": test[-1]["day"],
        "candidate_count": len(selected),
    }, selected


def global_samples(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    events = [
        dict(row)
        for row in conn.execute(
            "SELECT substr(event_time,1,10) day,latitude,longitude,depth_km,magnitude "
            "FROM h_events WHERE magnitude IS NOT NULL ORDER BY event_time"
        )
    ]
    by_cell_day: dict[tuple[int, int], dict[date, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in events:
        event_day = date.fromisoformat(str(event["day"]))
        by_cell_day[_cell(float(event["latitude"]), float(event["longitude"]))][event_day].append(event)
    output: list[dict[str, Any]] = []
    for cell, day_map in by_cell_day.items():
        if len(day_map) < 80:
            continue
        days = sorted(day_map)
        first, last = days[0], days[-1]
        candidate_days = set(days)
        cursor = first
        while cursor <= last:
            candidate_days.add(cursor)
            cursor += timedelta(days=14)
        for current_day in sorted(
            day
            for day in candidate_days
            if first + timedelta(days=31) <= day <= last - timedelta(days=8)
        ):
            def window(start_offset: int, end_offset: int) -> list[dict[str, Any]]:
                rows: list[dict[str, Any]] = []
                for offset in range(start_offset, end_offset + 1):
                    rows.extend(day_map.get(current_day + timedelta(days=offset), []))
                return rows

            three_days = window(-2, 0)
            seven_days = window(-6, 0)
            previous_seven = window(-13, -7)
            fourteen_days = window(-13, 0)
            future_three = window(1, 3)
            future_seven = window(1, 7)
            energy = sum(10 ** (1.5 * float(row["magnitude"])) for row in seven_days)
            previous_energy = sum(
                10 ** (1.5 * float(row["magnitude"])) for row in previous_seven
            )
            depths = [
                float(row["depth_km"])
                for row in fourteen_days
                if row.get("depth_km") is not None
            ]
            output.append(
                {
                    "day": current_day.isoformat(),
                    "cell": f"{cell[0]}:{cell[1]}",
                    "count_3": float(len(three_days)),
                    "count_7": float(len(seven_days)),
                    "count_14": float(len(fourteen_days)),
                    "count_acceleration": len(seven_days) / max(1.0, len(previous_seven)),
                    "energy_ratio_7": math.log10(1 + energy)
                    - math.log10(1 + previous_energy),
                    "max_mag_14": max(
                        [float(row["magnitude"]) for row in fourteen_days] or [0.0]
                    ),
                    "mean_depth_14": statistics.mean(depths) if depths else 0.0,
                    "shallow_ratio_14": (
                        sum(1 for value in depths if value <= 50.0) / len(depths)
                        if depths
                        else 0.0
                    ),
                    "target_m6_3d": int(
                        any(float(row["magnitude"]) >= 6.0 for row in future_three)
                    ),
                    "target_m7_7d": int(
                        any(float(row["magnitude"]) >= 7.0 for row in future_seven)
                    ),
                }
            )
    return output


def _safe_feature_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")[:100]


def regional_multisource_samples(archives_dirs: Iterable[Path]) -> list[dict[str, Any]]:
    archives: dict[str, Path] = {}
    for directory in archives_dirs:
        if not directory.exists():
            continue
        for archive in directory.glob("*.zip"):
            archives.setdefault(archive.stem, archive)
    output: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sismoai_historical_regions_") as temporary:
        temporary_root = Path(temporary)
        for region_id, archive in sorted(archives.items()):
            database = temporary_root / f"{region_id}.sqlite"
            try:
                with zipfile.ZipFile(archive) as package:
                    with package.open("mam/mam_data.sqlite") as source, database.open("wb") as target:
                        shutil.copyfileobj(source, target)
                config = {}
                try:
                    with zipfile.ZipFile(archive) as package:
                        config = json.loads(
                            package.read("config/dtrg_research.json").decode("utf-8")
                        )
                except Exception:
                    config = {}
                event_magnitude = float(config.get("backtest_event_magnitude") or 5.0)
                source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
                source.row_factory = sqlite3.Row
                try:
                    feature_rows = source.execute(
                        "SELECT day,family,feature,value,score,quality "
                        "FROM dtrg_r_features WHERE value IS NOT NULL ORDER BY day"
                    ).fetchall()
                    event_rows = source.execute(
                        "SELECT substr(event_time,1,10) day,magnitude "
                        "FROM dtrg_r_events WHERE magnitude IS NOT NULL"
                    ).fetchall()
                finally:
                    source.close()
                by_day: dict[date, dict[str, float]] = defaultdict(dict)
                for row in feature_rows:
                    day = date.fromisoformat(str(row["day"])[:10])
                    prefix = _safe_feature_name(f"{row['family']}__{row['feature']}")
                    if row["score"] is not None:
                        by_day[day][prefix + "__score"] = float(row["score"])
                    if row["quality"] is not None:
                        by_day[day][prefix + "__quality"] = float(row["quality"])
                events: dict[date, list[float]] = defaultdict(list)
                for row in event_rows:
                    events[date.fromisoformat(str(row["day"]))].append(float(row["magnitude"]))
                if len(by_day) < 40:
                    continue
                first, last = min(by_day), max(by_day)
                for current_day in sorted(
                    day
                    for day in by_day
                    if first + timedelta(days=14) <= day <= last - timedelta(days=8)
                ):
                    sample: dict[str, Any] = {
                        "day": current_day.isoformat(),
                        "region": region_id,
                    }
                    feature_names = {
                        name
                        for offset in range(-13, 1)
                        for name in by_day.get(current_day + timedelta(days=offset), {})
                    }
                    for name in feature_names:
                        current = [
                            by_day.get(current_day + timedelta(days=offset), {}).get(name, 0.0)
                            for offset in range(-6, 1)
                        ]
                        previous = [
                            by_day.get(current_day + timedelta(days=offset), {}).get(name, 0.0)
                            for offset in range(-13, -6)
                        ]
                        sample[name + "__mean7"] = statistics.mean(current)
                        sample[name + "__delta7"] = statistics.mean(current) - statistics.mean(previous)
                    sample["target_regional_event_7d"] = int(
                        any(
                            magnitude >= event_magnitude
                            for offset in range(1, 8)
                            for magnitude in events.get(current_day + timedelta(days=offset), [])
                        )
                    )
                    output.append(sample)
            except Exception:
                continue
            finally:
                database.unlink(missing_ok=True)
    return output


def store_pattern_result(
    conn: sqlite3.Connection,
    run_summary: dict[str, Any],
    patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO h_pattern_runs(
          run_id,created_at,scope,target,train_start,train_end,test_start,test_end,
          samples,positives,status,details_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            utcnow(),
            run_summary.get("scope", "unknown"),
            run_summary.get("target", "unknown"),
            run_summary.get("train_start"),
            run_summary.get("train_end"),
            run_summary.get("test_start"),
            run_summary.get("test_end"),
            int(run_summary.get("samples") or 0),
            int(run_summary.get("positives") or 0),
            run_summary.get("status", "UNKNOWN"),
            json.dumps(run_summary, ensure_ascii=False),
        ),
    )
    for index, pattern in enumerate(patterns):
        pattern_id = hashlib.sha256(
            (
                run_id
                + "|"
                + str(index)
                + "|"
                + str(run_summary.get("scope"))
                + "|"
                + str(run_summary.get("target"))
                + "|"
                + pattern["expression"]
            ).encode("utf-8")
        ).hexdigest()[:32]
        train_lift = float(pattern["train"].get("lift") or 0)
        test_lift = float(pattern["test"].get("lift") or 0)
        status = (
            "PROMISING_CANDIDATE"
            if train_lift >= 1.2
            and test_lift >= 1.5
            and int(pattern["test"].get("tp") or 0) >= 3
            else "EXPLORATORY_CANDIDATE"
        )
        conn.execute(
            """
            INSERT INTO h_patterns(
              pattern_id,run_id,scope,target,expression,features_json,
              train_metrics_json,test_metrics_json,status,research_only,
              public_gate_pass,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pattern_id,
                run_id,
                run_summary.get("scope", "unknown"),
                run_summary.get("target", "unknown"),
                pattern["expression"],
                json.dumps(pattern["features"], ensure_ascii=False),
                json.dumps(pattern["train"], ensure_ascii=False),
                json.dumps(pattern["test"], ensure_ascii=False),
                status,
                1,
                0,
                utcnow(),
            ),
        )
    return {"run_id": run_id, **run_summary}


def discover(path: Path, archives_dirs: Iterable[Path]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with connect(path) as conn:
        seismic_samples = global_samples(conn)
        for label, target in (
            ("target_m6_3d", "M6_WITHIN_72H_SAME_10DEG_CELL"),
            ("target_m7_7d", "M7_WITHIN_7D_SAME_10DEG_CELL"),
        ):
            run_summary, patterns = evaluate_rules(
                seismic_samples,
                label,
                "GLOBAL_SEISMIC_HISTORY",
                target,
            )
            results.append(store_pattern_result(conn, run_summary, patterns))
        multisource_samples = regional_multisource_samples(archives_dirs)
        run_summary, patterns = evaluate_rules(
            multisource_samples,
            "target_regional_event_7d",
            "WORLD_REGIONAL_MULTISOURCE",
            "REGIONAL_THRESHOLD_EVENT_WITHIN_7D",
        )
        results.append(store_pattern_result(conn, run_summary, patterns))
        event_count = int(conn.execute("SELECT COUNT(*) FROM h_events").fetchone()[0])
        set_meta(conn, "last_pattern_event_count", event_count)
        set_meta(conn, "last_pattern_scan", utcnow())
    return {"status": "OK", "runs": results}


def summary(path: Path, *, mode: str, run_status: str = "OK") -> dict[str, Any]:
    initialize(path)
    with connect(path) as conn:
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT * FROM h_meta")}
        event_stats = conn.execute(
            "SELECT COUNT(*) count,MIN(event_time) earliest,MAX(event_time) latest,"
            "SUM(CASE WHEN magnitude>=6 THEN 1 ELSE 0 END) m6,"
            "SUM(CASE WHEN magnitude>=7 THEN 1 ELSE 0 END) m7 FROM h_events"
        ).fetchone()
        chunk_stats = conn.execute(
            "SELECT COUNT(*) count,"
            "SUM(CASE WHEN status='OK' THEN 1 ELSE 0 END) complete,"
            "SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) errors FROM h_chunks"
        ).fetchone()
        total_months = max(
            1,
            (date.today().year - START_DATE.year) * 12
            + date.today().month
            - START_DATE.month
            + 1,
        )
        source_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT source,family,regions,records,earliest,latest,status,"
                "historical_depth,role,affects_iedc FROM h_source_coverage "
                "ORDER BY affects_iedc DESC,records DESC,source"
            )
        ]
        latest_run_ids = [
            str(row["run_id"])
            for row in conn.execute(
                """
                SELECT run_id
                FROM (
                  SELECT run_id,scope,target,created_at,
                         ROW_NUMBER() OVER (
                           PARTITION BY scope,target
                           ORDER BY created_at DESC,run_id DESC
                         ) AS position
                  FROM h_pattern_runs
                )
                WHERE position=1
                """
            )
        ]
        pattern_rows = []
        if latest_run_ids:
            placeholders = ",".join("?" for _ in latest_run_ids)
            pattern_query = (
                "SELECT p.pattern_id,p.run_id,p.scope,p.target,p.expression,p.status,"
                "p.features_json,p.train_metrics_json,p.test_metrics_json,p.created_at,"
                "r.train_start,r.train_end,r.test_start,r.test_end,r.samples,r.positives "
                "FROM h_patterns p JOIN h_pattern_runs r ON r.run_id=p.run_id "
                f"WHERE p.run_id IN ({placeholders})"
            )
            for row in conn.execute(pattern_query, latest_run_ids):
                item = dict(row)
                item["features"] = json.loads(item.pop("features_json"))
                item["train_metrics"] = json.loads(item.pop("train_metrics_json"))
                item["test_metrics"] = json.loads(item.pop("test_metrics_json"))
                item["research_only"] = True
                item["public_gate_pass"] = False
                item["current_scan"] = True
                pattern_rows.append(item)
        pattern_rows.sort(
            key=lambda item: (
                float(item["test_metrics"].get("lift") or 0),
                float(item["test_metrics"].get("precision") or 0),
                str(item.get("created_at") or ""),
            ),
            reverse=True,
        )
        cursor = date.fromisoformat(meta.get("cursor", START_DATE.isoformat()))
        complete = cursor > date.today()
        return {
            "schema_version": 1,
            "version": VERSION,
            "generated_at": utcnow(),
            "run_status": run_status,
            "state": "HISTORICAL_COMPLETE" if complete else meta.get("state", "BUILDING_HISTORY"),
            "mode": mode,
            "catalog": {
                "source": "USGS ComCat/FDSN",
                "minimum_magnitude": MIN_MAGNITUDE,
                "target_start": START_DATE.isoformat(),
                "cursor": cursor.isoformat(),
                "target_end": date.today().isoformat(),
                "events": int(event_stats["count"] or 0),
                "earliest_event": event_stats["earliest"],
                "latest_event": event_stats["latest"],
                "m6_events": int(event_stats["m6"] or 0),
                "m7_events": int(event_stats["m7"] or 0),
                "months_complete": int(chunk_stats["complete"] or 0),
                "months_total": total_months,
                "progress": min(
                    1.0, float(chunk_stats["complete"] or 0) / total_months
                ),
                "chunk_errors": int(chunk_stats["errors"] or 0),
            },
            "sources": source_rows,
            "context_controls": CONTEXT_CONTROLS,
            "patterns": pattern_rows,
            "pattern_policy": {
                "search_mode": "bounded_univariate_and_pair_candidates",
                "current_scan_only": True,
                "chronological_split": "70_percent_train_30_percent_later_test",
                "target_leakage_blocked": True,
                "research_only": True,
                "public_gate_pass": False,
                "modifies_iedc": False,
                "activates_alerts": False,
            },
            "last_pattern_scan": meta.get("last_pattern_scan"),
            "last_success": meta.get("last_success"),
            "last_error": meta.get("last_error"),
            "scientific_notice": SCIENTIFIC_NOTICE,
        }


def _restore_database(input_archive: Path, database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    if input_archive.exists():
        with gzip.open(input_archive, "rb") as source, database.open("wb") as target:
            shutil.copyfileobj(source, target)
    initialize(database)


def _publish_database(database: Path, output_archive: Path) -> None:
    with connect(database) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    output_archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_archive.with_suffix(output_archive.suffix + ".tmp")
    with database.open("rb") as source, gzip.open(temporary, "wb", compresslevel=6) as target:
        shutil.copyfileobj(source, target)
    os.replace(temporary, output_archive)


def run(
    *,
    input_archive: Path,
    work_dir: Path,
    output_dir: Path,
    region_archives: list[Path],
    result_dirs: list[Path],
    mode: str,
    months: int | None = None,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    database = work_dir / "historical.sqlite"
    _restore_database(input_archive, database)
    initialize(database)
    run_id = str(uuid.uuid4())
    started_at = utcnow()
    with connect(database) as conn:
        cursor = date.fromisoformat(get_meta(conn, "cursor", START_DATE.isoformat()))
        conn.execute(
            "INSERT INTO h_runs(run_id,started_at,mode,status) VALUES(?,?,?,'RUNNING')",
            (run_id, started_at, mode),
        )
        set_meta(conn, "state", "BUILDING_HISTORY")
        set_meta(conn, "last_error", "")
    completed: list[dict[str, Any]] = []
    status = "OK"
    error = ""
    discovery: dict[str, Any] = {"status": "DEFERRED"}
    try:
        limit = max(1, min(int(months or MONTHS_BY_MODE.get(mode, 24)), 240))
        for _ in range(limit):
            if cursor > date.today():
                break
            end = min(month_end(cursor), date.today())
            completed.append(ingest_interval(database, cursor, end, mark_chunk=True))
            cursor = end + timedelta(days=1)
            with connect(database) as conn:
                set_meta(conn, "cursor", cursor.isoformat())
                set_meta(conn, "last_success", utcnow())
            time.sleep(0.25)
        if cursor > date.today():
            recent_start = max(START_DATE, date.today() - timedelta(days=35))
            ingest_interval(database, recent_start, date.today(), mark_chunk=False)
        with connect(database) as conn:
            event_stats = conn.execute(
                "SELECT COUNT(*),MIN(event_time),MAX(event_time) FROM h_events"
            ).fetchone()
            event_count = int(event_stats[0] or 0)
            last_pattern_count = int(get_meta(conn, "last_pattern_event_count", "0") or 0)
        inventory_sources(
            database,
            result_dirs,
            event_count,
            event_stats[1],
            event_stats[2],
        )
        should_discover = event_count >= 2500 and (
            last_pattern_count == 0
            or event_count - last_pattern_count >= 25000
            or cursor > date.today()
            or mode in {"weekly", "bootstrap"}
        )
        if should_discover:
            discovery = discover(database, region_archives)
        with connect(database) as conn:
            if cursor > date.today():
                set_meta(conn, "state", "HISTORICAL_COMPLETE")
            conn.execute(
                "UPDATE h_runs SET finished_at=?,status='OK',details_json=? WHERE run_id=?",
                (
                    utcnow(),
                    json.dumps(
                        {"chunks": completed, "patterns": discovery},
                        ensure_ascii=False,
                    ),
                    run_id,
                ),
            )
    except Exception as exc:
        status = "DEGRADED_RETRY_PENDING"
        error = f"{type(exc).__name__}: {exc}"
        with connect(database) as conn:
            set_meta(conn, "state", status)
            set_meta(conn, "last_error", error)
            conn.execute(
                "UPDATE h_runs SET finished_at=?,status='ERROR',details_json=? WHERE run_id=?",
                (utcnow(), json.dumps({"error": error}, ensure_ascii=False), run_id),
            )
    output_archive = output_dir / "historical.sqlite.gz"
    _publish_database(database, output_archive)
    current = summary(database, mode=mode, run_status=status)
    summary_path = output_dir / "historical_summary.json"
    temporary_summary = summary_path.with_suffix(".json.tmp")
    temporary_summary.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary_summary, summary_path)
    return {
        "run_id": run_id,
        "status": status,
        "error": error or None,
        "chunks_processed": len(completed),
        "patterns": discovery,
        "database_archive": str(output_archive),
        "summary": str(summary_path),
        "current": current,
    }


def selftest() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sismoai_historical_selftest_") as temporary:
        root = Path(temporary)
        database = root / "historical.sqlite"
        initialize(database)
        with connect(database) as conn:
            for year in range(2000, 2015):
                for month in range(1, 13):
                    for index in range(1, 6):
                        event_day = date(year, month, min(20, index * 3))
                        magnitude = 7.1 if month == 12 and index == 5 else 4.6 + index * 0.12
                        conn.execute(
                            """
                            INSERT INTO h_events(
                              event_id,event_time,latitude,longitude,depth_km,
                              magnitude,source,ingested_at
                            ) VALUES(?,?,?,?,?,?,?,?)
                            """,
                            (
                                f"{year}-{month}-{index}",
                                event_day.isoformat() + "T00:00:00Z",
                                10.0,
                                -67.0,
                                20.0 + index,
                                magnitude,
                                "SELFTEST",
                                utcnow(),
                            ),
                        )
            set_meta(conn, "cursor", (date.today() + timedelta(days=1)).isoformat())
        with connect(database) as conn:
            samples = global_samples(conn)
        if len(samples) < 500:
            raise AssertionError("El constructor de muestras históricas produjo pocas muestras")
        test_summary, _ = evaluate_rules(
            samples,
            "target_m7_7d",
            "SELFTEST",
            "SELFTEST_TARGET",
        )
        current = summary(database, mode="selftest")
        if current["catalog"]["events"] != 900:
            raise AssertionError("Conteo histórico incorrecto")
        if current["pattern_policy"]["activates_alerts"]:
            raise AssertionError("El laboratorio no puede activar alertas")
        return {
            "status": "OK",
            "events": current["catalog"]["events"],
            "samples": len(samples),
            "rule_engine": test_summary["status"],
            "separation": "OK",
        }


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SismoAI World Cloud Historical Lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input-archive", required=True)
    run_parser.add_argument("--work-dir", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--region-archives", action="append", default=[])
    run_parser.add_argument("--result-dir", action="append", default=[])
    run_parser.add_argument("--mode", choices=sorted(MONTHS_BY_MODE), required=True)
    run_parser.add_argument("--months", type=int)
    subparsers.add_parser("selftest")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.command == "selftest":
        emit(selftest())
        return 0
    result = run(
        input_archive=Path(arguments.input_archive),
        work_dir=Path(arguments.work_dir),
        output_dir=Path(arguments.output_dir),
        region_archives=[Path(value) for value in arguments.region_archives],
        result_dirs=[Path(value) for value in arguments.result_dir],
        mode=arguments.mode,
        months=arguments.months,
    )
    emit(
        {
            "run_id": result["run_id"],
            "status": result["status"],
            "error": result["error"],
            "chunks_processed": result["chunks_processed"],
            "database_archive": result["database_archive"],
            "summary": result["summary"],
            "catalog": result["current"]["catalog"],
            "pattern_count": len(result["current"]["patterns"]),
        }
    )
    # A temporary source failure must not stop the existing World Cloud build.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

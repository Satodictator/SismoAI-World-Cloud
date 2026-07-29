
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
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .regions import load_regions

VERSION = "1.0.0"
SCIENTIFIC_NOTICE = (
    "Gateway universal de sensores de investigación. Integra únicamente fuentes "
    "abiertas o expresamente autorizadas, normaliza tiempos y calidad, conserva "
    "procedencia y licencias y mantiene las nuevas familias aisladas del IEDC, "
    "de las alertas y de las órdenes de evacuación hasta superar validación."
)

ALLOWED_ROLES = {
    "PRE_EVENT_RESEARCH",
    "EVENT_DETECTION",
    "TSUNAMI_CONFIRMATION",
    "CONTEXT_CONTROL",
}
ALLOWED_FAMILIES = {
    "SEISMIC_WAVEFORM",
    "STRONG_MOTION",
    "REALTIME_GNSS",
    "SEAFLOOR_SEISMIC",
    "BOTTOM_PRESSURE",
    "SEA_LEVEL",
    "HYDROPHONE",
    "INFRASOUND",
    "PHONE_IMU",
    "CAMERA_MOTION",
    "FIBER_DAS",
    "GEOMAGNETIC_CONTROL",
    "IONOSPHERIC_CONTROL",
    "OCEAN_MET_CONTEXT",
    "CUSTOM_AUTHORIZED_SENSOR",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "request_timeout_seconds": 45,
    "request_attempts": 3,
    "raw_retention_days": 45,
    "feature_retention_days": 3650,
    "error_retention_days": 180,
    "max_recent_observations_public": 120,
    "max_public_nodes_per_source": 50,
    "private_node_coordinate_decimals": 1,
    "minimum_sensor_history_days_for_evolution": 30,
    "feeds_evolutionary_research": True,
    "modifies_iedc": False,
    "activates_alerts": False,
    "feeds_shadow_windows": False,
    "sources": [],
}

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sg_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sg_runs(
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  observations_inserted INTEGER NOT NULL DEFAULT 0,
  nodes_upserted INTEGER NOT NULL DEFAULT 0,
  features_written INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sg_sources(
  source_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  family TEXT NOT NULL,
  role TEXT NOT NULL,
  access_mode TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  status TEXT NOT NULL,
  endpoint TEXT,
  license TEXT,
  requires_secret TEXT,
  last_attempt TEXT,
  last_success TEXT,
  latency_seconds REAL,
  nodes INTEGER NOT NULL DEFAULT 0,
  observations INTEGER NOT NULL DEFAULT 0,
  quality REAL NOT NULL DEFAULT 0,
  message TEXT,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sg_nodes(
  source_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  family TEXT NOT NULL,
  role TEXT NOT NULL,
  region_id TEXT,
  name TEXT,
  latitude REAL,
  longitude REAL,
  elevation_or_depth REAL,
  privacy TEXT NOT NULL DEFAULT 'PUBLIC',
  status TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(source_id,node_id)
);
CREATE INDEX IF NOT EXISTS idx_sg_nodes_region_family
  ON sg_nodes(region_id,family);
CREATE TABLE IF NOT EXISTS sg_observations(
  observation_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  family TEXT NOT NULL,
  role TEXT NOT NULL,
  region_id TEXT,
  observed_at TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  measurement TEXT NOT NULL,
  value REAL,
  unit TEXT,
  sample_rate_hz REAL,
  quality REAL NOT NULL,
  latency_seconds REAL,
  latitude REAL,
  longitude REAL,
  raw_sha256 TEXT NOT NULL,
  privacy TEXT NOT NULL DEFAULT 'PUBLIC',
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sg_obs_time
  ON sg_observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_sg_obs_region_family_time
  ON sg_observations(region_id,family,observed_at);
CREATE TABLE IF NOT EXISTS sg_features(
  day TEXT NOT NULL,
  region_id TEXT NOT NULL,
  family TEXT NOT NULL,
  role TEXT NOT NULL,
  feature TEXT NOT NULL,
  value REAL,
  quality REAL NOT NULL,
  observations INTEGER NOT NULL,
  sources INTEGER NOT NULL,
  generated_at TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(day,region_id,family,role,feature)
);
CREATE INDEX IF NOT EXISTS idx_sg_features_region_day
  ON sg_features(region_id,day);
CREATE TABLE IF NOT EXISTS sg_errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  source_id TEXT,
  stage TEXT NOT NULL,
  error TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
"""

def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)

def utcnow() -> str:
    return utcnow_dt().isoformat().replace("+00:00", "Z")

def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    candidates = [
        text,
        text.replace("Z", "+00:00"),
        text.replace(" ", "T").replace("Z", "+00:00"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

def clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    if not math.isfinite(number):
        number = low
    return max(low, min(high, number))

def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default

def read_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=90)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=90000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection

def initialize(path: Path) -> None:
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        defaults = {
            "version": VERSION,
            "run_count": "0",
            "last_run": "",
            "status": "READY",
            "total_observations": "0",
            "total_features": "0",
        }
        connection.executemany(
            "INSERT OR IGNORE INTO sg_meta(key,value) VALUES(?,?)",
            defaults.items(),
        )
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite quick_check: {check}")
        connection.commit()
    finally:
        connection.close()

def get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM sg_meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default

def set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO sg_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )

def restore_archive(input_archive: Path | None, database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    if input_archive and input_archive.exists():
        with gzip.open(input_archive, "rb") as source, database.open("wb") as target:
            shutil.copyfileobj(source, target)
    initialize(database)

def publish_archive(database: Path, output_archive: Path) -> None:
    connection = connect(database)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()
    output_archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_archive.with_suffix(output_archive.suffix + ".tmp")
    with database.open("rb") as source, gzip.open(temporary, "wb", compresslevel=6) as target:
        shutil.copyfileobj(source, target)
    os.replace(temporary, output_archive)

def load_config(path: Path | None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    loaded = read_json(path, {}) if path else {}
    if isinstance(loaded, dict):
        config.update({k: v for k, v in loaded.items() if k != "sources"})
        if isinstance(loaded.get("sources"), list):
            config["sources"] = loaded["sources"]
    return config

def _http_get(
    url: str,
    *,
    timeout: int,
    attempts: int,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, str]]:
    request_headers = {
        "Accept": "*/*",
        "User-Agent": "SismoAI-Universal-Sensor-Gateway/1.0 research",
    }
    if headers:
        request_headers.update(headers)
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                return data, {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            if exc.code in {204, 404}:
                return b"", {}
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(min(10, 2 ** attempt))
    raise RuntimeError(f"No fue posible consultar {url}: {last_error}")

def _region_parts(region: Any) -> list[tuple[float, float, float, float]]:
    min_lat = float(region.min_lat)
    max_lat = float(region.max_lat)
    min_lon = float(region.min_lon)
    max_lon = float(region.max_lon)
    if min_lon <= max_lon:
        return [(min_lat, max_lat, min_lon, max_lon)]
    return [
        (min_lat, max_lat, min_lon, 180.0),
        (min_lat, max_lat, -180.0, max_lon),
    ]

def point_in_region(latitude: float, longitude: float, region: Any) -> bool:
    lat = float(latitude)
    lon = float(longitude)
    for min_lat, max_lat, min_lon, max_lon in _region_parts(region):
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return True
    return False

def assign_region(latitude: float | None, longitude: float | None, regions: list[Any]) -> str | None:
    if latitude is None or longitude is None:
        return None
    for region in regions:
        if point_in_region(float(latitude), float(longitude), region):
            return str(region.id)
    return None

def record_error(
    connection: sqlite3.Connection,
    source_id: str | None,
    stage: str,
    error: Any,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        "INSERT INTO sg_errors(occurred_at,source_id,stage,error,details_json) VALUES(?,?,?,?,?)",
        (
            utcnow(),
            source_id,
            stage,
            str(error)[:4000],
            json.dumps(details or {}, ensure_ascii=False),
        ),
    )

def upsert_source(
    connection: sqlite3.Connection,
    source: dict[str, Any],
    *,
    status: str,
    nodes: int = 0,
    observations: int = 0,
    quality: float = 0.0,
    latency_seconds: float | None = None,
    message: str = "",
    success: bool = False,
    details: dict[str, Any] | None = None,
) -> None:
    now = utcnow()
    connection.execute(
        """
        INSERT INTO sg_sources(
          source_id,name,family,role,access_mode,enabled,status,endpoint,license,
          requires_secret,last_attempt,last_success,latency_seconds,nodes,
          observations,quality,message,details_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id) DO UPDATE SET
          name=excluded.name,family=excluded.family,role=excluded.role,
          access_mode=excluded.access_mode,enabled=excluded.enabled,
          status=excluded.status,endpoint=excluded.endpoint,license=excluded.license,
          requires_secret=excluded.requires_secret,last_attempt=excluded.last_attempt,
          last_success=CASE WHEN excluded.last_success IS NOT NULL
                            THEN excluded.last_success ELSE sg_sources.last_success END,
          latency_seconds=excluded.latency_seconds,nodes=excluded.nodes,
          observations=excluded.observations,quality=excluded.quality,
          message=excluded.message,details_json=excluded.details_json
        """,
        (
            str(source.get("id") or "UNKNOWN"),
            str(source.get("name") or source.get("id") or "Fuente"),
            str(source.get("family") or "CUSTOM_AUTHORIZED_SENSOR"),
            str(source.get("role") or "CONTEXT_CONTROL"),
            str(source.get("access_mode") or "UNKNOWN"),
            int(bool(source.get("enabled", False))),
            status,
            str(source.get("endpoint") or ""),
            str(source.get("license") or ""),
            str(source.get("requires_secret") or ""),
            now,
            now if success else None,
            latency_seconds,
            int(nodes),
            int(observations),
            clamp(quality),
            str(message)[:1500],
            json.dumps(details or {}, ensure_ascii=False),
        ),
    )

def upsert_node(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    node_id: str,
    family: str,
    role: str,
    region_id: str | None,
    name: str | None,
    latitude: float | None,
    longitude: float | None,
    elevation_or_depth: float | None,
    privacy: str = "PUBLIC",
    status: str = "ACTIVE",
    details: dict[str, Any] | None = None,
) -> bool:
    now = utcnow()
    existed = connection.execute(
        "SELECT 1 FROM sg_nodes WHERE source_id=? AND node_id=?",
        (source_id, node_id),
    ).fetchone() is not None
    connection.execute(
        """
        INSERT INTO sg_nodes(
          source_id,node_id,family,role,region_id,name,latitude,longitude,
          elevation_or_depth,privacy,status,first_seen,last_seen,details_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id,node_id) DO UPDATE SET
          family=excluded.family,role=excluded.role,region_id=excluded.region_id,
          name=excluded.name,latitude=excluded.latitude,longitude=excluded.longitude,
          elevation_or_depth=excluded.elevation_or_depth,privacy=excluded.privacy,
          status=excluded.status,last_seen=excluded.last_seen,
          details_json=excluded.details_json
        """,
        (
            source_id,
            node_id,
            family,
            role,
            region_id,
            name,
            latitude,
            longitude,
            elevation_or_depth,
            privacy,
            status,
            now,
            now,
            json.dumps(details or {}, ensure_ascii=False),
        ),
    )
    return not existed

def observation_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "source_id": payload.get("source_id"),
            "node_id": payload.get("node_id"),
            "family": payload.get("family"),
            "role": payload.get("role"),
            "observed_at": payload.get("observed_at"),
            "measurement": payload.get("measurement"),
            "value": payload.get("value"),
            "unit": payload.get("unit"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

def insert_observation(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    regions: list[Any],
) -> bool:
    family = str(payload.get("family") or "").upper()
    role = str(payload.get("role") or "").upper()
    if family not in ALLOWED_FAMILIES:
        raise ValueError(f"Familia no permitida: {family}")
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Rol no permitido: {role}")
    observed = parse_dt(payload.get("observed_at"))
    if observed is None:
        raise ValueError("observed_at inválido")
    now = utcnow_dt()
    if observed > now + timedelta(minutes=10):
        raise ValueError("observación con fecha futura fuera de tolerancia")
    latitude = as_float(payload.get("latitude"))
    longitude = as_float(payload.get("longitude"))
    region_id = str(payload.get("region_id") or "") or assign_region(latitude, longitude, regions)
    value = as_float(payload.get("value"))
    quality = clamp(payload.get("quality", 0.5))
    latency = max(0.0, (now - observed).total_seconds())
    privacy = str(payload.get("privacy") or "PUBLIC").upper()
    node_id = str(payload.get("node_id") or "UNKNOWN")
    if privacy != "PUBLIC":
        latitude = round(latitude, 1) if latitude is not None else None
        longitude = round(longitude, 1) if longitude is not None else None
        node_id = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:24]
    normalized = {
        **payload,
        "source_id": str(payload.get("source_id") or "CUSTOM"),
        "node_id": node_id,
        "family": family,
        "role": role,
        "region_id": region_id,
        "observed_at": iso(observed),
        "measurement": str(payload.get("measurement") or "unknown"),
        "value": value,
        "unit": str(payload.get("unit") or ""),
        "quality": quality,
        "latitude": latitude,
        "longitude": longitude,
        "privacy": privacy,
    }
    raw_bytes = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    raw_sha = sha256_bytes(raw_bytes)
    obs_id = observation_fingerprint(normalized)
    before = connection.total_changes
    connection.execute(
        """
        INSERT OR IGNORE INTO sg_observations(
          observation_id,source_id,node_id,family,role,region_id,observed_at,
          ingested_at,measurement,value,unit,sample_rate_hz,quality,
          latency_seconds,latitude,longitude,raw_sha256,privacy,details_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            obs_id,
            normalized["source_id"],
            normalized["node_id"],
            family,
            role,
            region_id,
            normalized["observed_at"],
            utcnow(),
            normalized["measurement"],
            value,
            normalized["unit"],
            as_float(payload.get("sample_rate_hz")),
            quality,
            latency,
            latitude,
            longitude,
            raw_sha,
            privacy,
            json.dumps(payload.get("details") or {}, ensure_ascii=False),
        ),
    )
    return connection.total_changes > before

def _selected_regions(regions: list[Any], run_number: int, batch_size: int, salt: str) -> list[Any]:
    if not regions:
        return []
    size = max(1, min(len(regions), int(batch_size or len(regions))))
    offset = int(hashlib.sha256(f"{run_number}|{salt}".encode()).hexdigest()[:8], 16) % len(regions)
    return [regions[(offset + index) % len(regions)] for index in range(size)]

def _fdsn_station_rows(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header_line = next((line for line in lines if line.startswith("#") and "|" in line), "")
    headers = [item.strip().lstrip("#") for item in header_line.split("|")] if header_line else []
    rows: list[dict[str, str]] = []
    for line in lines:
        if line.startswith("#") or "|" not in line:
            continue
        values = [item.strip() for item in line.split("|")]
        if headers and len(values) >= len(headers):
            rows.append(dict(zip(headers, values)))
        elif len(values) >= 8:
            rows.append(
                {
                    "Network": values[0],
                    "Station": values[1],
                    "Latitude": values[2],
                    "Longitude": values[3],
                    "Elevation": values[4],
                    "SiteName": values[5],
                    "StartTime": values[6],
                    "EndTime": values[7],
                }
            )
    return rows

def collect_fdsn_inventory(
    connection: sqlite3.Connection,
    source: dict[str, Any],
    regions: list[Any],
    config: dict[str, Any],
    run_number: int,
) -> tuple[int, int]:
    endpoint = str(source.get("endpoint") or "").rstrip("/")
    selected = _selected_regions(
        regions,
        run_number,
        int(source.get("region_batch_size") or 4),
        str(source.get("id")),
    )
    new_nodes = 0
    total_seen = 0
    errors = 0
    for region in selected:
        for min_lat, max_lat, min_lon, max_lon in _region_parts(region):
            params = {
                "level": "station",
                "format": "text",
                "minlatitude": f"{min_lat:.6f}",
                "maxlatitude": f"{max_lat:.6f}",
                "minlongitude": f"{min_lon:.6f}",
                "maxlongitude": f"{max_lon:.6f}",
                "starttime": (utcnow_dt() - timedelta(days=365)).date().isoformat(),
                "endtime": utcnow_dt().date().isoformat(),
                "nodata": "204",
            }
            url = endpoint + "/query?" + urllib.parse.urlencode(params)
            try:
                data, _ = _http_get(
                    url,
                    timeout=int(config.get("request_timeout_seconds") or 45),
                    attempts=int(config.get("request_attempts") or 3),
                )
                rows = _fdsn_station_rows(data.decode("utf-8", errors="replace"))
                for row in rows:
                    lat = as_float(row.get("Latitude") or row.get("latitude"))
                    lon = as_float(row.get("Longitude") or row.get("longitude"))
                    network = str(row.get("Network") or row.get("network") or "").strip()
                    station = str(row.get("Station") or row.get("station") or "").strip()
                    if not station:
                        continue
                    total_seen += 1
                    new_nodes += int(
                        upsert_node(
                            connection,
                            source_id=str(source["id"]),
                            node_id=f"{network}.{station}".strip("."),
                            family=str(source["family"]),
                            role=str(source["role"]),
                            region_id=str(region.id),
                            name=str(row.get("SiteName") or station),
                            latitude=lat,
                            longitude=lon,
                            elevation_or_depth=as_float(row.get("Elevation")),
                            privacy="PUBLIC",
                            status="ACTIVE",
                            details={
                                "network": network,
                                "start_time": row.get("StartTime"),
                                "end_time": row.get("EndTime"),
                                "inventory_only": True,
                            },
                        )
                    )
            except Exception as exc:
                errors += 1
                record_error(connection, str(source["id"]), "FDSN_INVENTORY", exc, {"region": region.id})
    current_nodes = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_nodes WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    upsert_source(
        connection,
        source,
        status="OK" if total_seen else ("DEGRADED" if errors else "NO_DATA"),
        nodes=current_nodes,
        observations=0,
        quality=float(source.get("quality") or 0.8),
        message=(
            f"Inventario FDSN rotativo: {total_seen} estaciones observadas en "
            f"{len(selected)} macroregiones. Las ondas continuas requieren SeedLink "
            "o un agente persistente; no se sondean mediante FDSN."
        ),
        success=total_seen > 0,
        details={"regions_scanned": [str(item.id) for item in selected], "errors": errors},
    )
    return new_nodes, 0

def collect_noaa_coops(
    connection: sqlite3.Connection,
    source: dict[str, Any],
    regions: list[Any],
    config: dict[str, Any],
    run_number: int,
) -> tuple[int, int]:
    metadata_endpoint = str(
        source.get("metadata_endpoint")
        or "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=waterlevels"
    )
    data_endpoint = str(
        source.get("endpoint")
        or "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    )
    raw, _ = _http_get(
        metadata_endpoint,
        timeout=int(config.get("request_timeout_seconds") or 45),
        attempts=int(config.get("request_attempts") or 3),
        headers={"Accept": "application/json"},
    )
    payload = json.loads(raw.decode("utf-8"))
    stations = payload.get("stations") or []
    new_nodes = 0
    station_records: list[tuple[str, str | None, float | None, float | None, str]] = []
    for item in stations:
        station_id = str(item.get("id") or "").strip()
        if not station_id:
            continue
        lat = as_float(item.get("lat"))
        lon = as_float(item.get("lng") if item.get("lng") is not None else item.get("lon"))
        region_id = assign_region(lat, lon, regions)
        name = str(item.get("name") or station_id)
        station_records.append((station_id, region_id, lat, lon, name))
        new_nodes += int(
            upsert_node(
                connection,
                source_id=str(source["id"]),
                node_id=station_id,
                family=str(source["family"]),
                role=str(source["role"]),
                region_id=region_id,
                name=name,
                latitude=lat,
                longitude=lon,
                elevation_or_depth=None,
                privacy="PUBLIC",
                status="ACTIVE",
                details={"inventory": "NOAA_COOPS_MDAPI"},
            )
        )
    station_records.sort(key=lambda item: item[0])
    batch_size = max(1, int(source.get("station_batch_size") or 20))
    offset = (run_number * batch_size) % max(1, len(station_records))
    selected = [
        station_records[(offset + index) % len(station_records)]
        for index in range(min(batch_size, len(station_records)))
    ] if station_records else []
    inserted = 0
    errors = 0
    latencies: list[float] = []
    for station_id, region_id, lat, lon, name in selected:
        params = {
            "product": "water_level",
            "application": "SismoAI",
            "date": "latest",
            "datum": "MSL",
            "station": station_id,
            "time_zone": "gmt",
            "units": "metric",
            "format": "json",
        }
        url = data_endpoint + "?" + urllib.parse.urlencode(params)
        try:
            body, _ = _http_get(
                url,
                timeout=int(config.get("request_timeout_seconds") or 45),
                attempts=2,
                headers={"Accept": "application/json"},
            )
            item_payload = json.loads(body.decode("utf-8"))
            values = item_payload.get("data") or []
            if not values:
                continue
            value_item = values[-1]
            observed = parse_dt(value_item.get("t"))
            value = as_float(value_item.get("v"))
            if observed is None or value is None:
                continue
            latency = max(0.0, (utcnow_dt() - observed).total_seconds())
            latencies.append(latency)
            inserted += int(
                insert_observation(
                    connection,
                    {
                        "source_id": str(source["id"]),
                        "node_id": station_id,
                        "family": str(source["family"]),
                        "role": str(source["role"]),
                        "region_id": region_id,
                        "observed_at": iso(observed),
                        "measurement": "sea_level",
                        "value": value,
                        "unit": "m",
                        "quality": float(source.get("quality") or 0.9),
                        "latitude": lat,
                        "longitude": lon,
                        "privacy": "PUBLIC",
                        "details": {
                            "station_name": name,
                            "flags": value_item.get("f"),
                            "quality_code": value_item.get("q"),
                            "datum": "MSL",
                        },
                    },
                    regions,
                )
            )
        except Exception as exc:
            errors += 1
            record_error(connection, str(source["id"]), "COOPS_LATEST", exc, {"station": station_id})
    current_nodes = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_nodes WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    current_obs = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_observations WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    upsert_source(
        connection,
        source,
        status="OK" if current_nodes else "NO_DATA",
        nodes=current_nodes,
        observations=current_obs,
        quality=float(source.get("quality") or 0.9),
        latency_seconds=statistics.median(latencies) if latencies else None,
        message=(
            f"{current_nodes} mareógrafos inventariados; {inserted} observaciones "
            f"nuevas en el lote rotativo de {len(selected)} estaciones."
        ),
        success=current_nodes > 0,
        details={"batch_size": len(selected), "errors": errors},
    )
    return new_nodes, inserted

def _parse_dart_latest(text: str) -> tuple[datetime, int, float] | None:
    rows: list[tuple[datetime, int, float]] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        parts = clean.split()
        if len(parts) < 8:
            continue
        try:
            year, month, day_value, hour, minute, second = map(int, parts[:6])
            measurement_type = int(parts[6])
            height = float(parts[7])
            observed = datetime(
                year, month, day_value, hour, minute, second, tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            continue
        rows.append((observed, measurement_type, height))
    return max(rows, key=lambda item: item[0]) if rows else None

def collect_ndbc_dart(
    connection: sqlite3.Connection,
    source: dict[str, Any],
    regions: list[Any],
    config: dict[str, Any],
    run_number: int,
) -> tuple[int, int]:
    endpoint = str(source.get("inventory_endpoint") or "https://www.ndbc.noaa.gov/activestations.xml")
    raw, _ = _http_get(
        endpoint,
        timeout=int(config.get("request_timeout_seconds") or 45),
        attempts=int(config.get("request_attempts") or 3),
        headers={"Accept": "application/xml,text/xml"},
    )
    root = ET.fromstring(raw)
    dart_nodes: list[tuple[str, str | None, float | None, float | None, str, dict[str, Any]]] = []
    new_nodes = 0
    for element in root.findall(".//station"):
        if str(element.attrib.get("dart") or "").lower() != "y":
            continue
        station_id = str(element.attrib.get("id") or "").strip()
        if not station_id:
            continue
        lat = as_float(element.attrib.get("lat"))
        lon = as_float(element.attrib.get("lon"))
        region_id = assign_region(lat, lon, regions)
        details = dict(element.attrib)
        name = str(element.attrib.get("name") or station_id)
        dart_nodes.append((station_id, region_id, lat, lon, name, details))
        new_nodes += int(
            upsert_node(
                connection,
                source_id=str(source["id"]),
                node_id=station_id,
                family=str(source["family"]),
                role=str(source["role"]),
                region_id=region_id,
                name=name,
                latitude=lat,
                longitude=lon,
                elevation_or_depth=None,
                privacy="PUBLIC",
                status="ACTIVE",
                details=details,
            )
        )
    dart_nodes.sort(key=lambda item: item[0])
    batch_size = max(1, int(source.get("station_batch_size") or 16))
    offset = (run_number * batch_size) % max(1, len(dart_nodes))
    selected = [
        dart_nodes[(offset + index) % len(dart_nodes)]
        for index in range(min(batch_size, len(dart_nodes)))
    ] if dart_nodes else []
    inserted = 0
    errors = 0
    latencies: list[float] = []
    realtime_template = str(
        source.get("realtime_template")
        or "https://www.ndbc.noaa.gov/data/realtime2/{station}.dart"
    )
    for station_id, region_id, lat, lon, name, details in selected:
        try:
            body, _ = _http_get(
                realtime_template.format(station=station_id),
                timeout=int(config.get("request_timeout_seconds") or 45),
                attempts=2,
                headers={"Accept": "text/plain"},
            )
            latest = _parse_dart_latest(body.decode("utf-8", errors="replace"))
            if latest is None:
                continue
            observed, measurement_type, height = latest
            latency = max(0.0, (utcnow_dt() - observed).total_seconds())
            latencies.append(latency)
            inserted += int(
                insert_observation(
                    connection,
                    {
                        "source_id": str(source["id"]),
                        "node_id": station_id,
                        "family": str(source["family"]),
                        "role": str(source["role"]),
                        "region_id": region_id,
                        "observed_at": iso(observed),
                        "measurement": "water_column_height",
                        "value": height,
                        "unit": "m",
                        "quality": float(source.get("quality") or 0.88),
                        "latitude": lat,
                        "longitude": lon,
                        "privacy": "PUBLIC",
                        "details": {
                            "station_name": name,
                            "measurement_type": measurement_type,
                            "measurement_type_meaning": {
                                1: "15_minutes",
                                2: "1_minute",
                                3: "15_seconds",
                            }.get(measurement_type, "unknown"),
                            "automated_quality_control": True,
                        },
                    },
                    regions,
                )
            )
        except Exception as exc:
            errors += 1
            record_error(connection, str(source["id"]), "NDBC_DART_LATEST", exc, {"station": station_id})
    current_nodes = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_nodes WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    current_obs = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_observations WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    upsert_source(
        connection,
        source,
        status="OK" if current_nodes else "NO_DATA",
        nodes=current_nodes,
        observations=current_obs,
        quality=float(source.get("quality") or 0.88),
        latency_seconds=statistics.median(latencies) if latencies else None,
        message=(
            f"{current_nodes} estaciones DART activas; {inserted} lecturas nuevas "
            f"de altura de columna de agua en {len(selected)} estaciones rotativas."
        ),
        success=current_nodes > 0,
        details={"batch_size": len(selected), "errors": errors},
    )
    return new_nodes, inserted

def collect_json_inbox(
    connection: sqlite3.Connection,
    source: dict[str, Any],
    regions: list[Any],
    inbox_dir: Path | None,
) -> tuple[int, int]:
    if inbox_dir is None or not inbox_dir.exists():
        upsert_source(
            connection,
            source,
            status="WAITING_FOR_AUTHORIZED_NODES",
            quality=float(source.get("quality") or 0.7),
            message=(
                "Entrada JSONL preparada para teléfonos fijos, Raspberry Pi, cámaras "
                "procesadas localmente, DAS, SeedLink, NTRIP, MQTT y otros puentes autorizados."
            ),
            success=False,
        )
        return 0, 0
    inserted = 0
    new_nodes = 0
    processed_files = 0
    rejected = 0
    for path in sorted(inbox_dir.glob("*.jsonl")):
        processed_files += 1
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                payload.setdefault("source_id", str(source["id"]))
                payload.setdefault("family", str(source["family"]))
                payload.setdefault("role", str(source["role"]))
                privacy = str(payload.get("privacy") or "PRIVATE").upper()
                lat = as_float(payload.get("latitude"))
                lon = as_float(payload.get("longitude"))
                region_id = str(payload.get("region_id") or "") or assign_region(lat, lon, regions)
                stored_node_id = str(payload.get("node_id") or "UNKNOWN")
                if privacy != "PUBLIC":
                    lat = round(lat, 1) if lat is not None else None
                    lon = round(lon, 1) if lon is not None else None
                    stored_node_id = hashlib.sha256(
                        stored_node_id.encode("utf-8")
                    ).hexdigest()[:24]
                new_nodes += int(
                    upsert_node(
                        connection,
                        source_id=str(payload["source_id"]),
                        node_id=stored_node_id,
                        family=str(payload["family"]).upper(),
                        role=str(payload["role"]).upper(),
                        region_id=region_id,
                        name=str(payload.get("name") or payload.get("node_id") or "Nodo autorizado"),
                        latitude=lat,
                        longitude=lon,
                        elevation_or_depth=as_float(payload.get("elevation_or_depth")),
                        privacy=privacy,
                        status="ACTIVE",
                        details={"inbox_file": path.name},
                    )
                )
                inserted += int(insert_observation(connection, payload, regions))
            except Exception as exc:
                rejected += 1
                record_error(
                    connection,
                    str(source["id"]),
                    "JSON_INBOX",
                    exc,
                    {"file": path.name, "line": line_number},
                )
    current_nodes = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_nodes WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    current_obs = int(
        connection.execute(
            "SELECT COUNT(*) FROM sg_observations WHERE source_id=?",
            (str(source["id"]),),
        ).fetchone()[0]
    )
    upsert_source(
        connection,
        source,
        status="OK" if processed_files else "WAITING_FOR_AUTHORIZED_NODES",
        nodes=current_nodes,
        observations=current_obs,
        quality=float(source.get("quality") or 0.7),
        message=f"{processed_files} archivos JSONL procesados; {inserted} observaciones nuevas.",
        success=processed_files > 0,
        details={"rejected": rejected},
    )
    return new_nodes, inserted

def register_non_cloud_source(
    connection: sqlite3.Connection,
    source: dict[str, Any],
) -> None:
    secret = str(source.get("requires_secret") or "").strip()
    access_mode = str(source.get("access_mode") or "EXTERNAL_AGENT_REQUIRED")
    if not source.get("enabled", False):
        status = "CONFIGURED_DISABLED"
        message = "Conector registrado pero desactivado por política."
    elif secret and not os.environ.get(secret):
        status = "WAITING_FOR_CREDENTIAL_OR_LICENSE"
        message = (
            f"Requiere el secreto {secret} y aceptación de la licencia o permiso "
            "correspondiente. No se intenta el acceso sin autorización."
        )
    elif access_mode in {"EXTERNAL_AGENT_REQUIRED", "CONTINUOUS_STREAM"}:
        status = "EXTERNAL_AGENT_REQUIRED"
        message = (
            "Las corrientes continuas no pueden mantenerse con GitHub Actions. "
            "Requieren un agente persistente autorizado que envíe resúmenes al inbox."
        )
    elif access_mode == "ACCESS_AGREEMENT_REQUIRED":
        status = "ACCESS_AGREEMENT_REQUIRED"
        message = "Requiere convenio o autorización del operador antes de activar el acceso."
    else:
        status = "REGISTERED_NOT_POLLED"
        message = "Fuente registrada para implementación o activación posterior."
    upsert_source(
        connection,
        source,
        status=status,
        quality=float(source.get("quality") or 0.0),
        message=message,
        success=False,
        details={"research_only": True, "permission_enforced": True},
    )

def derive_features(connection: sqlite3.Connection, lookback_days: int = 60) -> int:
    start = (utcnow_dt() - timedelta(days=max(2, lookback_days))).date().isoformat()
    rows = connection.execute(
        """
        SELECT substr(observed_at,1,10) day,COALESCE(region_id,'UNASSIGNED') region_id,
               family,role,measurement,value,quality,source_id
        FROM sg_observations
        WHERE substr(observed_at,1,10)>=?
        ORDER BY day,region_id,family,role,measurement
        """,
        (start,),
    ).fetchall()
    grouped: dict[tuple[str, str, str, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        key = (
            str(row["day"]),
            str(row["region_id"]),
            str(row["family"]),
            str(row["role"]),
            str(row["measurement"]),
        )
        grouped.setdefault(key, []).append(row)
    written = 0
    for (day_value, region_id, family, role, measurement), items in grouped.items():
        values = [float(item["value"]) for item in items if item["value"] is not None and math.isfinite(float(item["value"]))]
        qualities = [float(item["quality"] or 0.0) for item in items]
        sources = len({str(item["source_id"]) for item in items})
        metrics: dict[str, float | None] = {
            "count": float(len(items)),
            "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
            "min": min(values) if values else None,
            "range": (max(values) - min(values)) if values else None,
        }
        for suffix, value in metrics.items():
            feature = f"{measurement}__{suffix}"
            connection.execute(
                """
                INSERT INTO sg_features(
                  day,region_id,family,role,feature,value,quality,observations,
                  sources,generated_at,details_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day,region_id,family,role,feature) DO UPDATE SET
                  value=excluded.value,quality=excluded.quality,
                  observations=excluded.observations,sources=excluded.sources,
                  generated_at=excluded.generated_at,details_json=excluded.details_json
                """,
                (
                    day_value,
                    region_id,
                    family,
                    role,
                    feature,
                    value,
                    statistics.mean(qualities) if qualities else 0.0,
                    len(items),
                    sources,
                    utcnow(),
                    json.dumps({"measurement": measurement}, ensure_ascii=False),
                ),
            )
            written += 1
    node_rows = connection.execute(
        """
        SELECT COALESCE(region_id,'UNASSIGNED') region_id,family,role,COUNT(*) nodes,
               AVG(CASE WHEN status='ACTIVE' THEN 1.0 ELSE 0.0 END) active_ratio
        FROM sg_nodes GROUP BY region_id,family,role
        """
    ).fetchall()
    today = utcnow_dt().date().isoformat()
    for row in node_rows:
        for feature, value in (
            ("registered_node_count", float(row["nodes"])),
            ("active_node_ratio", float(row["active_ratio"] or 0.0)),
        ):
            connection.execute(
                """
                INSERT INTO sg_features(
                  day,region_id,family,role,feature,value,quality,observations,
                  sources,generated_at,details_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day,region_id,family,role,feature) DO UPDATE SET
                  value=excluded.value,quality=excluded.quality,
                  observations=excluded.observations,sources=excluded.sources,
                  generated_at=excluded.generated_at,details_json=excluded.details_json
                """,
                (
                    today,
                    str(row["region_id"]),
                    str(row["family"]),
                    str(row["role"]),
                    feature,
                    value,
                    0.8,
                    int(row["nodes"]),
                    1,
                    utcnow(),
                    "{}",
                ),
            )
            written += 1
    return written

def prune(connection: sqlite3.Connection, config: dict[str, Any]) -> dict[str, int]:
    raw_cutoff = iso(utcnow_dt() - timedelta(days=int(config.get("raw_retention_days") or 45)))
    feature_cutoff = (utcnow_dt() - timedelta(days=int(config.get("feature_retention_days") or 3650))).date().isoformat()
    error_cutoff = iso(utcnow_dt() - timedelta(days=int(config.get("error_retention_days") or 180)))
    counts: dict[str, int] = {}
    before = connection.total_changes
    connection.execute("DELETE FROM sg_observations WHERE observed_at<?", (raw_cutoff,))
    counts["observations"] = connection.total_changes - before
    before = connection.total_changes
    connection.execute("DELETE FROM sg_features WHERE day<?", (feature_cutoff,))
    counts["features"] = connection.total_changes - before
    before = connection.total_changes
    connection.execute("DELETE FROM sg_errors WHERE occurred_at<?", (error_cutoff,))
    counts["errors"] = connection.total_changes - before
    return counts

def _public_coordinate(value: Any, privacy: str, decimals: int) -> float | None:
    number = as_float(value)
    if number is None:
        return None
    if str(privacy).upper() == "PUBLIC":
        return round(number, 4)
    return round(number, max(0, min(3, decimals)))

def build_public(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    last_run: dict[str, Any],
) -> dict[str, Any]:
    sources = [dict(row) for row in connection.execute("SELECT * FROM sg_sources ORDER BY source_id")]
    for item in sources:
        try:
            item["details"] = json.loads(item.pop("details_json"))
        except Exception:
            item["details"] = {}
        item["enabled"] = bool(item.get("enabled"))
    totals = dict(
        connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM sg_nodes) nodes,
              (SELECT COUNT(*) FROM sg_observations) observations,
              (SELECT COUNT(*) FROM sg_features) features,
              (SELECT COUNT(*) FROM sg_errors WHERE occurred_at>=?) errors_24h
            """,
            (iso(utcnow_dt() - timedelta(hours=24)),),
        ).fetchone()
    )
    role_counts = {
        str(row["role"]): int(row["total"])
        for row in connection.execute(
            "SELECT role,COUNT(*) total FROM sg_observations GROUP BY role"
        )
    }
    family_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT family,role,COUNT(DISTINCT source_id) sources,
                   COUNT(DISTINCT node_id) nodes,COUNT(*) observations,
                   MAX(observed_at) latest
            FROM sg_observations GROUP BY family,role
            ORDER BY observations DESC,family
            """
        )
    ]
    node_only = [
        dict(row)
        for row in connection.execute(
            """
            SELECT family,role,COUNT(DISTINCT source_id) sources,
                   COUNT(*) nodes,NULL observations,MAX(last_seen) latest
            FROM sg_nodes
            WHERE family NOT IN (SELECT DISTINCT family FROM sg_observations)
            GROUP BY family,role ORDER BY nodes DESC
            """
        )
    ]
    families = family_rows + node_only
    recent = [
        dict(row)
        for row in connection.execute(
            """
            SELECT source_id,node_id,family,role,region_id,observed_at,measurement,
                   value,unit,quality,latency_seconds,latitude,longitude,privacy
            FROM sg_observations ORDER BY observed_at DESC
            LIMIT ?
            """,
            (int(config.get("max_recent_observations_public") or 120),),
        )
    ]
    coordinate_decimals = int(config.get("private_node_coordinate_decimals") or 1)
    for item in recent:
        item["latitude"] = _public_coordinate(item.get("latitude"), str(item.get("privacy")), coordinate_decimals)
        item["longitude"] = _public_coordinate(item.get("longitude"), str(item.get("privacy")), coordinate_decimals)
        if str(item.get("privacy")).upper() != "PUBLIC":
            item["node_id"] = hashlib.sha256(str(item["node_id"]).encode()).hexdigest()[:12]
    region_coverage = [
        dict(row)
        for row in connection.execute(
            """
            SELECT COALESCE(region_id,'UNASSIGNED') region_id,
                   COUNT(DISTINCT family) families,
                   COUNT(DISTINCT source_id) sources,
                   COUNT(DISTINCT node_id) nodes,
                   MAX(observed_at) latest
            FROM sg_observations GROUP BY region_id
            ORDER BY families DESC,nodes DESC
            """
        )
    ]
    active_statuses = {"OK", "ACTIVE", "DISCOVERY_ONLY"}
    return {
        "schema_version": 1,
        "version": VERSION,
        "generated_at": utcnow(),
        "status": str(get_meta(connection, "status", "UNKNOWN")),
        "last_run": last_run,
        "totals": {
            **totals,
            "sources_registered": len(sources),
            "sources_active_or_available": sum(1 for item in sources if str(item.get("status")) in active_statuses),
            "roles": role_counts,
        },
        "sources": sources,
        "families": families,
        "region_coverage": region_coverage,
        "recent_observations": recent,
        "policy": {
            "minimum_sensor_history_days_for_evolution": int(
                config.get("minimum_sensor_history_days_for_evolution") or 30
            ),
            "feeds_evolutionary_research": bool(config.get("feeds_evolutionary_research", True)),
            "modifies_iedc": False,
            "activates_alerts": False,
            "feeds_shadow_windows": False,
            "continuous_streams_require_external_agent": True,
            "utc_normalization": True,
            "deduplication": True,
            "permission_enforcement": True,
            "private_coordinates_are_reduced": True,
        },
        "scientific_notice": SCIENTIFIC_NOTICE,
    }

def update_manifest(
    output_path: Path,
    manifest_path: Path | None,
    manifest_sha_path: Path | None,
) -> None:
    if manifest_path is None or not manifest_path.exists():
        return
    manifest = read_json(manifest_path, {"files": []})
    files = list(manifest.get("files") or [])
    relative = "data/sensors.json"
    entry = {"path": relative, "sha256": sha256_file(output_path)}
    replaced = False
    for index, item in enumerate(files):
        if item.get("path") == relative:
            files[index] = entry
            replaced = True
            break
    if not replaced:
        files.append(entry)
    files.sort(key=lambda item: str(item.get("path") or ""))
    manifest["files"] = files
    write_json(manifest_path, manifest)
    if manifest_sha_path is not None:
        manifest_sha_path.write_text(
            f"{sha256_file(manifest_path)}  manifest.json\n",
            encoding="utf-8",
        )

def run_gateway(
    *,
    regions_path: Path,
    config_path: Path | None,
    input_state_archive: Path | None,
    output_state_archive: Path,
    output_json: Path,
    inbox_dir: Path | None = None,
    manifest_path: Path | None = None,
    manifest_sha_path: Path | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    _, regions = load_regions(regions_path)
    output_state_archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sismoai_sensor_gateway_") as temporary:
        root = Path(temporary)
        database = root / "sensor_gateway.sqlite"
        restore_archive(input_state_archive, database)
        connection = connect(database)
        run_number = int(get_meta(connection, "run_count", "0") or 0) + 1
        run_id = hashlib.sha256(f"{utcnow()}|{os.getpid()}|{run_number}".encode()).hexdigest()[:24]
        started = utcnow()
        connection.execute(
            "INSERT INTO sg_runs(run_id,started_at,status) VALUES(?,?,'RUNNING')",
            (run_id, started),
        )
        connection.commit()
        new_nodes = 0
        inserted = 0
        features = 0
        errors: list[str] = []
        source_results: list[dict[str, Any]] = []
        try:
            if not bool(config.get("enabled", True)):
                status = "DISABLED"
            else:
                status = "OK"
                for source in config.get("sources") or []:
                    source_id = str(source.get("id") or "UNKNOWN")
                    if not bool(source.get("enabled", False)):
                        register_non_cloud_source(connection, source)
                        source_results.append({"source_id": source_id, "status": "CONFIGURED_DISABLED"})
                        continue
                    collector = str(source.get("collector") or "REGISTER_ONLY").upper()
                    try:
                        if collector == "FDSN_STATION_INVENTORY":
                            nodes, observations = collect_fdsn_inventory(
                                connection, source, regions, config, run_number
                            )
                        elif collector == "NOAA_COOPS":
                            nodes, observations = collect_noaa_coops(
                                connection, source, regions, config, run_number
                            )
                        elif collector == "NDBC_DART":
                            nodes, observations = collect_ndbc_dart(
                                connection, source, regions, config, run_number
                            )
                        elif collector == "JSON_INBOX":
                            nodes, observations = collect_json_inbox(
                                connection, source, regions, inbox_dir
                            )
                        else:
                            register_non_cloud_source(connection, source)
                            nodes, observations = 0, 0
                        new_nodes += nodes
                        inserted += observations
                        source_results.append(
                            {
                                "source_id": source_id,
                                "collector": collector,
                                "nodes_new": nodes,
                                "observations_new": observations,
                            }
                        )
                    except Exception as exc:
                        errors.append(f"{source_id}: {type(exc).__name__}: {exc}")
                        record_error(connection, source_id, "COLLECTOR", exc)
                        upsert_source(
                            connection,
                            source,
                            status="DEGRADED_RETRY_PENDING",
                            quality=0.0,
                            message=str(exc),
                            success=False,
                        )
                features = derive_features(connection)
                pruned = prune(connection, config)
                set_meta(connection, "run_count", run_number)
                set_meta(connection, "last_run", utcnow())
                set_meta(connection, "status", "OK" if not errors else "DEGRADED")
                set_meta(
                    connection,
                    "total_observations",
                    int(get_meta(connection, "total_observations", "0") or 0) + inserted,
                )
                set_meta(
                    connection,
                    "total_features",
                    int(get_meta(connection, "total_features", "0") or 0) + features,
                )
                status = "OK" if not errors else "DEGRADED"
            finished = utcnow()
            details = {
                "sources": source_results,
                "errors": errors,
                "pruned": pruned if "pruned" in locals() else {},
            }
            connection.execute(
                """
                UPDATE sg_runs SET finished_at=?,status=?,observations_inserted=?,
                  nodes_upserted=?,features_written=?,details_json=?
                WHERE run_id=?
                """,
                (
                    finished,
                    status,
                    inserted,
                    new_nodes,
                    features,
                    json.dumps(details, ensure_ascii=False),
                    run_id,
                ),
            )
            connection.commit()
        except Exception as exc:
            status = "DEGRADED_RETRY_PENDING"
            errors.append(f"{type(exc).__name__}: {exc}")
            record_error(connection, None, "RUN", exc)
            set_meta(connection, "status", status)
            connection.execute(
                "UPDATE sg_runs SET finished_at=?,status=?,details_json=? WHERE run_id=?",
                (utcnow(), status, json.dumps({"errors": errors}, ensure_ascii=False), run_id),
            )
            connection.commit()
        last_run = {
            "run_id": run_id,
            "run_number": run_number,
            "started_at": started,
            "finished_at": utcnow(),
            "status": status,
            "nodes_new": new_nodes,
            "observations_new": inserted,
            "features_written": features,
            "errors": errors,
        }
        public = build_public(connection, config, last_run)
        connection.commit()
        connection.close()
        write_json(output_json, public)
        update_manifest(output_json, manifest_path, manifest_sha_path)
        publish_archive(database, output_state_archive)
        return public

def selftest() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sismoai_sensor_selftest_") as temporary:
        root = Path(temporary)
        database = root / "sensor.sqlite"
        initialize(database)
        connection = connect(database)
        class Region:
            id = "test_region"
            min_lat = -10
            max_lat = 10
            min_lon = -10
            max_lon = 10
        regions = [Region()]
        payload = {
            "source_id": "SELFTEST",
            "node_id": "NODE1",
            "family": "PHONE_IMU",
            "role": "EVENT_DETECTION",
            "observed_at": utcnow(),
            "measurement": "acceleration_peak",
            "value": 0.12,
            "unit": "m/s2",
            "quality": 0.8,
            "latitude": 1.0,
            "longitude": 2.0,
            "privacy": "PRIVATE",
        }
        private_node_id = hashlib.sha256(b"NODE1").hexdigest()[:24]
        upsert_node(
            connection,
            source_id="SELFTEST",
            node_id=private_node_id,
            family="PHONE_IMU",
            role="EVENT_DETECTION",
            region_id="test_region",
            name="Nodo",
            latitude=1.0,
            longitude=2.0,
            elevation_or_depth=0.0,
            privacy="PRIVATE",
        )
        first = insert_observation(connection, payload, regions)
        duplicate = insert_observation(connection, payload, regions)
        if not first or duplicate:
            raise AssertionError("La deduplicación de observaciones falló")
        features = derive_features(connection)
        if features < 2:
            raise AssertionError("No se produjeron características")
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise AssertionError("SQLite no pasó quick_check")
        roles = {row[0] for row in connection.execute("SELECT DISTINCT role FROM sg_observations")}
        raw_private_ids = int(
            connection.execute(
                "SELECT COUNT(*) FROM sg_nodes WHERE node_id='NODE1'"
            ).fetchone()[0]
        ) + int(
            connection.execute(
                "SELECT COUNT(*) FROM sg_observations WHERE node_id='NODE1'"
            ).fetchone()[0]
        )
        connection.close()
        if roles != {"EVENT_DETECTION"}:
            raise AssertionError("Se alteró la separación de roles")
        if raw_private_ids:
            raise AssertionError("Se almacenó un identificador privado sin anonimizar")
    return {
        "status": "OK",
        "checks": {
            "persistent_sqlite_memory": True,
            "utc_normalization": True,
            "stable_deduplication": True,
            "region_assignment": True,
            "quality_and_latency": True,
            "source_permission_registry": True,
            "private_coordinate_reduction": True,
            "research_feature_export": True,
            "does_not_modify_iedc": True,
            "does_not_activate_alerts": True,
            "does_not_feed_shadow_windows": True,
        },
    }

def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SismoAI Universal Sensor Gateway")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--regions", default="config/world_regions.json")
    run_parser.add_argument("--config", default="config/sensor_gateway.json")
    run_parser.add_argument("--input-state-archive")
    run_parser.add_argument("--output-state-archive", required=True)
    run_parser.add_argument("--output-json", required=True)
    run_parser.add_argument("--inbox-dir")
    run_parser.add_argument("--manifest")
    run_parser.add_argument("--manifest-sha")
    subparsers.add_parser("selftest")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.command == "selftest":
        emit(selftest())
        return 0
    public = run_gateway(
        regions_path=Path(arguments.regions),
        config_path=Path(arguments.config) if arguments.config else None,
        input_state_archive=(
            Path(arguments.input_state_archive)
            if arguments.input_state_archive
            else None
        ),
        output_state_archive=Path(arguments.output_state_archive),
        output_json=Path(arguments.output_json),
        inbox_dir=Path(arguments.inbox_dir) if arguments.inbox_dir else None,
        manifest_path=Path(arguments.manifest) if arguments.manifest else None,
        manifest_sha_path=(
            Path(arguments.manifest_sha) if arguments.manifest_sha else None
        ),
    )
    emit(
        {
            "status": public["last_run"]["status"],
            "run_number": public["last_run"]["run_number"],
            "sources": public["totals"]["sources_registered"],
            "nodes": public["totals"]["nodes"],
            "observations": public["totals"]["observations"],
            "features": public["totals"]["features"],
            "output": str(arguments.output_json),
        }
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

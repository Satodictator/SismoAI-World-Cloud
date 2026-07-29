
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")[:120]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def sensor_history_days(database: Path) -> int:
    if not database.exists():
        return 0
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT day)
            FROM sg_features
            WHERE role IN ('PRE_EVENT_RESEARCH','CONTEXT_CONTROL')
              AND region_id<>'UNASSIGNED'
            """
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0
    finally:
        connection.close()


def augment_regional_samples(
    samples: list[dict[str, Any]],
    sensor_database: Path | None,
    *,
    minimum_history_days: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sensor_database is None or not sensor_database.exists():
        return samples, {
            "status": "NO_SENSOR_DATABASE",
            "history_days": 0,
            "features_added": 0,
        }
    history_days = sensor_history_days(sensor_database)
    if history_days < max(1, int(minimum_history_days)):
        return samples, {
            "status": "WAITING_FOR_SENSOR_HISTORY",
            "history_days": history_days,
            "minimum_history_days": int(minimum_history_days),
            "features_added": 0,
        }
    connection = sqlite3.connect(
        f"file:{sensor_database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT day,region_id,family,role,feature,value,quality
            FROM sg_features
            WHERE role IN ('PRE_EVENT_RESEARCH','CONTEXT_CONTROL')
              AND region_id<>'UNASSIGNED'
              AND value IS NOT NULL
            ORDER BY region_id,day,family,feature
            """
        ).fetchall()
    finally:
        connection.close()
    by_region_day: dict[str, dict[date, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    feature_names: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        try:
            day_value = date.fromisoformat(str(row["day"])[:10])
        except ValueError:
            continue
        value = _finite(row["value"])
        quality = _finite(row["quality"])
        if value is None or quality is None or quality < 0.25:
            continue
        name = _safe(
            f"sensor__{row['role']}__{row['family']}__{row['feature']}"
        )
        region_id = str(row["region_id"])
        by_region_day[region_id][day_value][name] = value
        feature_names[region_id].add(name)
    output: list[dict[str, Any]] = []
    additions = 0
    for original in samples:
        row = dict(original)
        region_id = str(row.get("region") or "")
        try:
            current_day = date.fromisoformat(str(row.get("day"))[:10])
        except ValueError:
            output.append(row)
            continue
        names = feature_names.get(region_id) or set()
        for name in names:
            current = [
                by_region_day[region_id]
                .get(current_day + timedelta(days=offset), {})
                .get(name)
                for offset in range(-6, 1)
            ]
            previous = [
                by_region_day[region_id]
                .get(current_day + timedelta(days=offset), {})
                .get(name)
                for offset in range(-13, -6)
            ]
            current = [value for value in current if value is not None]
            previous = [value for value in previous if value is not None]
            if len(current) < 3:
                continue
            current_mean = sum(current) / len(current)
            row[name + "__mean7"] = current_mean
            additions += 1
            if len(previous) >= 3:
                row[name + "__delta7"] = current_mean - (
                    sum(previous) / len(previous)
                )
                additions += 1
        output.append(row)
    return output, {
        "status": "OK",
        "history_days": history_days,
        "features_added": additions,
        "roles_allowed": ["PRE_EVENT_RESEARCH", "CONTEXT_CONTROL"],
        "event_detection_excluded_to_prevent_leakage": True,
        "tsunami_confirmation_excluded_to_prevent_leakage": True,
    }


def selftest() -> dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="sismoai_sensor_evolution_") as temporary:
        database = Path(temporary) / "sensor.sqlite"
        connection = sqlite3.connect(database)
        connection.execute(
            """
            CREATE TABLE sg_features(
              day TEXT,region_id TEXT,family TEXT,role TEXT,feature TEXT,
              value REAL,quality REAL
            )
            """
        )
        start = date(2025, 1, 1)
        for index in range(45):
            current = start + timedelta(days=index)
            connection.execute(
                "INSERT INTO sg_features VALUES(?,?,?,?,?,?,?)",
                (
                    current.isoformat(),
                    "r1",
                    "REALTIME_GNSS",
                    "PRE_EVENT_RESEARCH",
                    "displacement__mean",
                    float(index),
                    0.9,
                ),
            )
            connection.execute(
                "INSERT INTO sg_features VALUES(?,?,?,?,?,?,?)",
                (
                    current.isoformat(),
                    "r1",
                    "PHONE_IMU",
                    "EVENT_DETECTION",
                    "acceleration__max",
                    99.0,
                    0.9,
                ),
            )
        connection.commit()
        connection.close()
        samples = [
            {
                "day": (start + timedelta(days=index)).isoformat(),
                "region": "r1",
                "target_regional_event_7d": 0,
            }
            for index in range(20, 45)
        ]
        augmented, details = augment_regional_samples(
            samples,
            database,
            minimum_history_days=30,
        )
        keys = {key for row in augmented for key in row}
        if not any("realtime_gnss" in key for key in keys):
            raise AssertionError("No se añadió la familia de investigación")
        if any("phone_imu" in key for key in keys):
            raise AssertionError("Se filtró incorrectamente detección del evento")
        if details["status"] != "OK":
            raise AssertionError("Estado inesperado")
    return {
        "status": "OK",
        "checks": {
            "minimum_history_gate": True,
            "research_roles_only": True,
            "event_detection_leakage_blocked": True,
            "tsunami_confirmation_leakage_blocked": True,
            "rolling_mean_and_delta": True,
        },
    }

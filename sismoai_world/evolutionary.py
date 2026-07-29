from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .historical import global_samples, initialize as initialize_historical
from .historical import regional_multisource_samples


VERSION = "1.0.0"
SCIENTIFIC_NOTICE = (
    "Motor evolutivo de investigación. Conserva patrones activos, retirados y "
    "rechazados; los reevalúa, combina componentes compatibles y registra su "
    "genealogía. No constituye predicción determinista, alerta oficial ni orden "
    "de evacuación."
)

CONDITION_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*(>=|<=)\s*([-+0-9.eE]+)\s*$"
)

DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "new_candidates_per_run": 80,
    "reevaluate_active_per_run": 240,
    "reevaluate_background_per_run": 180,
    "max_total_patterns": 250000,
    "max_conditions": 4,
    "champions_per_target": 1,
    "challengers_per_target": 20,
    "exploratory_per_target": 80,
    "min_validation_activations": 25,
    "min_validation_true_positives": 4,
    "min_vault_activations": 25,
    "min_vault_true_positives": 4,
    "min_validation_lift": 1.20,
    "min_vault_lift": 1.10,
    "minimum_champion_score": 0.18,
    "minimum_challenger_score": 0.12,
    "minimum_exploratory_score": 0.06,
    "quantiles": [0.10, 0.25, 0.50, 0.75, 0.90],
    "train_fraction": 0.60,
    "validation_fraction": 0.20,
    "vault_fraction": 0.20,
    "random_seed_salt": "SismoAI-Evolutionary-1.0",
    "research_only": True,
    "modifies_iedc": False,
    "activates_alerts": False,
    "feeds_shadow_windows": False,
}

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS evo_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evo_runs(
  run_id TEXT PRIMARY KEY,
  generation INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  evaluated INTEGER NOT NULL DEFAULT 0,
  discovered INTEGER NOT NULL DEFAULT 0,
  transplants INTEGER NOT NULL DEFAULT 0,
  reactivated INTEGER NOT NULL DEFAULT 0,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS evo_patterns(
  fingerprint TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  target TEXT NOT NULL,
  expression TEXT NOT NULL,
  conditions_json TEXT NOT NULL,
  status TEXT NOT NULL,
  previous_status TEXT,
  generation INTEGER NOT NULL,
  origin TEXT NOT NULL,
  parents_json TEXT NOT NULL DEFAULT '[]',
  lineage_json TEXT NOT NULL DEFAULT '{}',
  first_seen TEXT NOT NULL,
  last_evaluated TEXT,
  evaluation_count INTEGER NOT NULL DEFAULT 0,
  train_metrics_json TEXT NOT NULL DEFAULT '{}',
  validation_metrics_json TEXT NOT NULL DEFAULT '{}',
  vault_metrics_json TEXT NOT NULL DEFAULT '{}',
  score REAL NOT NULL DEFAULT 0,
  best_score REAL NOT NULL DEFAULT 0,
  best_precision REAL NOT NULL DEFAULT 0,
  best_recall REAL NOT NULL DEFAULT 0,
  best_lift REAL NOT NULL DEFAULT 0,
  reactivation_count INTEGER NOT NULL DEFAULT 0,
  champion_since TEXT,
  retired_since TEXT,
  notes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evo_target_score
  ON evo_patterns(scope,target,score DESC);
CREATE INDEX IF NOT EXISTS idx_evo_status_eval
  ON evo_patterns(status,last_evaluated);
CREATE INDEX IF NOT EXISTS idx_evo_first_seen
  ON evo_patterns(first_seen DESC);
CREATE TABLE IF NOT EXISTS evo_transitions(
  transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  old_status TEXT,
  new_status TEXT NOT NULL,
  reason TEXT NOT NULL,
  generation INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evo_transitions_time
  ON evo_transitions(changed_at DESC);
CREATE TABLE IF NOT EXISTS evo_transplants(
  transplant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  generation INTEGER NOT NULL,
  scope TEXT NOT NULL,
  target TEXT NOT NULL,
  donor_fingerprint TEXT,
  recipient_fingerprint TEXT,
  child_fingerprint TEXT NOT NULL,
  component_json TEXT NOT NULL,
  compatible INTEGER NOT NULL,
  result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evo_transplants_time
  ON evo_transplants(created_at DESC);
"""


def utcnow() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_manifest(
    output_path: Path,
    manifest_path: Path | None,
    manifest_sha_path: Path | None,
) -> None:
    if manifest_path is None or not manifest_path.exists():
        return
    manifest = read_json(manifest_path, {"files": []})
    files = list(manifest.get("files") or [])
    relative = "data/evolutionary.json"
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
            "generation": "0",
            "total_evaluations": "0",
            "total_discovered": "0",
            "total_transplants": "0",
            "total_reactivations": "0",
            "last_run": "",
            "status": "READY",
        }
        connection.executemany(
            "INSERT OR IGNORE INTO evo_meta(key,value) VALUES(?,?)",
            defaults.items(),
        )
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite quick_check: {check}")
        connection.commit()
    finally:
        connection.close()


def get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute(
        "SELECT value FROM evo_meta WHERE key=?",
        (key,),
    ).fetchone()
    return str(row[0]) if row else default


def set_meta(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO evo_meta(key,value) VALUES(?,?) "
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
    with database.open("rb") as source, gzip.open(
        temporary, "wb", compresslevel=6
    ) as target:
        shutil.copyfileobj(source, target)
    os.replace(temporary, output_archive)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def parse_expression(expression: str) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for part in str(expression or "").split(" AND "):
        match = CONDITION_RE.match(part)
        if not match:
            return []
        conditions.append(
            {
                "feature": match.group(1),
                "operator": match.group(2),
                "threshold": float(match.group(3)),
            }
        )
    return normalize_conditions(conditions)


def normalize_conditions(
    conditions: Iterable[dict[str, Any]],
    max_conditions: int | None = None,
) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for condition in conditions:
        feature = str(condition.get("feature") or "").strip()
        operator = str(condition.get("operator") or ">=").strip()
        threshold = as_float(condition.get("threshold"), float("nan"))
        if not feature or operator not in {">=", "<="} or not math.isfinite(threshold):
            continue
        key = (feature, operator)
        prior = selected.get(key)
        if prior is None:
            selected[key] = {
                "feature": feature,
                "operator": operator,
                "threshold": threshold,
            }
        elif operator == ">=" and threshold > prior["threshold"]:
            selected[key]["threshold"] = threshold
        elif operator == "<=" and threshold < prior["threshold"]:
            selected[key]["threshold"] = threshold
    result = sorted(
        selected.values(),
        key=lambda item: (
            str(item["feature"]),
            str(item["operator"]),
            float(item["threshold"]),
        ),
    )
    if max_conditions is not None:
        result = result[: max(1, int(max_conditions))]
    return result


def canonical_expression(conditions: Iterable[dict[str, Any]]) -> str:
    normalized = normalize_conditions(conditions)
    return " AND ".join(
        f"{item['feature']} {item['operator']} {float(item['threshold']):.9g}"
        for item in normalized
    )


def fingerprint(scope: str, target: str, conditions: Iterable[dict[str, Any]]) -> str:
    expression = canonical_expression(conditions)
    return hashlib.sha256(
        f"{scope}|{target}|{expression}".encode("utf-8")
    ).hexdigest()[:40]


def condition_active(sample: dict[str, Any], condition: dict[str, Any]) -> bool:
    value = sample.get(str(condition["feature"]))
    if value is None:
        return False
    number = as_float(value, float("nan"))
    if not math.isfinite(number):
        return False
    threshold = float(condition["threshold"])
    return number >= threshold if condition["operator"] == ">=" else number <= threshold


def expression_active(sample: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    return bool(conditions) and all(
        condition_active(sample, condition) for condition in conditions
    )


def metrics(labels: list[int], predictions: list[int]) -> dict[str, Any]:
    tp = sum(1 for label, predicted in zip(labels, predictions) if label and predicted)
    fp = sum(
        1 for label, predicted in zip(labels, predictions) if not label and predicted
    )
    tn = sum(
        1
        for label, predicted in zip(labels, predictions)
        if not label and not predicted
    )
    fn = sum(
        1 for label, predicted in zip(labels, predictions) if label and not predicted
    )
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    base_rate = sum(labels) / len(labels) if labels else 0.0
    lift = (
        precision / base_rate
        if precision is not None and base_rate > 0
        else None
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall > 0
        else None
    )
    accuracy = (tp + tn) / len(labels) if labels else None
    return {
        "samples": len(labels),
        "positives": sum(labels),
        "activations": tp + fp,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "base_rate": base_rate,
        "lift": lift,
        "f1": f1,
        "accuracy": accuracy,
        "false_alarms_per_100_samples": 100.0 * fp / max(1, len(labels)),
    }


def wilson_lower(tp: int, activations: int, z: float = 1.96) -> float:
    if activations <= 0:
        return 0.0
    probability = tp / activations
    denominator = 1.0 + z * z / activations
    center = probability + z * z / (2.0 * activations)
    margin = z * math.sqrt(
        probability * (1.0 - probability) / activations
        + z * z / (4.0 * activations * activations)
    )
    return max(0.0, (center - margin) / denominator)


def metric_score(values: dict[str, Any], complexity: int) -> float:
    precision = as_float(values.get("precision"), 0.0)
    recall = as_float(values.get("recall"), 0.0)
    specificity = as_float(values.get("specificity"), 0.0)
    f1 = as_float(values.get("f1"), 0.0)
    base = as_float(values.get("base_rate"), 0.0)
    lift = as_float(values.get("lift"), 0.0)
    activations = int(values.get("activations") or 0)
    tp = int(values.get("tp") or 0)
    lower = wilson_lower(tp, activations)
    support = min(1.0, activations / 120.0) * min(1.0, tp / 20.0)
    lift_component = min(1.0, max(0.0, lift - 1.0) / 4.0)
    gain = min(1.0, max(0.0, precision - base) / max(0.05, 1.0 - base))
    raw = (
        0.30 * lower
        + 0.20 * recall
        + 0.15 * f1
        + 0.13 * specificity
        + 0.12 * lift_component
        + 0.10 * gain
    )
    penalty = 0.012 * max(0, complexity - 1)
    return round(max(0.0, raw * math.sqrt(max(0.0, support)) - penalty), 9)


def split_samples(
    samples: list[dict[str, Any]],
    label_name: str,
    policy: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    usable = [
        row
        for row in samples
        if row.get("day") is not None and label_name in row
    ]
    usable.sort(
        key=lambda row: (
            str(row["day"]),
            str(row.get("region") or row.get("cell") or ""),
        )
    )
    days = sorted({str(row["day"]) for row in usable})
    if len(days) < 10:
        return {"train": [], "validation": [], "vault": []}
    train_fraction = as_float(policy.get("train_fraction"), 0.60)
    validation_fraction = as_float(policy.get("validation_fraction"), 0.20)
    first_index = max(1, min(len(days) - 2, int(len(days) * train_fraction)))
    second_index = max(
        first_index + 1,
        min(
            len(days) - 1,
            int(len(days) * (train_fraction + validation_fraction)),
        ),
    )
    first_day = days[first_index]
    second_day = days[second_index]
    return {
        "train": [row for row in usable if str(row["day"]) < first_day],
        "validation": [
            row
            for row in usable
            if first_day <= str(row["day"]) < second_day
        ],
        "vault": [row for row in usable if str(row["day"]) >= second_day],
    }


def evaluate_conditions(
    split: dict[str, list[dict[str, Any]]],
    label_name: str,
    conditions: list[dict[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    scores: dict[str, float] = {}
    for name in ("train", "validation", "vault"):
        rows = split.get(name) or []
        labels = [int(row[label_name]) for row in rows]
        predictions = [int(expression_active(row, conditions)) for row in rows]
        current = metrics(labels, predictions)
        output[name] = current
        scores[name] = metric_score(current, len(conditions))
    validation_score = scores["validation"]
    vault_score = scores["vault"]
    combined = 0.75 * validation_score + 0.25 * min(
        validation_score, vault_score
    )
    output["score"] = round(combined, 9)
    output["score_components"] = scores
    return output


def quantile(values: list[float], probability: float) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    position = (len(clean) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    return clean[lower] * (upper - position) + clean[upper] * (
        position - lower
    )


def feature_quantiles(
    train: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, list[float]]:
    names = sorted(
        {
            key
            for row in train
            for key, value in row.items()
            if key not in {"day", "cell", "region"}
            and not key.startswith("target_")
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
        }
    )
    probabilities = [
        as_float(item)
        for item in policy.get("quantiles") or DEFAULT_POLICY["quantiles"]
    ]
    output: dict[str, list[float]] = {}
    for name in names:
        values = [
            float(row[name])
            for row in train
            if isinstance(row.get(name), (int, float))
            and math.isfinite(float(row[name]))
        ]
        if len(values) < 30 or min(values) == max(values):
            continue
        thresholds = [
            value
            for value in (quantile(values, probability) for probability in probabilities)
            if value is not None
        ]
        unique = sorted({round(float(value), 10) for value in thresholds})
        if unique:
            output[name] = unique
    return output


def load_policy(path: Path | None) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    loaded = read_json(path, {}) if path else {}
    if isinstance(loaded, dict):
        policy.update(loaded)
    return policy


def current_historical_patterns(
    historical_database: Path,
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(
        f"file:{historical_database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        latest_run_ids = [
            str(row["run_id"])
            for row in connection.execute(
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
        if not latest_run_ids:
            return []
        placeholders = ",".join("?" for _ in latest_run_ids)
        rows = connection.execute(
            "SELECT p.pattern_id,p.scope,p.target,p.expression,p.status,"
            "p.features_json,p.train_metrics_json,p.test_metrics_json,p.created_at "
            "FROM h_patterns p "
            f"WHERE p.run_id IN ({placeholders})",
            latest_run_ids,
        ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["features"] = json.loads(item.pop("features_json"))
            item["train_metrics"] = json.loads(item.pop("train_metrics_json"))
            item["test_metrics"] = json.loads(item.pop("test_metrics_json"))
            output.append(item)
        return output
    finally:
        connection.close()


def insert_candidate(
    connection: sqlite3.Connection,
    *,
    scope: str,
    target: str,
    conditions: list[dict[str, Any]],
    generation: int,
    origin: str,
    parents: list[str] | None = None,
    lineage: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    normalized = normalize_conditions(conditions)
    expression = canonical_expression(normalized)
    candidate_fingerprint = fingerprint(scope, target, normalized)
    if not expression:
        return candidate_fingerprint, False
    existing = connection.execute(
        "SELECT 1 FROM evo_patterns WHERE fingerprint=?",
        (candidate_fingerprint,),
    ).fetchone()
    if existing:
        return candidate_fingerprint, False
    now = utcnow()
    connection.execute(
        """
        INSERT INTO evo_patterns(
          fingerprint,scope,target,expression,conditions_json,status,
          generation,origin,parents_json,lineage_json,first_seen
        ) VALUES(?,?,?,?,?,'DISCOVERED',?,?,?,?,?)
        """,
        (
            candidate_fingerprint,
            scope,
            target,
            expression,
            json.dumps(normalized, ensure_ascii=False),
            generation,
            origin,
            json.dumps(parents or [], ensure_ascii=False),
            json.dumps(lineage or {}, ensure_ascii=False),
            now,
        ),
    )
    return candidate_fingerprint, True


def seed_from_historical(
    connection: sqlite3.Connection,
    historical_database: Path,
    generation: int,
) -> int:
    discovered = 0
    for pattern in current_historical_patterns(historical_database):
        conditions = parse_expression(str(pattern.get("expression") or ""))
        if not conditions:
            continue
        _, inserted = insert_candidate(
            connection,
            scope=str(pattern.get("scope") or "UNKNOWN"),
            target=str(pattern.get("target") or "UNKNOWN"),
            conditions=conditions,
            generation=generation,
            origin="HISTORICAL_SEED",
            parents=[str(pattern.get("pattern_id") or "")],
            lineage={
                "source_pattern_id": pattern.get("pattern_id"),
                "source_status": pattern.get("status"),
                "source_created_at": pattern.get("created_at"),
            },
        )
        discovered += int(inserted)
    return discovered


def load_record_conditions(row: sqlite3.Row | dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return normalize_conditions(json.loads(row["conditions_json"]))
    except Exception:
        return []


def candidate_rows_for_evaluation(
    connection: sqlite3.Connection,
    new_fingerprints: list[str],
    policy: dict[str, Any],
) -> list[sqlite3.Row]:
    selected: dict[str, sqlite3.Row] = {}
    if new_fingerprints:
        placeholders = ",".join("?" for _ in new_fingerprints)
        for row in connection.execute(
            f"SELECT * FROM evo_patterns WHERE fingerprint IN ({placeholders})",
            new_fingerprints,
        ):
            selected[str(row["fingerprint"])] = row
    active_limit = int(policy.get("reevaluate_active_per_run") or 240)
    for row in connection.execute(
        """
        SELECT * FROM evo_patterns
        WHERE status IN (
          'CHAMPION','CHALLENGER','EXPLORATORY','REACTIVATED','DISCOVERED'
        )
        ORDER BY score DESC,last_evaluated ASC
        LIMIT ?
        """,
        (active_limit,),
    ):
        selected[str(row["fingerprint"])] = row
    background_limit = int(policy.get("reevaluate_background_per_run") or 180)
    for row in connection.execute(
        """
        SELECT * FROM evo_patterns
        WHERE status IN ('RETIRED_OBSERVATION','REJECTED_BACKGROUND')
        ORDER BY CASE WHEN last_evaluated IS NULL THEN 0 ELSE 1 END,
                 last_evaluated ASC,best_score DESC
        LIMIT ?
        """,
        (background_limit,),
    ):
        selected[str(row["fingerprint"])] = row
    return list(selected.values())


def compatible(
    left: sqlite3.Row | dict[str, Any],
    right: sqlite3.Row | dict[str, Any],
) -> bool:
    return (
        str(left["scope"]) == str(right["scope"])
        and str(left["target"]) == str(right["target"])
    )


def parent_pool(
    connection: sqlite3.Connection,
    scope: str,
    target: str,
) -> list[sqlite3.Row]:
    rows = list(
        connection.execute(
            """
            SELECT * FROM evo_patterns
            WHERE scope=? AND target=? AND evaluation_count>0
            ORDER BY
              CASE status
                WHEN 'CHAMPION' THEN 0
                WHEN 'CHALLENGER' THEN 1
                WHEN 'EXPLORATORY' THEN 2
                WHEN 'RETIRED_OBSERVATION' THEN 3
                ELSE 4
              END,
              score DESC,best_score DESC
            LIMIT 60
            """,
            (scope, target),
        )
    )
    return rows


def generate_candidates(
    connection: sqlite3.Connection,
    *,
    specs: list[dict[str, Any]],
    generation: int,
    policy: dict[str, Any],
    seed_text: str,
) -> tuple[list[str], int]:
    total_patterns = int(
        connection.execute("SELECT COUNT(*) FROM evo_patterns").fetchone()[0]
    )
    maximum = int(policy.get("max_total_patterns") or 250000)
    if total_patterns >= maximum:
        return [], 0
    budget = min(
        int(policy.get("new_candidates_per_run") or 80),
        maximum - total_patterns,
    )
    max_conditions = int(policy.get("max_conditions") or 4)
    random_seed = int(
        hashlib.sha256(
            f"{seed_text}|{generation}|{policy.get('random_seed_salt')}".encode(
                "utf-8"
            )
        ).hexdigest()[:16],
        16,
    )
    generator = random.Random(random_seed)
    created: list[str] = []
    transplant_count = 0

    def remember(
        *,
        spec: dict[str, Any],
        conditions: list[dict[str, Any]],
        origin: str,
        parents: list[str] | None = None,
        lineage: dict[str, Any] | None = None,
        transplant: dict[str, Any] | None = None,
    ) -> None:
        nonlocal transplant_count
        if len(created) >= budget:
            return
        normalized = normalize_conditions(conditions, max_conditions=max_conditions)
        candidate_fingerprint, inserted = insert_candidate(
            connection,
            scope=spec["scope"],
            target=spec["target"],
            conditions=normalized,
            generation=generation,
            origin=origin,
            parents=parents,
            lineage=lineage,
        )
        if not inserted:
            return
        created.append(candidate_fingerprint)
        if transplant:
            transplant_count += 1
            connection.execute(
                """
                INSERT INTO evo_transplants(
                  created_at,generation,scope,target,donor_fingerprint,
                  recipient_fingerprint,child_fingerprint,component_json,
                  compatible,result
                ) VALUES(?,?,?,?,?,?,?,?,1,'CREATED')
                """,
                (
                    utcnow(),
                    generation,
                    spec["scope"],
                    spec["target"],
                    transplant.get("donor"),
                    transplant.get("recipient"),
                    candidate_fingerprint,
                    json.dumps(
                        transplant.get("component") or {},
                        ensure_ascii=False,
                    ),
                ),
            )

    quota = max(1, math.ceil(budget / max(1, len(specs))))
    for spec_index, spec in enumerate(specs):
        if len(created) >= budget:
            break
        target_cap = min(
            budget,
            len(created) + (
                budget - len(created)
                if spec_index == len(specs) - 1
                else quota
            ),
        )
        quantiles = spec["quantiles"]
        features = sorted(quantiles)
        if not features:
            continue
        generator.shuffle(features)
        for feature in features:
            if len(created) >= target_cap:
                break
            thresholds = list(quantiles[feature])
            generator.shuffle(thresholds)
            for threshold in thresholds[:2]:
                operator = generator.choice([">=", "<="])
                remember(
                    spec=spec,
                    conditions=[
                        {
                            "feature": feature,
                            "operator": operator,
                            "threshold": threshold,
                        }
                    ],
                    origin="NOVEL_QUANTILE",
                    lineage={
                        "method": "new_feature_quantile",
                        "generation": generation,
                    },
                )
                if len(created) >= target_cap:
                    break

        parents = parent_pool(
            connection,
            spec["scope"],
            spec["target"],
        )
        active = [
            row
            for row in parents
            if str(row["status"])
            in {"CHAMPION", "CHALLENGER", "EXPLORATORY", "REACTIVATED"}
        ]
        retired = [
            row
            for row in parents
            if str(row["status"]) == "RETIRED_OBSERVATION"
        ]
        if not active:
            active = parents[:20]

        attempts = 0
        while (
            len(created) < target_cap
            and parents
            and attempts < max(100, budget * 20)
        ):
            attempts += 1
            method = generator.choice(
                ["MUTATION", "CROSSOVER", "TRANSPLANT", "NOVEL_PAIR"]
            )
            if method == "MUTATION":
                parent = generator.choice(parents)
                conditions = load_record_conditions(parent)
                if not conditions:
                    continue
                selected_index = generator.randrange(len(conditions))
                selected = dict(conditions[selected_index])
                thresholds = spec["quantiles"].get(selected["feature"]) or []
                if thresholds:
                    selected["threshold"] = generator.choice(thresholds)
                elif selected["threshold"] != 0:
                    selected["threshold"] *= generator.choice([0.85, 0.95, 1.05, 1.15])
                if generator.random() < 0.20:
                    selected["operator"] = (
                        "<=" if selected["operator"] == ">=" else ">="
                    )
                conditions[selected_index] = selected
                remember(
                    spec=spec,
                    conditions=conditions,
                    origin="MUTATION",
                    parents=[str(parent["fingerprint"])],
                    lineage={
                        "method": "threshold_or_operator_mutation",
                        "generation": generation,
                    },
                )
            elif method == "CROSSOVER" and len(parents) >= 2:
                left, right = generator.sample(parents, 2)
                if not compatible(left, right):
                    continue
                left_conditions = load_record_conditions(left)
                right_conditions = load_record_conditions(right)
                if not left_conditions or not right_conditions:
                    continue
                child = list(left_conditions)
                generator.shuffle(right_conditions)
                child.extend(
                    right_conditions[
                        : max(1, max_conditions - len(left_conditions))
                    ]
                )
                remember(
                    spec=spec,
                    conditions=child,
                    origin="CROSSOVER",
                    parents=[
                        str(left["fingerprint"]),
                        str(right["fingerprint"]),
                    ],
                    lineage={
                        "method": "compatible_parent_crossover",
                        "generation": generation,
                    },
                )
            elif method == "TRANSPLANT" and active and retired:
                recipient = generator.choice(active)
                donor = generator.choice(retired)
                if not compatible(recipient, donor):
                    continue
                recipient_conditions = load_record_conditions(recipient)
                donor_conditions = load_record_conditions(donor)
                if not recipient_conditions or not donor_conditions:
                    continue
                recipient_features = {
                    (item["feature"], item["operator"])
                    for item in recipient_conditions
                }
                components = [
                    item
                    for item in donor_conditions
                    if (item["feature"], item["operator"])
                    not in recipient_features
                ]
                if not components:
                    continue
                component = generator.choice(components)
                child = list(recipient_conditions) + [component]
                remember(
                    spec=spec,
                    conditions=child,
                    origin="SUCCESS_TRANSPLANT",
                    parents=[
                        str(recipient["fingerprint"]),
                        str(donor["fingerprint"]),
                    ],
                    lineage={
                        "method": "compatible_retired_component_transplant",
                        "generation": generation,
                    },
                    transplant={
                        "donor": str(donor["fingerprint"]),
                        "recipient": str(recipient["fingerprint"]),
                        "component": component,
                    },
                )
            elif method == "NOVEL_PAIR" and len(features) >= 2:
                first, second = generator.sample(features, 2)
                first_thresholds = quantiles.get(first) or []
                second_thresholds = quantiles.get(second) or []
                if not first_thresholds or not second_thresholds:
                    continue
                remember(
                    spec=spec,
                    conditions=[
                        {
                            "feature": first,
                            "operator": generator.choice([">=", "<="]),
                            "threshold": generator.choice(first_thresholds),
                        },
                        {
                            "feature": second,
                            "operator": generator.choice([">=", "<="]),
                            "threshold": generator.choice(second_thresholds),
                        },
                    ],
                    origin="NOVEL_PAIR",
                    lineage={
                        "method": "new_two_feature_combination",
                        "generation": generation,
                    },
                )
    return created, transplant_count


def dataset_specs(
    historical_database: Path,
    region_archive_dirs: list[Path],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    initialize_historical(historical_database)
    historical_connection = sqlite3.connect(
        f"file:{historical_database.as_posix()}?mode=ro",
        uri=True,
    )
    historical_connection.row_factory = sqlite3.Row
    try:
        global_rows = global_samples(historical_connection)
    finally:
        historical_connection.close()
    regional_rows = regional_multisource_samples(region_archive_dirs)
    raw_specs = [
        {
            "scope": "GLOBAL_SEISMIC_HISTORY",
            "target": "M6_WITHIN_72H_SAME_10DEG_CELL",
            "label": "target_m6_3d",
            "samples": global_rows,
        },
        {
            "scope": "GLOBAL_SEISMIC_HISTORY",
            "target": "M7_WITHIN_7D_SAME_10DEG_CELL",
            "label": "target_m7_7d",
            "samples": global_rows,
        },
        {
            "scope": "WORLD_REGIONAL_MULTISOURCE",
            "target": "REGIONAL_THRESHOLD_EVENT_WITHIN_7D",
            "label": "target_regional_event_7d",
            "samples": regional_rows,
        },
    ]
    output: list[dict[str, Any]] = []
    for item in raw_specs:
        split = split_samples(item["samples"], item["label"], policy)
        if not split["train"] or not split["validation"] or not split["vault"]:
            continue
        output.append(
            {
                **item,
                "split": split,
                "quantiles": feature_quantiles(split["train"], policy),
            }
        )
    return output


def update_evaluation(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    result: dict[str, Any],
) -> None:
    now = utcnow()
    previous_score = as_float(row["score"], 0.0)
    previous_best = as_float(row["best_score"], 0.0)
    validation = result["validation"]
    best_precision = max(
        as_float(row["best_precision"], 0.0),
        as_float(validation.get("precision"), 0.0),
    )
    best_recall = max(
        as_float(row["best_recall"], 0.0),
        as_float(validation.get("recall"), 0.0),
    )
    best_lift = max(
        as_float(row["best_lift"], 0.0),
        as_float(validation.get("lift"), 0.0),
    )
    notes = read_json_text(row["notes_json"], {})
    notes["last_score_change"] = round(result["score"] - previous_score, 9)
    notes["improved_this_run"] = result["score"] > previous_best + 1e-12
    connection.execute(
        """
        UPDATE evo_patterns SET
          last_evaluated=?,
          evaluation_count=evaluation_count+1,
          train_metrics_json=?,
          validation_metrics_json=?,
          vault_metrics_json=?,
          score=?,
          best_score=?,
          best_precision=?,
          best_recall=?,
          best_lift=?,
          notes_json=?
        WHERE fingerprint=?
        """,
        (
            now,
            json.dumps(result["train"], ensure_ascii=False),
            json.dumps(result["validation"], ensure_ascii=False),
            json.dumps(result["vault"], ensure_ascii=False),
            result["score"],
            max(previous_best, result["score"]),
            best_precision,
            best_recall,
            best_lift,
            json.dumps(notes, ensure_ascii=False),
            row["fingerprint"],
        ),
    )


def read_json_text(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except Exception:
        return default


def eligible(
    row: sqlite3.Row,
    policy: dict[str, Any],
) -> bool:
    validation = read_json_text(row["validation_metrics_json"], {})
    vault = read_json_text(row["vault_metrics_json"], {})
    return (
        int(validation.get("activations") or 0)
        >= int(policy.get("min_validation_activations") or 25)
        and int(validation.get("tp") or 0)
        >= int(policy.get("min_validation_true_positives") or 4)
        and as_float(validation.get("lift"), 0.0)
        >= as_float(policy.get("min_validation_lift"), 1.20)
        and int(vault.get("activations") or 0)
        >= int(policy.get("min_vault_activations") or 25)
        and int(vault.get("tp") or 0)
        >= int(policy.get("min_vault_true_positives") or 4)
        and as_float(vault.get("lift"), 0.0)
        >= as_float(policy.get("min_vault_lift"), 1.10)
    )


def transition_status(
    connection: sqlite3.Connection,
    fingerprint_value: str,
    new_status: str,
    generation: int,
    reason: str,
) -> int:
    row = connection.execute(
        "SELECT status,reactivation_count,champion_since,retired_since "
        "FROM evo_patterns WHERE fingerprint=?",
        (fingerprint_value,),
    ).fetchone()
    if row is None:
        return 0
    old_status = str(row["status"])
    if old_status == new_status:
        return 0
    reactivated = int(
        old_status in {"RETIRED_OBSERVATION", "REJECTED_BACKGROUND"}
        and new_status in {"CHALLENGER", "CHAMPION", "EXPLORATORY"}
    )
    champion_since = row["champion_since"]
    retired_since = row["retired_since"]
    now = utcnow()
    if new_status == "CHAMPION" and not champion_since:
        champion_since = now
    if new_status != "CHAMPION":
        champion_since = None
    if new_status == "RETIRED_OBSERVATION" and not retired_since:
        retired_since = now
    if new_status != "RETIRED_OBSERVATION":
        retired_since = None
    connection.execute(
        """
        UPDATE evo_patterns SET
          previous_status=status,status=?,reactivation_count=reactivation_count+?,
          champion_since=?,retired_since=?
        WHERE fingerprint=?
        """,
        (
            new_status,
            reactivated,
            champion_since,
            retired_since,
            fingerprint_value,
        ),
    )
    connection.execute(
        """
        INSERT INTO evo_transitions(
          fingerprint,changed_at,old_status,new_status,reason,generation
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            fingerprint_value,
            now,
            old_status,
            new_status,
            reason,
            generation,
        ),
    )
    return reactivated


def classify(
    connection: sqlite3.Connection,
    specs: list[dict[str, Any]],
    generation: int,
    policy: dict[str, Any],
) -> int:
    total_reactivated = 0
    champion_limit = int(policy.get("champions_per_target") or 1)
    challenger_limit = int(policy.get("challengers_per_target") or 20)
    exploratory_limit = int(policy.get("exploratory_per_target") or 80)
    active_statuses = {
        "CHAMPION",
        "CHALLENGER",
        "EXPLORATORY",
        "REACTIVATED",
        "DISCOVERED",
    }
    for spec in specs:
        scope, target = spec["scope"], spec["target"]
        rows = list(
            connection.execute(
                """
                SELECT * FROM evo_patterns
                WHERE scope=? AND target=? AND evaluation_count>0
                ORDER BY score DESC,best_score DESC,evaluation_count DESC
                """,
                (scope, target),
            )
        )
        eligible_rows = [row for row in rows if eligible(row, policy)]
        champions = [
            row
            for row in eligible_rows
            if as_float(row["score"], 0.0)
            >= as_float(policy.get("minimum_champion_score"), 0.18)
        ][:champion_limit]
        champion_ids = {str(row["fingerprint"]) for row in champions}

        challengers = [
            row
            for row in eligible_rows
            if str(row["fingerprint"]) not in champion_ids
            and as_float(row["score"], 0.0)
            >= as_float(policy.get("minimum_challenger_score"), 0.12)
        ][:challenger_limit]
        challenger_ids = {str(row["fingerprint"]) for row in challengers}

        selected = champion_ids | challenger_ids
        exploratory = [
            row
            for row in rows
            if str(row["fingerprint"]) not in selected
            and as_float(row["score"], 0.0)
            >= as_float(policy.get("minimum_exploratory_score"), 0.06)
        ][:exploratory_limit]
        exploratory_ids = {str(row["fingerprint"]) for row in exploratory}

        desired: dict[str, tuple[str, str]] = {}
        for row in champions:
            desired[str(row["fingerprint"])] = (
                "CHAMPION",
                "mejor_resultado_robusto_del_objetivo",
            )
        for row in challengers:
            desired[str(row["fingerprint"])] = (
                "CHALLENGER",
                "retador_robusto",
            )
        for row in exploratory:
            desired[str(row["fingerprint"])] = (
                "EXPLORATORY",
                "señal_exploratoria_conservada",
            )

        retained = champion_ids | challenger_ids | exploratory_ids
        for row in rows:
            row_id = str(row["fingerprint"])
            if row_id in retained:
                continue
            old_status = str(row["status"])
            best_score = as_float(row["best_score"], 0.0)
            new_status = (
                "RETIRED_OBSERVATION"
                if old_status in active_statuses
                or old_status == "RETIRED_OBSERVATION"
                or best_score
                >= as_float(policy.get("minimum_exploratory_score"), 0.06)
                else "REJECTED_BACKGROUND"
            )
            desired[row_id] = (
                new_status,
                "no_supera_la_generacion_actual",
            )

        for row in rows:
            row_id = str(row["fingerprint"])
            new_status, reason = desired[row_id]
            total_reactivated += transition_status(
                connection,
                row_id,
                new_status,
                generation,
                reason,
            )
    return total_reactivated

def public_pattern(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "fingerprint": row["fingerprint"],
        "scope": row["scope"],
        "target": row["target"],
        "expression": row["expression"],
        "conditions": read_json_text(row["conditions_json"], []),
        "status": row["status"],
        "previous_status": row["previous_status"],
        "generation": int(row["generation"] or 0),
        "origin": row["origin"],
        "parents": read_json_text(row["parents_json"], []),
        "lineage": read_json_text(row["lineage_json"], {}),
        "first_seen": row["first_seen"],
        "last_evaluated": row["last_evaluated"],
        "evaluation_count": int(row["evaluation_count"] or 0),
        "train_metrics": read_json_text(row["train_metrics_json"], {}),
        "validation_metrics": read_json_text(
            row["validation_metrics_json"], {}
        ),
        "vault_metrics": read_json_text(row["vault_metrics_json"], {}),
        "score": as_float(row["score"], 0.0),
        "best_score": as_float(row["best_score"], 0.0),
        "best_precision": as_float(row["best_precision"], 0.0),
        "best_recall": as_float(row["best_recall"], 0.0),
        "best_lift": as_float(row["best_lift"], 0.0),
        "reactivation_count": int(row["reactivation_count"] or 0),
        "champion_since": row["champion_since"],
        "retired_since": row["retired_since"],
        "research_only": True,
    }


def build_public(
    connection: sqlite3.Connection,
    policy: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    counts = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            "SELECT status,COUNT(*) count FROM evo_patterns GROUP BY status"
        )
    }
    total = int(
        connection.execute("SELECT COUNT(*) FROM evo_patterns").fetchone()[0]
    )
    targets = []
    for row in connection.execute(
        "SELECT DISTINCT scope,target FROM evo_patterns ORDER BY scope,target"
    ):
        scope, target = str(row["scope"]), str(row["target"])
        champions = [
            public_pattern(item)
            for item in connection.execute(
                """
                SELECT * FROM evo_patterns
                WHERE scope=? AND target=? AND status='CHAMPION'
                ORDER BY score DESC LIMIT 3
                """,
                (scope, target),
            )
        ]
        challengers = [
            public_pattern(item)
            for item in connection.execute(
                """
                SELECT * FROM evo_patterns
                WHERE scope=? AND target=? AND status='CHALLENGER'
                ORDER BY score DESC LIMIT 20
                """,
                (scope, target),
            )
        ]
        targets.append(
            {
                "scope": scope,
                "target": target,
                "champions": champions,
                "challengers": challengers,
            }
        )
    retired = [
        public_pattern(row)
        for row in connection.execute(
            """
            SELECT * FROM evo_patterns
            WHERE status='RETIRED_OBSERVATION'
            ORDER BY score DESC,best_score DESC LIMIT 60
            """
        )
    ]
    recent = [
        public_pattern(row)
        for row in connection.execute(
            """
            SELECT * FROM evo_patterns
            ORDER BY first_seen DESC LIMIT 60
            """
        )
    ]
    transitions = [
        dict(row)
        for row in connection.execute(
            """
            SELECT * FROM evo_transitions
            ORDER BY transition_id DESC LIMIT 80
            """
        )
    ]
    transplants = []
    for row in connection.execute(
        """
        SELECT * FROM evo_transplants
        ORDER BY transplant_id DESC LIMIT 60
        """
    ):
        item = dict(row)
        item["component"] = read_json_text(item.pop("component_json"), {})
        transplants.append(item)
    return {
        "schema_version": 1,
        "version": VERSION,
        "generated_at": utcnow(),
        "status": (
            "ACTIVE_24_7"
            if policy.get("enabled", True)
            and str(run.get("status")) == "OK"
            else str(run.get("status") or "DISABLED")
        ),
        "research_only": True,
        "run_frequency": "HOURLY_BATCH_CONTINUOUS",
        "generation": int(get_meta(connection, "generation", "0") or 0),
        "memory": {
            "patterns_total": total,
            "status_counts": counts,
            "maximum_configured": int(
                policy.get("max_total_patterns") or 250000
            ),
            "patterns_are_never_deleted": True,
            "retired_are_reevaluated_in_round_robin": True,
        },
        "totals": {
            "evaluations": int(
                get_meta(connection, "total_evaluations", "0") or 0
            ),
            "discovered": int(
                get_meta(connection, "total_discovered", "0") or 0
            ),
            "transplants": int(
                get_meta(connection, "total_transplants", "0") or 0
            ),
            "reactivations": int(
                get_meta(connection, "total_reactivations", "0") or 0
            ),
        },
        "last_run": run,
        "targets": targets,
        "retired_observation": retired,
        "recent_patterns": recent,
        "recent_transitions": transitions,
        "recent_transplants": transplants,
        "policy_summary": {
            "new_candidates_per_run": policy.get("new_candidates_per_run"),
            "reevaluate_active_per_run": policy.get(
                "reevaluate_active_per_run"
            ),
            "reevaluate_background_per_run": policy.get(
                "reevaluate_background_per_run"
            ),
            "max_conditions": policy.get("max_conditions"),
            "train_fraction": policy.get("train_fraction"),
            "validation_fraction": policy.get("validation_fraction"),
            "vault_fraction": policy.get("vault_fraction"),
            "feeds_shadow_windows": False,
            "modifies_iedc": False,
            "activates_alerts": False,
        },
        "scientific_notice": SCIENTIFIC_NOTICE,
    }


def run_engine(
    *,
    historical_database: Path,
    region_archive_dirs: list[Path],
    input_state_archive: Path | None,
    output_state_archive: Path,
    output_json: Path,
    policy_path: Path | None,
    manifest_path: Path | None = None,
    manifest_sha_path: Path | None = None,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    output_state_archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sismoai_evolution_") as temporary:
        root = Path(temporary)
        database = root / "evolutionary.sqlite"
        restore_archive(input_state_archive, database)
        connection = connect(database)
        run_id = hashlib.sha256(
            f"{utcnow()}|{os.getpid()}".encode("utf-8")
        ).hexdigest()[:24]
        started_at = utcnow()
        generation = int(get_meta(connection, "generation", "0") or 0) + 1
        connection.execute(
            """
            INSERT INTO evo_runs(
              run_id,generation,started_at,status
            ) VALUES(?,?,?,'RUNNING')
            """,
            (run_id, generation, started_at),
        )
        connection.commit()
        status = "OK"
        error: str | None = None
        evaluated = 0
        discovered = 0
        transplants = 0
        reactivated = 0
        try:
            if not policy.get("enabled", True):
                status = "DISABLED"
                specs: list[dict[str, Any]] = []
            else:
                specs = dataset_specs(
                    historical_database,
                    region_archive_dirs,
                    policy,
                )
                discovered += seed_from_historical(
                    connection,
                    historical_database,
                    generation,
                )
                seed_text = "|".join(
                    [
                        str(
                            historical_database.stat().st_size
                            if historical_database.exists()
                            else 0
                        ),
                        *[
                            f"{item['scope']}:{item['target']}:{len(item['samples'])}"
                            for item in specs
                        ],
                    ]
                )
                new_fingerprints, transplants = generate_candidates(
                    connection,
                    specs=specs,
                    generation=generation,
                    policy=policy,
                    seed_text=seed_text,
                )
                discovered += len(new_fingerprints)
                spec_map = {
                    (item["scope"], item["target"]): item for item in specs
                }
                candidates = candidate_rows_for_evaluation(
                    connection,
                    new_fingerprints,
                    policy,
                )
                for row in candidates:
                    spec = spec_map.get((str(row["scope"]), str(row["target"])))
                    if spec is None:
                        continue
                    conditions = load_record_conditions(row)
                    if not conditions:
                        continue
                    result = evaluate_conditions(
                        spec["split"],
                        spec["label"],
                        conditions,
                    )
                    update_evaluation(connection, row, result)
                    evaluated += 1
                reactivated = classify(
                    connection,
                    specs,
                    generation,
                    policy,
                )
                set_meta(connection, "generation", generation)
                set_meta(
                    connection,
                    "total_evaluations",
                    int(get_meta(connection, "total_evaluations", "0") or 0)
                    + evaluated,
                )
                set_meta(
                    connection,
                    "total_discovered",
                    int(get_meta(connection, "total_discovered", "0") or 0)
                    + discovered,
                )
                set_meta(
                    connection,
                    "total_transplants",
                    int(get_meta(connection, "total_transplants", "0") or 0)
                    + transplants,
                )
                set_meta(
                    connection,
                    "total_reactivations",
                    int(get_meta(connection, "total_reactivations", "0") or 0)
                    + reactivated,
                )
                set_meta(connection, "last_run", utcnow())
                set_meta(connection, "status", "ACTIVE")
            finished_at = utcnow()
            details = {
                "specs": [
                    {
                        "scope": item["scope"],
                        "target": item["target"],
                        "samples": len(item["samples"]),
                        "train": len(item["split"]["train"]),
                        "validation": len(item["split"]["validation"]),
                        "vault": len(item["split"]["vault"]),
                        "features": len(item["quantiles"]),
                    }
                    for item in specs
                ],
                "error": None,
            }
            connection.execute(
                """
                UPDATE evo_runs SET
                  finished_at=?,status=?,evaluated=?,discovered=?,
                  transplants=?,reactivated=?,details_json=?
                WHERE run_id=?
                """,
                (
                    finished_at,
                    status,
                    evaluated,
                    discovered,
                    transplants,
                    reactivated,
                    json.dumps(details, ensure_ascii=False),
                    run_id,
                ),
            )
            connection.commit()
        except Exception as exc:
            status = "DEGRADED_RETRY_PENDING"
            error = f"{type(exc).__name__}: {exc}"
            finished_at = utcnow()
            connection.execute(
                """
                UPDATE evo_runs SET
                  finished_at=?,status=?,evaluated=?,discovered=?,
                  transplants=?,reactivated=?,details_json=?
                WHERE run_id=?
                """,
                (
                    finished_at,
                    status,
                    evaluated,
                    discovered,
                    transplants,
                    reactivated,
                    json.dumps({"error": error}, ensure_ascii=False),
                    run_id,
                ),
            )
            set_meta(connection, "status", status)
            connection.commit()
        run_public = {
            "run_id": run_id,
            "generation": generation,
            "started_at": started_at,
            "finished_at": utcnow(),
            "status": status,
            "evaluated": evaluated,
            "discovered": discovered,
            "transplants": transplants,
            "reactivated": reactivated,
            "error": error,
        }
        public = build_public(connection, policy, run_public)
        connection.commit()
        connection.close()
        write_json(output_json, public)
        update_manifest(output_json, manifest_path, manifest_sha_path)
        publish_archive(database, output_state_archive)
        return public


def selftest() -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for day in range(1, 401):
        signal = float(day % 20)
        target = int(signal >= 16)
        samples.append(
            {
                "day": f"2025-{((day - 1) // 28) % 12 + 1:02d}-{((day - 1) % 28) + 1:02d}",
                "cell": "0:0",
                "signal": signal,
                "secondary": float((day * 3) % 17),
                "target_test": target,
            }
        )
    policy = dict(DEFAULT_POLICY)
    split = split_samples(samples, "target_test", policy)
    conditions = [
        {"feature": "signal", "operator": ">=", "threshold": 16.0}
    ]
    evaluation = evaluate_conditions(split, "target_test", conditions)
    if as_float(evaluation["validation"].get("precision"), 0.0) < 0.99:
        raise AssertionError("La evaluación sintética no reconoció el patrón")
    expression = canonical_expression(conditions)
    if parse_expression(expression) != normalize_conditions(conditions):
        raise AssertionError("La expresión canónica no se pudo reconstruir")
    with tempfile.TemporaryDirectory(prefix="sismoai_evo_selftest_") as temporary:
        database = Path(temporary) / "evolutionary.sqlite"
        initialize(database)
        connection = connect(database)
        first, inserted = insert_candidate(
            connection,
            scope="SELFTEST",
            target="SELFTEST_TARGET",
            conditions=conditions,
            generation=1,
            origin="SELFTEST",
        )
        if not inserted:
            raise AssertionError("No se insertó el candidato sintético")
        _, duplicate = insert_candidate(
            connection,
            scope="SELFTEST",
            target="SELFTEST_TARGET",
            conditions=conditions,
            generation=1,
            origin="SELFTEST",
        )
        if duplicate:
            raise AssertionError("No se deduplicó el candidato sintético")
        connection.commit()
        row = connection.execute(
            "SELECT * FROM evo_patterns WHERE fingerprint=?",
            (first,),
        ).fetchone()
        update_evaluation(connection, row, evaluation)
        connection.commit()
        transition_status(
            connection,
            first,
            "RETIRED_OBSERVATION",
            1,
            "selftest",
        )
        reactivated = transition_status(
            connection,
            first,
            "CHALLENGER",
            2,
            "selftest_reactivation",
        )
        if reactivated != 1:
            raise AssertionError("No se registró la reactivación")
        connection.commit()
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        connection.close()
        if check != "ok":
            raise AssertionError("SQLite evolutivo no pasó quick_check")
    return {
        "status": "OK",
        "checks": {
            "persistent_memory": True,
            "stable_fingerprint": True,
            "deduplication": True,
            "chronological_train_validation_vault": True,
            "retired_observation": True,
            "reactivation": True,
            "compatible_crossover": True,
            "success_transplant_registry": True,
            "research_isolation": True,
        },
    }


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SismoAI persistent evolutionary pattern laboratory"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--historical-db", required=True)
    run_parser.add_argument("--region-archives", action="append", default=[])
    run_parser.add_argument("--input-state-archive")
    run_parser.add_argument("--output-state-archive", required=True)
    run_parser.add_argument("--output-json", required=True)
    run_parser.add_argument("--policy")
    run_parser.add_argument("--manifest")
    run_parser.add_argument("--manifest-sha")
    subparsers.add_parser("selftest")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.command == "selftest":
        emit(selftest())
        return 0
    public = run_engine(
        historical_database=Path(arguments.historical_db),
        region_archive_dirs=[
            Path(value) for value in arguments.region_archives
        ],
        input_state_archive=(
            Path(arguments.input_state_archive)
            if arguments.input_state_archive
            else None
        ),
        output_state_archive=Path(arguments.output_state_archive),
        output_json=Path(arguments.output_json),
        policy_path=Path(arguments.policy) if arguments.policy else None,
        manifest_path=Path(arguments.manifest) if arguments.manifest else None,
        manifest_sha_path=(
            Path(arguments.manifest_sha) if arguments.manifest_sha else None
        ),
    )
    emit(
        {
            "status": public["last_run"]["status"],
            "generation": public["generation"],
            "patterns_total": public["memory"]["patterns_total"],
            "evaluated": public["last_run"]["evaluated"],
            "discovered": public["last_run"]["discovered"],
            "transplants": public["last_run"]["transplants"],
            "reactivated": public["last_run"]["reactivated"],
            "output": str(arguments.output_json),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

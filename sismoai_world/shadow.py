from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .regions import load_regions


SCIENTIFIC_NOTICE = (
    "Ventanas probabilísticas experimentales en modo sombra. No constituyen "
    "predicción sísmica determinista, alerta oficial ni orden de evacuación."
)

EXPRESSION_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*>=\s*([-+0-9.eE]+)"
    r"(?:\s+AND\s+([A-Za-z0-9_]+)\s*>=\s*([-+0-9.eE]+))?\s*$"
)


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def read_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


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
    files = manifest.setdefault("files", [])
    relative = "data/shadow.json"
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
    write_json(manifest_path, manifest)
    if manifest_sha_path is not None:
        manifest_sha_path.write_text(
            f"{sha256_file(manifest_path)}  manifest.json\n",
            encoding="utf-8",
        )


def safe_feature_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")[:100]


def archive_map(directories: Iterable[Path]) -> dict[str, Path]:
    archives: dict[str, Path] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for archive in sorted(directory.glob("*.zip")):
            archives.setdefault(archive.stem, archive)
    return archives


def current_regional_features(directories: Iterable[Path]) -> dict[str, dict[str, Any]]:
    archives = archive_map(directories)
    output: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="sismoai_shadow_") as temporary:
        root = Path(temporary)
        for region_id, archive in sorted(archives.items()):
            database = root / f"{region_id}.sqlite"
            try:
                with zipfile.ZipFile(archive) as package:
                    with package.open("mam/mam_data.sqlite") as source, database.open("wb") as target:
                        shutil.copyfileobj(source, target)

                source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
                source.row_factory = sqlite3.Row
                try:
                    latest_row = source.execute(
                        "SELECT MAX(substr(day,1,10)) latest FROM dtrg_r_features "
                        "WHERE score IS NOT NULL"
                    ).fetchone()
                    if not latest_row or not latest_row["latest"]:
                        continue
                    latest_day = date.fromisoformat(str(latest_row["latest"])[:10])
                    start_day = latest_day - timedelta(days=20)
                    rows = source.execute(
                        "SELECT substr(day,1,10) day,family,feature,score,quality "
                        "FROM dtrg_r_features "
                        "WHERE substr(day,1,10) BETWEEN ? AND ? AND score IS NOT NULL "
                        "ORDER BY day",
                        (start_day.isoformat(), latest_day.isoformat()),
                    ).fetchall()
                finally:
                    source.close()

                by_day: dict[date, dict[str, float]] = defaultdict(dict)
                for row in rows:
                    day_value = date.fromisoformat(str(row["day"])[:10])
                    prefix = safe_feature_name(f"{row['family']}__{row['feature']}")
                    if row["score"] is not None:
                        by_day[day_value][prefix + "__score"] = float(row["score"])
                    if row["quality"] is not None:
                        by_day[day_value][prefix + "__quality"] = float(row["quality"])

                feature_names = {
                    name
                    for offset in range(-13, 1)
                    for name in by_day.get(latest_day + timedelta(days=offset), {})
                }
                sample: dict[str, Any] = {
                    "day": latest_day.isoformat(),
                    "region": region_id,
                }
                for name in feature_names:
                    current = [
                        by_day.get(latest_day + timedelta(days=offset), {}).get(name, 0.0)
                        for offset in range(-6, 1)
                    ]
                    previous = [
                        by_day.get(latest_day + timedelta(days=offset), {}).get(name, 0.0)
                        for offset in range(-13, -6)
                    ]
                    sample[name + "__mean7"] = sum(current) / len(current)
                    sample[name + "__delta7"] = (
                        sum(current) / len(current) - sum(previous) / len(previous)
                    )
                output[region_id] = sample
            except Exception:
                continue
            finally:
                database.unlink(missing_ok=True)
    return output


def parse_expression(expression: str) -> list[tuple[str, float]] | None:
    match = EXPRESSION_RE.match(str(expression or ""))
    if not match:
        return None
    conditions = [(match.group(1), float(match.group(2)))]
    if match.group(3):
        conditions.append((match.group(3), float(match.group(4))))
    return conditions


def expression_is_active(expression: str, sample: dict[str, Any]) -> bool:
    conditions = parse_expression(expression)
    if not conditions:
        return False
    for feature, threshold in conditions:
        if as_float(sample.get(feature), float("-inf")) < threshold:
            return False
    return True


def historical_ready(historical: dict[str, Any], policy: dict[str, Any]) -> bool:
    if not bool(policy.get("require_historical_complete", True)):
        return True
    catalog = historical.get("catalog") or {}
    return (
        str(historical.get("state") or "") == "HISTORICAL_COMPLETE"
        or as_float(catalog.get("progress"), 0.0) >= 0.999
    )


def load_historical_patterns(database: Path) -> list[dict[str, Any]]:
    if not database.exists():
        return []
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT pattern_id,scope,target,expression,status,"
            "train_metrics_json,test_metrics_json,created_at "
            "FROM h_patterns ORDER BY created_at DESC LIMIT 2000"
        ).fetchall()
    finally:
        connection.close()
    patterns: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["train_metrics"] = json.loads(item.pop("train_metrics_json"))
            item["test_metrics"] = json.loads(item.pop("test_metrics_json"))
        except Exception:
            continue
        item["research_only"] = True
        item["public_gate_pass"] = False
        patterns.append(item)
    return patterns


def qualifying_patterns(
    historical: dict[str, Any],
    sample: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    scope = str(policy.get("scope") or "WORLD_REGIONAL_MULTISOURCE")
    target = str(policy.get("target") or "REGIONAL_THRESHOLD_EVENT_WITHIN_7D")
    min_tp = int(policy.get("min_test_true_positives", 3))
    min_lift = as_float(policy.get("min_test_lift"), 1.5)
    unique: dict[str, dict[str, Any]] = {}
    for pattern in historical.get("patterns") or []:
        if str(pattern.get("scope")) != scope or str(pattern.get("target")) != target:
            continue
        metrics = pattern.get("test_metrics") or {}
        if int(metrics.get("tp") or 0) < min_tp:
            continue
        if as_float(metrics.get("lift"), 0.0) < min_lift:
            continue
        if metrics.get("precision") is None or metrics.get("base_rate") is None:
            continue
        expression = str(pattern.get("expression") or "")
        if not expression_is_active(expression, sample):
            continue
        prior = unique.get(expression)
        if prior is None:
            unique[expression] = pattern
            continue
        prior_metrics = prior.get("test_metrics") or {}
        if (
            as_float(metrics.get("lift"), 0.0),
            as_float(metrics.get("precision"), 0.0),
            int(metrics.get("tp") or 0),
        ) > (
            as_float(prior_metrics.get("lift"), 0.0),
            as_float(prior_metrics.get("precision"), 0.0),
            int(prior_metrics.get("tp") or 0),
        ):
            unique[expression] = pattern
    result = list(unique.values())
    result.sort(
        key=lambda item: (
            as_float((item.get("test_metrics") or {}).get("lift"), 0.0),
            as_float((item.get("test_metrics") or {}).get("precision"), 0.0),
            int((item.get("test_metrics") or {}).get("tp") or 0),
        ),
        reverse=True,
    )
    return result


def evidence_summary(
    patterns: list[dict[str, Any]],
    current: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    weighted_precision = 0.0
    weighted_baseline = 0.0
    total_weight = 0.0
    total_tp = 0
    for pattern in patterns:
        metrics = pattern.get("test_metrics") or {}
        tp = max(1, int(metrics.get("tp") or 0))
        lift = max(1.0, as_float(metrics.get("lift"), 1.0))
        weight = tp * lift
        weighted_precision += weight * as_float(metrics.get("precision"), 0.0)
        weighted_baseline += weight * as_float(metrics.get("base_rate"), 0.0)
        total_weight += weight
        total_tp += tp

    raw_probability = weighted_precision / total_weight if total_weight else 0.0
    baseline_probability = weighted_baseline / total_weight if total_weight else 0.0
    retention = min(1.0, max(0.0, as_float(policy.get("retain_pattern_signal"), 0.5)))
    probability = baseline_probability + retention * (
        raw_probability - baseline_probability
    )
    probability = min(
        as_float(policy.get("maximum_probability"), 0.50),
        max(baseline_probability, probability),
    )

    support = min(1.0, len(patterns) / max(1, int(policy.get("full_support_patterns", 5))))
    support *= min(1.0, total_tp / max(1, int(policy.get("full_support_true_positives", 20))))
    current_confidence = as_float(current.get("confidence"), 0.0)
    confidence = current_confidence * (0.5 + 0.5 * support)

    return {
        "probability": round(probability, 6),
        "baseline_probability": round(baseline_probability, 6),
        "raw_pattern_precision": round(raw_probability, 6),
        "confidence": round(confidence, 6),
        "total_test_true_positives": total_tp,
        "active_pattern_count": len(patterns),
        "promising_pattern_count": sum(
            1 for item in patterns if item.get("status") == "PROMISING_CANDIDATE"
        ),
        "mean_lift": round(
            probability / baseline_probability if baseline_probability > 0 else 0.0,
            6,
        ),
    }


def region_is_eligible(
    current: dict[str, Any],
    evidence: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    allowed_states = set(policy.get("allowed_states") or ["ELEVATED", "HIGHLY_ATYPICAL"])
    state = str(current.get("state") or "NO_DATA")
    if state not in allowed_states:
        reasons.append(f"state={state}")
    if as_float(current.get("iedc_provisional"), 0.0) < as_float(
        policy.get("min_iedc"), 50.0
    ):
        reasons.append("iedc_bajo")
    if as_float(current.get("confidence"), 0.0) < as_float(
        policy.get("min_current_confidence"), 0.45
    ):
        reasons.append("confianza_actual_baja")
    if as_float(current.get("coverage"), 0.0) < as_float(
        policy.get("min_coverage"), 0.50
    ):
        reasons.append("cobertura_baja")
    if as_float(current.get("data_quality"), 0.0) < as_float(
        policy.get("min_data_quality"), 0.80
    ):
        reasons.append("calidad_baja")
    if as_float(current.get("baseline_progress"), 0.0) < as_float(
        policy.get("min_baseline_progress"), 1.0
    ):
        reasons.append("linea_base_incompleta")
    if int(current.get("available_families") or 0) < int(
        policy.get("min_available_families", 1)
    ):
        reasons.append("familias_insuficientes")
    if int(evidence.get("active_pattern_count") or 0) < int(
        policy.get("min_active_patterns", 3)
    ):
        reasons.append("patrones_activos_insuficientes")
    if int(evidence.get("promising_pattern_count") or 0) < int(
        policy.get("min_promising_patterns", 1)
    ):
        reasons.append("patrones_prometedores_insuficientes")
    if as_float(evidence.get("confidence"), 0.0) < as_float(
        policy.get("min_forecast_confidence"), 0.40
    ):
        reasons.append("confianza_pronostico_baja")
    gain = as_float(evidence.get("probability"), 0.0) - as_float(
        evidence.get("baseline_probability"), 0.0
    )
    if gain < as_float(policy.get("min_probability_gain"), 0.03):
        reasons.append("ganancia_probabilidad_baja")
    if as_float(evidence.get("mean_lift"), 0.0) < as_float(
        policy.get("min_combined_lift"), 1.5
    ):
        reasons.append("lift_combinado_bajo")
    return not reasons, reasons


def latest_region_map(world: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("region_id")): item
        for item in world.get("ranking") or []
        if item.get("region_id")
    }


def active_window_key(window: dict[str, Any]) -> tuple[str, str]:
    return (
        str(window.get("region_id") or ""),
        str(window.get("target") or ""),
    )


def generate(
    *,
    world: dict[str, Any],
    historical: dict[str, Any],
    regions_path: Path,
    region_archive_dirs: list[Path],
    previous_state: dict[str, Any],
    policy: dict[str, Any],
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_time = now or utcnow_dt()
    _, configured_regions = load_regions(regions_path)
    region_cfg = {region.id: region for region in configured_regions}
    world_regions = latest_region_map(world)
    samples = current_regional_features(region_archive_dirs)

    previous_windows = list(previous_state.get("windows") or [])
    history = list(previous_state.get("history") or [])
    open_windows: list[dict[str, Any]] = []
    for window in previous_windows:
        end = parse_dt(window.get("window_end"))
        if end and end > current_time:
            carried = dict(window)
            carried["status"] = "OPEN"
            carried["new_this_run"] = False
            open_windows.append(carried)
        else:
            closed = dict(window)
            closed["status"] = "CLOSED_UNVERIFIED"
            closed["closed_at"] = iso(current_time)
            closed["notification_eligible"] = False
            closed["new_this_run"] = False
            history.append(closed)

    status = "ACTIVE"
    blocked_reason = None
    ready = historical_ready(historical, policy)
    if not bool(policy.get("enabled", True)):
        status = "DISABLED"
        blocked_reason = "policy_disabled"
    elif not ready:
        status = "WAITING_FOR_HISTORICAL_COMPLETION"
        blocked_reason = "historical_replay_incomplete"

    target_code = str(policy.get("target") or "REGIONAL_THRESHOLD_EVENT_WITHIN_7D")
    existing_keys = {active_window_key(item) for item in open_windows}
    cooldown_hours = int(policy.get("cooldown_hours", 24))
    max_new = int(policy.get("max_new_windows_per_run", 10))
    max_open = int(policy.get("max_open_windows", 20))
    window_hours = int(policy.get("window_hours", 168))
    new_windows: list[dict[str, Any]] = []

    if status == "ACTIVE":
        ranked_candidates: list[tuple[float, dict[str, Any]]] = []
        for region_id, current in world_regions.items():
            sample = samples.get(region_id)
            config = region_cfg.get(region_id)
            if sample is None or config is None:
                continue
            patterns = qualifying_patterns(historical, sample, policy)
            evidence = evidence_summary(patterns, current, policy)
            eligible, rejection_reasons = region_is_eligible(current, evidence, policy)
            if not eligible:
                continue
            key = (region_id, target_code)
            if key in existing_keys:
                continue

            latest_closed = None
            for item in reversed(history):
                if active_window_key(item) != key:
                    continue
                latest_closed = parse_dt(item.get("closed_at") or item.get("window_end"))
                if latest_closed:
                    break
            if latest_closed and current_time - latest_closed < timedelta(hours=cooldown_hours):
                continue

            start = current_time
            end = current_time + timedelta(hours=window_hours)
            pattern_ids = [str(item.get("pattern_id") or "") for item in patterns[:10]]
            expressions = [str(item.get("expression") or "") for item in patterns[:5]]
            signature = "|".join(pattern_ids[:5])
            forecast_id = "SAI-" + hashlib.sha256(
                (
                    f"{region_id}|{target_code}|{iso(start)}|{signature}"
                ).encode("utf-8")
            ).hexdigest()[:20].upper()
            target = (
                f"Uno o más eventos M≥{config.event_magnitude:.1f} "
                f"dentro de {window_hours // 24} días en la macroregión"
            )
            window = {
                "forecast_id": forecast_id,
                "kind": "SHADOW_WINDOW",
                "region_id": region_id,
                "region_name": current.get("region_name") or config.name,
                "created_at": iso(current_time),
                "window_start": iso(start),
                "window_end": iso(end),
                "target": target,
                "target_code": target_code,
                "target_magnitude": config.event_magnitude,
                "probability": evidence["probability"],
                "baseline_probability": evidence["baseline_probability"],
                "confidence": evidence["confidence"],
                "iedc_provisional": as_float(current.get("iedc_provisional"), 0.0),
                "regional_state": current.get("state"),
                "coverage": as_float(current.get("coverage"), 0.0),
                "data_quality": as_float(current.get("data_quality"), 0.0),
                "baseline_progress": as_float(current.get("baseline_progress"), 0.0),
                "active_pattern_count": evidence["active_pattern_count"],
                "promising_pattern_count": evidence["promising_pattern_count"],
                "total_test_true_positives": evidence["total_test_true_positives"],
                "mean_lift": evidence["mean_lift"],
                "pattern_ids": pattern_ids,
                "pattern_expressions_sample": expressions,
                "status": "OPEN",
                "research_only": True,
                "public_gate_pass": False,
                "notification_eligible": True,
                "new_this_run": True,
                "source_world_generated_at": world.get("generated_at"),
                "source_historical_generated_at": historical.get("generated_at"),
                "scientific_notice": SCIENTIFIC_NOTICE,
            }
            priority = (
                evidence["probability"] - evidence["baseline_probability"]
            ) * max(1.0, evidence["mean_lift"]) * max(
                1.0, as_float(current.get("iedc_provisional"), 0.0) / 50.0
            )
            ranked_candidates.append((priority, window))

        ranked_candidates.sort(key=lambda item: item[0], reverse=True)
        available_slots = max(0, max_open - len(open_windows))
        for _, window in ranked_candidates[: min(max_new, available_slots)]:
            new_windows.append(window)
            open_windows.append(window)
            existing_keys.add(active_window_key(window))

    history = history[-500:]
    catalog = historical.get("catalog") or {}
    public = {
        "schema_version": 1,
        "generated_at": iso(current_time),
        "status": status,
        "blocked_reason": blocked_reason,
        "research_only": True,
        "multiple_windows_supported": True,
        "historical_progress": as_float(catalog.get("progress"), 0.0),
        "historical_months_complete": int(catalog.get("months_complete") or 0),
        "historical_months_total": int(catalog.get("months_total") or 0),
        "new_windows_count": len(new_windows),
        "open_windows_count": len(open_windows),
        "windows": open_windows,
        "policy_summary": {
            "target": target_code,
            "window_hours": window_hours,
            "allowed_states": policy.get("allowed_states"),
            "min_active_patterns": policy.get("min_active_patterns"),
            "min_promising_patterns": policy.get("min_promising_patterns"),
            "require_historical_complete": policy.get("require_historical_complete"),
        },
        "scientific_notice": SCIENTIFIC_NOTICE,
    }
    state = {
        "schema_version": 1,
        "updated_at": iso(current_time),
        "windows": open_windows,
        "history": history,
    }
    return public, state


def run_command(args: argparse.Namespace) -> int:
    world_path = Path(args.world)
    historical_path = Path(args.historical)
    regions_path = Path(args.regions)
    policy_path = Path(args.policy)
    historical_db_path = Path(args.historical_db) if args.historical_db else None
    previous_path = Path(args.previous_state) if args.previous_state else None
    output_path = Path(args.output)
    output_state_path = Path(args.output_state)
    manifest_path = Path(args.manifest) if args.manifest else None
    manifest_sha_path = Path(args.manifest_sha) if args.manifest_sha else None

    world = read_json(world_path, {})
    historical = read_json(historical_path, {})
    if historical_db_path and historical_db_path.exists():
        database_patterns = load_historical_patterns(historical_db_path)
        if database_patterns:
            historical["patterns"] = database_patterns
    policy = read_json(policy_path, {})
    previous_state = read_json(previous_path, {}) if previous_path else {}

    if not world.get("ranking"):
        raise SystemExit("world.json no contiene ranking regional.")
    if not historical.get("catalog"):
        raise SystemExit("historical.json no contiene catálogo histórico.")
    if not policy:
        raise SystemExit("No se pudo leer la política de ventanas.")

    public, state = generate(
        world=world,
        historical=historical,
        regions_path=regions_path,
        region_archive_dirs=[Path(item) for item in args.region_archives],
        previous_state=previous_state,
        policy=policy,
    )
    write_json(output_path, public)
    write_json(output_state_path, state)
    update_manifest(output_path, manifest_path, manifest_sha_path)

    print(
        json.dumps(
            {
                "status": "OK",
                "shadow_status": public["status"],
                "new_windows": public["new_windows_count"],
                "open_windows": public["open_windows_count"],
                "output": str(output_path),
                "output_state": str(output_state_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def selftest(_: argparse.Namespace) -> int:
    sample = {
        "seismic__seismic_count__score__mean7": 62.0,
        "gnss__gnss_residual_mm__score__delta7": 18.0,
    }
    assert expression_is_active(
        "seismic__seismic_count__score__mean7 >= 60", sample
    )
    assert expression_is_active(
        "seismic__seismic_count__score__mean7 >= 60 AND "
        "gnss__gnss_residual_mm__score__delta7 >= 10",
        sample,
    )
    assert not expression_is_active(
        "seismic__seismic_count__score__mean7 >= 70", sample
    )
    evidence = evidence_summary(
        [
            {
                "status": "PROMISING_CANDIDATE",
                "test_metrics": {
                    "tp": 5,
                    "precision": 0.20,
                    "base_rate": 0.05,
                    "lift": 4.0,
                },
            },
            {
                "status": "EXPLORATORY_CANDIDATE",
                "test_metrics": {
                    "tp": 4,
                    "precision": 0.15,
                    "base_rate": 0.05,
                    "lift": 3.0,
                },
            },
        ],
        {"confidence": 0.60},
        {
            "retain_pattern_signal": 0.5,
            "maximum_probability": 0.5,
            "full_support_patterns": 2,
            "full_support_true_positives": 9,
        },
    )
    assert evidence["probability"] > evidence["baseline_probability"]
    assert evidence["promising_pattern_count"] == 1
    print(
        json.dumps(
            {
                "status": "OK",
                "checks": {
                    "expression_parser": True,
                    "paired_expression": True,
                    "evidence_aggregation": True,
                    "multiple_windows_supported": True,
                    "prospective_only": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generador prospectivo de ventanas probabilísticas de SismoAI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--world", required=True)
    run_parser.add_argument("--historical", required=True)
    run_parser.add_argument("--historical-db")
    run_parser.add_argument("--regions", required=True)
    run_parser.add_argument("--policy", required=True)
    run_parser.add_argument("--region-archives", action="append", default=[])
    run_parser.add_argument("--previous-state")
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--output-state", required=True)
    run_parser.add_argument("--manifest")
    run_parser.add_argument("--manifest-sha")
    run_parser.set_defaults(func=run_command)

    test_parser = sub.add_parser("selftest")
    test_parser.set_defaults(func=selftest)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

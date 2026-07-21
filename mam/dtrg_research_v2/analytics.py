from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import SCIENTIFIC_NOTICE
from .config import ResearchConfig
from .db import connect, rows
from .util import autocorr_lag1, clamp, daterange, finite, linear_slope, median, robust_z, safe_json, utcnow, z_to_score


def _energy(mag: float | None) -> float:
    return 0.0 if mag is None else 10.0 ** (1.5 * mag + 4.8)


def _b_value(mags: list[float], minimum: float) -> float | None:
    xs = [m for m in mags if m >= minimum]
    if len(xs) < 20:
        return None
    mean_mag = statistics.mean(xs)
    denom = mean_mag - (minimum - 0.05)
    return None if denom <= 0 else math.log10(math.e) / denom


def build_daily_raw(db: Path, start: date, end: date) -> dict[str, dict[str, Any]]:
    # Missing historical coverage must not be interpreted as a real zero. We only
    # initialize zero-event days inside the date interval confirmed by the latest
    # successful USGS ingestion; outside that interval the feature remains absent.
    out = {d.isoformat(): {} for d in daterange(start, end)}
    try:
        src = rows(db, "SELECT status,details_json FROM dtrg_r_sources WHERE source='USGS_FDSN'")
        if src and src[0].get("status") == "OK":
            details = json.loads(src[0].get("details_json") or "{}")
            covered_start = max(start, date.fromisoformat(str(details.get("start"))[:10]))
            covered_end = min(end, date.fromisoformat(str(details.get("end"))[:10]))
            if covered_start <= covered_end:
                for d in daterange(covered_start, covered_end):
                    out[d.isoformat()].update({
                        "seismic_count": 0.0,
                        "seismic_energy_log10": 0.0,
                        "seismic_max_mag": 0.0,
                    })
    except Exception:
        # Older databases/selftests may not yet contain coverage metadata. Event
        # days below remain usable; unknown days remain missing rather than zero.
        pass
    evs = rows(db, "SELECT event_time,magnitude,depth_km,latitude,longitude FROM dtrg_r_events WHERE substr(event_time,1,10) BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
    by_day = defaultdict(list)
    for e in evs:
        by_day[e["event_time"][:10]].append(e)
    for day, items in by_day.items():
        mags = [finite(x["magnitude"]) for x in items]
        mags = [x for x in mags if x is not None]
        depths = [finite(x["depth_km"]) for x in items]
        depths = [x for x in depths if x is not None]
        out.setdefault(day, {}).update({
            "seismic_count": len(items),
            "seismic_energy_log10": math.log10(1.0 + sum(_energy(m) for m in mags)),
            "seismic_max_mag": max(mags) if mags else None,
            "seismic_b_value": _b_value(mags, min(mags) if mags else 2.5),
            "seismic_depth_median": median(depths),
        })
    gnss = rows(db, "SELECT station,obs_date,east_m,north_m,up_m FROM dtrg_r_gnss_obs WHERE obs_date BETWEEN ? AND ? ORDER BY station,obs_date", (start.isoformat(), end.isoformat()))
    by_station = defaultdict(list)
    for r in gnss:
        by_station[r["station"]].append(r)
    gnss_daily = defaultdict(list)
    for station, items in by_station.items():
        if len(items) < 15:
            continue
        for idx, r in enumerate(items):
            if idx < 7:
                continue
            prev = items[max(0, idx - 30):idx]
            med_e = median([x["east_m"] for x in prev])
            med_n = median([x["north_m"] for x in prev])
            med_u = median([x["up_m"] for x in prev])
            if None in (med_e, med_n, med_u):
                continue
            disp_mm = 1000.0 * math.sqrt((r["east_m"] - med_e) ** 2 + (r["north_m"] - med_n) ** 2 + (r["up_m"] - med_u) ** 2)
            gnss_daily[r["obs_date"]].append(disp_mm)
    for day, vals in gnss_daily.items():
        out.setdefault(day, {})["gnss_residual_mm"] = median(vals)
        out[day]["gnss_station_count"] = len(vals)
    goes = rows(db, "SELECT day,flash_count,energy_sum,coverage FROM dtrg_r_goes_daily WHERE day BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
    for r in goes:
        out.setdefault(r["day"], {}).update({
            "goes_flash_count": float(r["flash_count"]),
            "goes_energy": finite(r["energy_sum"], 0.0),
            "goes_coverage": finite(r["coverage"], 0.0),
        })
    insar = rows(db, "SELECT obs_date,abs_displacement_mm,coherence,quality FROM dtrg_r_insar_obs WHERE obs_date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))
    by_insar = defaultdict(list)
    for r in insar:
        by_insar[r["obs_date"]].append(r)
    for day, items in by_insar.items():
        out.setdefault(day, {}).update({
            "insar_abs_displacement_mm": median([x["abs_displacement_mm"] for x in items]),
            "insar_coherence": median([x["coherence"] for x in items if x["coherence"] is not None]),
            "insar_quality": median([x["quality"] for x in items]),
        })
    return out


FEATURES = {
    "seismic": [
        ("seismic_count", "positive", "Incremento robusto de la tasa sísmica"),
        ("seismic_energy_log10", "positive", "Incremento de energía sísmica agregada"),
        ("seismic_max_mag", "positive", "Magnitud máxima atípica en la ventana"),
    ],
    "gnss": [
        ("gnss_residual_mm", "positive", "Desplazamiento GNSS residual respecto de la mediana móvil"),
    ],
    "insar": [
        ("insar_abs_displacement_mm", "positive", "Desplazamiento InSAR absoluto respecto de su línea base"),
    ],
    "goes_lightning_control": [
        ("goes_flash_count", "positive", "Actividad de rayos GLM por encima de su línea base atmosférica"),
        ("goes_energy", "positive", "Energía óptica GLM agregada por encima de su línea base"),
    ],
}


def _source_snapshot(db: Path) -> list[dict[str, Any]]:
    return rows(db, "SELECT * FROM dtrg_r_sources ORDER BY source")


def calculate_history(root: Path, db: Path, cfg: ResearchConfig, start: date, end: date) -> dict[str, Any]:
    raw_start = start - timedelta(days=cfg.baseline_days + cfg.score_window_days + 45)
    daily = build_daily_raw(db, raw_start, end)
    source_snapshot = _source_snapshot(db)
    source_map = {s["source"]: s for s in source_snapshot}
    calculated = 0
    last_result = None
    for d in daterange(start, end):
        day = d.isoformat()
        family_scores: dict[str, float] = {}
        reasons = []
        feature_rows = []
        available_weight = 0.0
        weighted_score = 0.0
        quality_weighted = 0.0
        quality_den = 0.0
        family_source_keys = {
            "seismic": ["USGS_FDSN"],
            "gnss": ["NGL_GNSS"],
            "insar": ["LOCAL_INSAR_PRODUCTS"],
            "goes_lightning_control": ["NOAA_GOES_GLM"],
        }
        for family, specs in FEATURES.items():
            required = family_source_keys[family]
            source_available = any(
                source_map.get(k, {}).get("status") == "OK" and int(source_map.get(k, {}).get("records") or 0) > 0
                for k in required
            )
            if not source_available:
                continue
            scores = []
            for feature, direction, reason in specs:
                current_vals = [daily.get((d - timedelta(days=i)).isoformat(), {}).get(feature) for i in range(cfg.score_window_days)]
                current_vals = [finite(x) for x in current_vals if finite(x) is not None]
                current = median(current_vals)
                baseline_end = d - timedelta(days=cfg.score_window_days + 1)
                baseline_start = baseline_end - timedelta(days=cfg.baseline_days - 1)
                baseline = [daily.get(x.isoformat(), {}).get(feature) for x in daterange(baseline_start, baseline_end)]
                baseline = [finite(x) for x in baseline if finite(x) is not None]
                z = robust_z(current, baseline)
                score = z_to_score(z, "positive" if direction == "positive" else "absolute")
                if score is not None:
                    scores.append(score)
                    if score >= 40:
                        reasons.append({"family": family, "feature": feature, "score": round(score, 2), "reason": reason, "z": round(z or 0, 3)})
                quality = min(1.0, len(baseline) / max(30, cfg.baseline_days)) if current is not None else 0.0
                feature_rows.append((day, family, feature, current, median(baseline), z, score, quality, reason,
                                     safe_json({"baseline_n": len(baseline), "window_n": len(current_vals)})))
            if scores:
                family_score = sum(scores) / len(scores)
                family_scores[family] = round(family_score, 4)
                w = cfg.family_weights.get(family, 0.0)
                available_weight += w
                weighted_score += w * family_score
                # Source quality uses mapped operational source quality where possible.
                source_keys = family_source_keys[family]
                qs = [finite(source_map.get(k, {}).get("quality"), 0.0) or 0.0 for k in source_keys]
                q = sum(qs) / len(qs) if qs else 0.0
                quality_weighted += w * q
                quality_den += w
        coverage = clamp(available_weight / max(1e-9, sum(cfg.family_weights.values())))
        data_quality = clamp(quality_weighted / max(1e-9, quality_den))
        baseline_available_days = sum(1 for x in daterange(d - timedelta(days=cfg.baseline_days), d - timedelta(days=1))
                                      if daily.get(x.isoformat(), {}).get("seismic_count") is not None)
        baseline_progress = clamp(baseline_available_days / max(1, cfg.baseline_days))
        iedc = round(weighted_score / available_weight, 3) if available_weight > 0 else None
        available_families = len(family_scores)
        confidence = clamp(0.45 * coverage + 0.30 * data_quality + 0.25 * baseline_progress)
        if available_families <= 1:
            confidence = min(confidence, 0.45)
        state = "NO_DATA" if iedc is None else "NORMAL" if iedc < 25 else "WATCH" if iedc < 50 else "ELEVATED" if iedc < 75 else "HIGHLY_ATYPICAL"
        backtest = None
        with connect(db) as c:
            backtest = c.execute("SELECT * FROM dtrg_r_backtests ORDER BY created_at DESC LIMIT 1").fetchone()
        backtest_ok = bool(backtest and backtest["public_gate_pass"])
        public_valid = bool(
            iedc is not None and confidence >= cfg.public_min_confidence and
            available_families >= cfg.public_min_families and baseline_progress >= 1.0 and backtest_ok
        )
        public_value = iedc if public_valid else None
        reasons.sort(key=lambda x: x["score"], reverse=True)
        reasons = reasons[:12]
        with connect(db) as c:
            c.executemany("""
            INSERT INTO dtrg_r_features(day,family,feature,value,baseline_median,robust_z,score,quality,reason,details_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(day,family,feature) DO UPDATE SET value=excluded.value,baseline_median=excluded.baseline_median,
              robust_z=excluded.robust_z,score=excluded.score,quality=excluded.quality,reason=excluded.reason,
              details_json=excluded.details_json
            """, feature_rows)
            c.execute("""
            INSERT INTO dtrg_r_scores(calculated_at,day,region_id,iedc_raw,iedc_provisional,iedc_public,public_valid,state,
              confidence,coverage,data_quality,baseline_progress,available_families,reasons_json,family_scores_json,
              source_status_json,scientific_notice)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(day) DO UPDATE SET calculated_at=excluded.calculated_at,iedc_raw=excluded.iedc_raw,
              iedc_provisional=excluded.iedc_provisional,iedc_public=excluded.iedc_public,
              public_valid=excluded.public_valid,state=excluded.state,confidence=excluded.confidence,
              coverage=excluded.coverage,data_quality=excluded.data_quality,baseline_progress=excluded.baseline_progress,
              available_families=excluded.available_families,reasons_json=excluded.reasons_json,
              family_scores_json=excluded.family_scores_json,source_status_json=excluded.source_status_json,
              scientific_notice=excluded.scientific_notice
            """, (utcnow(), day, cfg.region_id, iedc, iedc, public_value, int(public_valid), state,
                  confidence, coverage, data_quality, baseline_progress, available_families,
                  safe_json(reasons), safe_json(family_scores), safe_json(source_snapshot), SCIENTIFIC_NOTICE))
        calculated += 1
        last_result = {
            "day": day, "region_id": cfg.region_id, "iedc_raw": iedc, "iedc_provisional": iedc,
            "iedc_public": public_value, "public_valid": public_valid, "state": state,
            "confidence": round(confidence, 4), "coverage": round(coverage, 4),
            "data_quality": round(data_quality, 4), "baseline_progress": round(baseline_progress, 4),
            "available_families": available_families, "family_scores": family_scores,
            "reasons": reasons, "scientific_notice": SCIENTIFIC_NOTICE,
        }
    return {"status": "OK", "days_calculated": calculated, "current": last_result}


def current(db: Path) -> dict[str, Any]:
    with connect(db) as c:
        r = c.execute("SELECT * FROM dtrg_r_scores ORDER BY day DESC LIMIT 1").fetchone()
        if not r:
            return {"status": "NO_DATA", "iedc_provisional": None, "public_valid": False, "scientific_notice": SCIENTIFIC_NOTICE}
        x = dict(r)
        for k in ("reasons_json", "family_scores_json", "source_status_json"):
            try:
                x[k[:-5]] = json.loads(x.pop(k))
            except Exception:
                x[k[:-5]] = [] if k != "family_scores_json" else {}
        x["public_valid"] = bool(x["public_valid"])
        return x

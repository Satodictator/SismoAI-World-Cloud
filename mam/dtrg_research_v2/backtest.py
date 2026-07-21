from __future__ import annotations

import math
import uuid
from datetime import date, timedelta
from pathlib import Path

from .config import ResearchConfig
from .db import connect
from .util import clamp, safe_json, utcnow


def _auc(labels, scores):
    pos = [(s, i) for i, (s, y) in enumerate(zip(scores, labels)) if y == 1]
    neg = [(s, i) for i, (s, y) in enumerate(zip(scores, labels)) if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for ps, _ in pos:
        for ns, _ in neg:
            wins += 1.0 if ps > ns else 0.5 if ps == ns else 0.0
    return wins / (len(pos) * len(neg))


def run_backtest(db: Path, cfg: ResearchConfig, start: date, end: date, threshold: float = 50.0):
    with connect(db) as c:
        scores = c.execute("SELECT day,iedc_provisional FROM dtrg_r_scores WHERE day BETWEEN ? AND ? AND iedc_provisional IS NOT NULL ORDER BY day", (start.isoformat(), end.isoformat())).fetchall()
        events = c.execute("SELECT substr(event_time,1,10) day,magnitude FROM dtrg_r_events WHERE magnitude>=? AND substr(event_time,1,10) BETWEEN ? AND ?", (cfg.backtest_event_magnitude, start.isoformat(), (end + timedelta(days=cfg.backtest_horizon_days)).isoformat())).fetchall()
    event_days = {date.fromisoformat(r["day"]) for r in events}
    labels, probs, decisions = [], [], []
    for r in scores:
        d = date.fromisoformat(r["day"])
        label = int(any(d + timedelta(days=i) in event_days for i in range(1, cfg.backtest_horizon_days + 1)))
        score = float(r["iedc_provisional"])
        labels.append(label)
        probs.append(clamp(score / 100.0))
        decisions.append(int(score >= threshold))
    tp = sum(1 for y, p in zip(labels, decisions) if y == 1 and p == 1)
    fp = sum(1 for y, p in zip(labels, decisions) if y == 0 and p == 1)
    tn = sum(1 for y, p in zip(labels, decisions) if y == 0 and p == 0)
    fn = sum(1 for y, p in zip(labels, decisions) if y == 1 and p == 0)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    auc = _auc(labels, probs)
    brier = sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(labels) if labels else None
    base_rate = sum(labels) / len(labels) if labels else 0.0
    base_brier = sum((base_rate - y) ** 2 for y in labels) / len(labels) if labels else None
    false_rate = 100.0 * fp / max(1, len(labels))
    gate = bool(
        sum(labels) >= cfg.public_min_backtest_positives and auc is not None and auc >= cfg.public_min_auc and
        brier is not None and base_brier is not None and brier <= base_brier * cfg.public_max_brier_ratio
    )
    metrics = {
        "samples": len(labels), "positives": sum(labels), "negatives": len(labels) - sum(labels),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall,
        "specificity": specificity, "f1": f1, "auc": auc, "brier": brier,
        "base_brier": base_brier, "false_alarms_per_100_days": false_rate,
        "public_gate_pass": gate,
        "interpretation": "Backtest exploratorio; no demuestra capacidad predictiva fuera de muestra.",
    }
    rid = str(uuid.uuid4())
    with connect(db) as c:
        c.execute("""
        INSERT INTO dtrg_r_backtests(run_id,created_at,start_day,end_day,horizon_days,event_magnitude,threshold,
          positives,negatives,tp,fp,tn,fn,precision,recall,specificity,f1,auc,brier,base_brier,
          false_alarms_per_100_days,public_gate_pass,metrics_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rid, utcnow(), start.isoformat(), end.isoformat(), cfg.backtest_horizon_days,
              cfg.backtest_event_magnitude, threshold, metrics["positives"], metrics["negatives"], tp, fp, tn, fn,
              precision, recall, specificity, f1, auc, brier, base_brier, false_rate, int(gate), safe_json(metrics)))
    metrics["run_id"] = rid
    return metrics

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

UTC = timezone.utc


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 10_000_000_000:
            v /= 1000.0
        return datetime.fromtimestamp(v, UTC)
    s = str(value).strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)


def day_string(value: Any) -> str:
    return parse_dt(value).date().isoformat()


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def finite(v: Any, default: float | None = None) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def median(values: Iterable[float]) -> float | None:
    xs = [float(x) for x in values if finite(x) is not None]
    return statistics.median(xs) if xs else None


def mad(values: Sequence[float], center: float | None = None) -> float | None:
    xs = [float(x) for x in values if finite(x) is not None]
    if not xs:
        return None
    c = statistics.median(xs) if center is None else center
    return statistics.median(abs(x - c) for x in xs)


def robust_z(value: float | None, baseline: Sequence[float]) -> float | None:
    if value is None:
        return None
    xs = [float(x) for x in baseline if finite(x) is not None]
    if len(xs) < 8:
        return None
    c = statistics.median(xs)
    m = mad(xs, c)
    if not m or m < 1e-12:
        sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        return 0.0 if sd < 1e-12 else (float(value) - c) / sd
    return 0.6744897501960817 * (float(value) - c) / m


def z_to_score(z: float | None, direction: str = "absolute") -> float | None:
    if z is None:
        return None
    x = abs(z) if direction == "absolute" else max(0.0, z)
    return round(100.0 * (1.0 - math.exp(-x / 2.2)), 4)


def linear_slope(values: Sequence[float]) -> float | None:
    ys = [finite(v) for v in values]
    pairs = [(i, y) for i, y in enumerate(ys) if y is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    sx = sum(x for x, _ in pairs)
    sy = sum(y for _, y in pairs)
    sxx = sum(x * x for x, _ in pairs)
    sxy = sum(x * y for x, y in pairs)
    den = n * sxx - sx * sx
    return None if not den else (n * sxy - sx * sy) / den


def autocorr_lag1(values: Sequence[float]) -> float | None:
    xs = [finite(v) for v in values]
    pairs = [(a, b) for a, b in zip(xs[:-1], xs[1:]) if a is not None and b is not None]
    if len(pairs) < 5:
        return None
    a = [x for x, _ in pairs]
    b = [y for _, y in pairs]
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in pairs)
    da = sum((x - ma) ** 2 for x in a)
    db = sum((y - mb) ** 2 for y in b)
    return None if da <= 0 or db <= 0 else num / math.sqrt(da * db)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


class HttpClient:
    def __init__(self, cache_dir: Path, timeout: int = 45, user_agent: str = "SismoAI-DTRG-Research/2.0"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

    def _paths(self, url: str, params: dict[str, Any] | None):
        key = sha256_bytes((url + "?" + safe_json(params or {})).encode("utf-8"))
        return self.cache_dir / f"{key}.bin", self.cache_dir / f"{key}.json"

    def get(self, url: str, *, params: dict[str, Any] | None = None, max_age_seconds: int = 0,
            headers: dict[str, str] | None = None, attempts: int = 4) -> requests.Response:
        data_path, meta_path = self._paths(url, params)
        if max_age_seconds > 0 and data_path.exists() and meta_path.exists():
            age = time.time() - data_path.stat().st_mtime
            if age <= max_age_seconds:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                response = requests.Response()
                response.status_code = int(meta.get("status_code", 200))
                response.url = meta.get("url", url)
                response.headers.update(meta.get("headers", {}))
                response._content = data_path.read_bytes()
                return response
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    retry = r.headers.get("Retry-After")
                    delay = float(retry) if retry and retry.isdigit() else min(30.0, (2 ** attempt) + random.random())
                    if attempt + 1 < attempts:
                        time.sleep(delay)
                        continue
                r.raise_for_status()
                data_path.write_bytes(r.content)
                meta_path.write_text(safe_json({
                    "status_code": r.status_code,
                    "url": r.url,
                    "headers": {"Content-Type": r.headers.get("Content-Type", "")},
                    "saved_at": utcnow(),
                }), encoding="utf-8")
                return r
            except Exception as exc:  # network sources must not corrupt the run
                last = exc
                if attempt + 1 < attempts:
                    time.sleep(min(30.0, (2 ** attempt) + random.random()))
        raise RuntimeError(f"HTTP_FAILED {url}: {last}")

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ResearchConfig
from .db import connect, upsert_source
from .util import HttpClient, day_string, finite, parse_dt, safe_json, sha256_bytes, utcnow

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NGL_COORD_URLS = [
    "https://geodesy.unr.edu/NGLStationPages/llh.out",
    "https://geodesy.unr.edu/NGLStationPages/DataHoldings.txt",
]
NGL_STATION_PAGE = "https://geodesy.unr.edu/NGLStationPages/stations/{station}.sta"
ASF_NOTICE = "ASF catalog search; displacement analysis requires downloaded/local InSAR products."


def _chunks(start: date, end: date, days: int = 90):
    d = start
    while d <= end:
        e = min(end, d + timedelta(days=days - 1))
        yield d, e
        d = e + timedelta(days=1)


def ingest_usgs(root: Path, db: Path, cfg: ResearchConfig, start: date, end: date) -> dict[str, Any]:
    client = HttpClient(root / "cache" / "dtrg_research" / "http" / "usgs")
    total = 0
    newest = None
    try:
        for a, b in _chunks(start, end, 90):
            params = {
                "format": "geojson", "starttime": a.isoformat(),
                "endtime": (b + timedelta(days=1)).isoformat(),
                "minlatitude": cfg.min_lat, "maxlatitude": cfg.max_lat,
                "minlongitude": cfg.min_lon, "maxlongitude": cfg.max_lon,
                "minmagnitude": cfg.min_magnitude, "orderby": "time-asc", "limit": 20000,
            }
            payload = client.get(USGS_URL, params=params, max_age_seconds=1800).json()
            feats = payload.get("features", [])
            with connect(db) as c:
                for f in feats:
                    p = f.get("properties") or {}
                    g = f.get("geometry") or {}
                    co = g.get("coordinates") or [None, None, None]
                    if co[0] is None or co[1] is None:
                        continue
                    t = parse_dt(p.get("time")).isoformat().replace("+00:00", "Z")
                    newest = max(newest, t) if newest else t
                    c.execute("""
                    INSERT INTO dtrg_r_events(event_id,event_time,updated_at,latitude,longitude,depth_km,magnitude,mag_type,place,url,felt,significance,raw_json,ingested_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id) DO UPDATE SET event_time=excluded.event_time,updated_at=excluded.updated_at,
                      latitude=excluded.latitude,longitude=excluded.longitude,depth_km=excluded.depth_km,
                      magnitude=excluded.magnitude,mag_type=excluded.mag_type,place=excluded.place,url=excluded.url,
                      felt=excluded.felt,significance=excluded.significance,raw_json=excluded.raw_json,ingested_at=excluded.ingested_at
                    """, (f.get("id") or sha256_bytes(safe_json(f).encode()), t,
                          parse_dt(p.get("updated")).isoformat().replace("+00:00", "Z") if p.get("updated") else None,
                          float(co[1]), float(co[0]), finite(co[2]), finite(p.get("mag")), p.get("magType"),
                          p.get("place"), p.get("url"), p.get("felt"), p.get("sig"), safe_json(f), utcnow()))
            total += len(feats)
        freshness = None
        if newest:
            freshness = max(0.0, (datetime.now(timezone.utc) - parse_dt(newest)).total_seconds() / 3600.0)
        coverage = min(1.0, (end - start).days / max(1, cfg.baseline_days))
        upsert_source(db, "USGS_FDSN", "OK", records=total, coverage=coverage, quality=0.95,
                      freshness_hours=freshness, message="Catálogo sísmico real actualizado", success=True,
                      details={"start": start.isoformat(), "end": end.isoformat(), "newest": newest})
        return {"status": "OK", "records_received": total, "newest": newest}
    except Exception as exc:
        upsert_source(db, "USGS_FDSN", "ERROR", message=str(exc), details={"start": str(start), "end": str(end)})
        return {"status": "ERROR", "error": str(exc), "records_received": total}


def _find_station_coordinates(text: str, cfg: ResearchConfig) -> dict[str, tuple[float, float]]:
    found: dict[str, tuple[float, float]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "%", "<")):
            continue
        parts = re.split(r"\s+|,", line)
        if not parts:
            continue
        station = re.sub(r"[^A-Za-z0-9]", "", parts[0]).upper()
        if not (3 <= len(station) <= 9):
            continue
        nums = []
        for tok in parts[1:15]:
            try:
                nums.append(float(tok))
            except ValueError:
                pass
        candidates = []
        for i in range(len(nums) - 1):
            a, b = nums[i], nums[i + 1]
            for lat, lon in ((a, b), (b, a)):
                # Some geodetic holdings publish longitude in the 0..360 range.
                # Normalize it before applying the regional bounding box.
                lon = lon - 360.0 if lon > 180.0 else lon
                if cfg.min_lat <= lat <= cfg.max_lat and cfg.min_lon <= lon <= cfg.max_lon:
                    candidates.append((lat, lon))
        if candidates:
            found.setdefault(station, candidates[0])
    return found


def _discover_tenv3(client: HttpClient, station: str) -> str | None:
    page = NGL_STATION_PAGE.format(station=station)
    try:
        text = client.get(page, max_age_seconds=86400).text
        links = re.findall(r'''(?:href|HREF)=["']([^"']+\.tenv3)["']''', text)
        links = [x.replace("http://", "https://") for x in links]
        preferred = [x for x in links if "IGS20" in x and "/rapids" not in x]
        chosen = (preferred or links)
        if chosen:
            url = chosen[0]
            if url.startswith("/"):
                url = "https://geodesy.unr.edu" + url
            elif not url.startswith("http"):
                url = "https://geodesy.unr.edu/NGLStationPages/stations/" + url
            return url
    except Exception:
        pass
    direct = f"https://geodesy.unr.edu/gps_timeseries/IGS20/tenv3/IGS20/{station}.tenv3"
    try:
        client.get(direct, max_age_seconds=86400)
        return direct
    except Exception:
        return None


def _parse_tenv3(text: str, start: date) -> list[tuple[str, float, float, float, float | None, float | None, float | None]]:
    out = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", "%")):
            continue
        p = line.split()
        if len(p) < 13:
            continue
        try:
            ds = p[1]
            d = datetime.strptime(ds.title(), "%y%b%d").date()
            if d < start:
                continue
            east, north, up = float(p[8]), float(p[10]), float(p[12])
            se = float(p[14]) if len(p) > 14 else None
            sn = float(p[15]) if len(p) > 15 else None
            su = float(p[16]) if len(p) > 16 else None
            if all(math.isfinite(x) for x in (east, north, up)):
                out.append((d.isoformat(), east, north, up, se, sn, su))
        except Exception:
            continue
    return out


def ingest_gnss(root: Path, db: Path, cfg: ResearchConfig, start: date) -> dict[str, Any]:
    client = HttpClient(root / "cache" / "dtrg_research" / "http" / "gnss", timeout=60)
    coords = {}
    coord_source = None
    errors = []
    for url in NGL_COORD_URLS:
        try:
            text = client.get(url, max_age_seconds=86400).text
            coords = _find_station_coordinates(text, cfg)
            if coords:
                coord_source = url
                break
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if not coords:
        msg = "No se pudieron descubrir estaciones GNSS dentro de la región. " + " | ".join(errors)
        upsert_source(db, "NGL_GNSS", "ERROR", message=msg)
        return {"status": "ERROR", "error": msg, "stations": 0, "observations": 0}
    stations = sorted(coords.items())[: cfg.max_gnss_stations]
    obs_total = 0
    stations_ok = 0
    last_obs = None
    for station, (lat, lon) in stations:
        url = _discover_tenv3(client, station)
        status = "NO_TIMESERIES"
        count = 0
        station_last_obs = None
        details: dict[str, Any] = {}
        if url:
            try:
                observations = _parse_tenv3(client.get(url, max_age_seconds=21600).text, start)
                with connect(db) as c:
                    for d, e, n, u, se, sn, su in observations:
                        c.execute("""
                        INSERT INTO dtrg_r_gnss_obs(station,obs_date,east_m,north_m,up_m,sigma_e,sigma_n,sigma_u,source_url,ingested_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(station,obs_date) DO UPDATE SET east_m=excluded.east_m,north_m=excluded.north_m,
                          up_m=excluded.up_m,sigma_e=excluded.sigma_e,sigma_n=excluded.sigma_n,sigma_u=excluded.sigma_u,
                          source_url=excluded.source_url,ingested_at=excluded.ingested_at
                        """, (station, d, e, n, u, se, sn, su, url, utcnow()))
                count = len(observations)
                if count:
                    stations_ok += 1
                    obs_total += count
                    status = "OK"
                    station_last_obs = observations[-1][0]
                    last_obs = max(last_obs, station_last_obs) if last_obs else station_last_obs
            except Exception as exc:
                status = "ERROR"
                details["error"] = str(exc)
        with connect(db) as c:
            c.execute("""
            INSERT INTO dtrg_r_gnss_stations(station,latitude,longitude,source_url,discovered_at,last_observation,observation_count,status,details_json)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(station) DO UPDATE SET latitude=excluded.latitude,longitude=excluded.longitude,
              source_url=excluded.source_url,last_observation=excluded.last_observation,
              observation_count=excluded.observation_count,status=excluded.status,details_json=excluded.details_json
            """, (station, lat, lon, url or coord_source, utcnow(), station_last_obs if status == "OK" else None,
                  count, status, safe_json(details)))
    coverage = min(1.0, stations_ok / max(3, min(cfg.max_gnss_stations, len(stations))))
    quality = 0.88 * coverage
    freshness = None
    if last_obs:
        freshness = max(0.0, (date.today() - date.fromisoformat(last_obs)).days * 24.0)
    status = "OK" if stations_ok else "NO_DATA"
    upsert_source(db, "NGL_GNSS", status, records=obs_total, coverage=coverage, quality=quality,
                  freshness_hours=freshness, message=f"{stations_ok}/{len(stations)} estaciones con series",
                  success=stations_ok > 0, details={"coordinate_source": coord_source, "errors": errors})
    return {"status": status, "stations_discovered": len(stations), "stations_ok": stations_ok,
            "observations": obs_total, "last_observation": last_obs}


def _goes_satellite(t: datetime) -> str:
    # GOES-19 became operational GOES-East on 2025-04-04; older archive uses GOES-16.
    return "19" if t >= datetime(2025, 4, 4, 15, tzinfo=timezone.utc) else "16"


def _list_s3(client: HttpClient, satellite: str, prefix: str) -> list[dict[str, Any]]:
    url = f"https://noaa-goes{satellite}.s3.amazonaws.com/"
    token = None
    out = []
    for _ in range(20):
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(client.get(url, params=params, max_age_seconds=900).content)
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        for item in root.findall(f"{ns}Contents"):
            out.append({
                "key": item.findtext(f"{ns}Key"),
                "size": int(item.findtext(f"{ns}Size") or 0),
                "last_modified": item.findtext(f"{ns}LastModified"),
            })
        truncated = (root.findtext(f"{ns}IsTruncated") or "false").lower() == "true"
        token = root.findtext(f"{ns}NextContinuationToken")
        if not truncated or not token:
            break
    return out


def _glm_times_from_key(key: str):
    m1 = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    m2 = re.search(r"_e(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", key)
    def cv(m):
        if not m:
            return None
        return datetime.strptime("".join(m.groups()), "%Y%j%H%M%S").replace(tzinfo=timezone.utc)
    return cv(m1), cv(m2)


def _process_glm_bytes(data: bytes, cfg: ResearchConfig) -> tuple[int, float]:
    try:
        from netCDF4 import Dataset
        import numpy as np
    except Exception as exc:
        raise RuntimeError("netCDF4/numpy no disponibles para GOES GLM") from exc
    tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        with Dataset(tmp.name, "r") as ds:
            lat = np.asarray(ds.variables["flash_lat"][:], dtype=float)
            lon = np.asarray(ds.variables["flash_lon"][:], dtype=float)
            mask = ((lat >= cfg.min_lat) & (lat <= cfg.max_lat) &
                    (lon >= cfg.min_lon) & (lon <= cfg.max_lon))
            count = int(mask.sum())
            energy = 0.0
            if "flash_energy" in ds.variables and count:
                vals = np.asarray(ds.variables["flash_energy"][:], dtype=float)
                energy = float(np.nansum(vals[mask]))
            return count, energy
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def ingest_goes(root: Path, db: Path, cfg: ResearchConfig, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    client = HttpClient(root / "cache" / "dtrg_research" / "http" / "goes", timeout=75)
    selected = []
    cursor = start_dt.replace(minute=0, second=0, microsecond=0)
    errors = []
    while cursor <= end_dt:
        sat = _goes_satellite(cursor)
        prefix = f"GLM-L2-LCFA/{cursor.year}/{cursor.timetuple().tm_yday:03d}/{cursor.hour:02d}/"
        try:
            entries = [x for x in _list_s3(client, sat, prefix) if x.get("key", "").endswith(".nc")]
            entries.sort(key=lambda x: x["key"])
            last_bucket = -999
            for e in entries:
                st, en = _glm_times_from_key(e["key"])
                if not st or st < start_dt or st > end_dt:
                    continue
                bucket = int(st.timestamp() // (cfg.goes_sampling_minutes * 60))
                if bucket == last_bucket:
                    continue
                last_bucket = bucket
                e.update({"satellite": sat, "start": st, "end": en})
                selected.append(e)
        except Exception as exc:
            errors.append(f"{prefix}: {exc}")
        cursor += timedelta(hours=1)
    total_flashes = 0
    processed = 0
    for e in selected:
        key = e["key"]
        existing = None
        with connect(db) as c:
            existing = c.execute("SELECT status,region_flash_count,energy_sum FROM dtrg_r_goes_files WHERE object_key=?", (key,)).fetchone()
        if existing and existing["status"] == "OK":
            total_flashes += int(existing["region_flash_count"] or 0)
            processed += 1
            continue
        url = f"https://noaa-goes{e['satellite']}.s3.amazonaws.com/{key}"
        status, count, energy, err = "ERROR", None, None, None
        try:
            data = client.get(url, max_age_seconds=86400).content
            count, energy = _process_glm_bytes(data, cfg)
            status = "OK"
            total_flashes += count
            processed += 1
        except Exception as exc:
            err = str(exc)
            errors.append(f"{key}: {err}")
        with connect(db) as c:
            c.execute("""
            INSERT INTO dtrg_r_goes_files(object_key,satellite,start_time,end_time,size_bytes,status,flash_count,region_flash_count,energy_sum,source_url,processed_at,error)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(object_key) DO UPDATE SET status=excluded.status,region_flash_count=excluded.region_flash_count,
              energy_sum=excluded.energy_sum,processed_at=excluded.processed_at,error=excluded.error
            """, (key, e["satellite"], e["start"].isoformat(), e["end"].isoformat() if e["end"] else None,
                  e["size"], status, None, count, energy, url, utcnow(), err))
    with connect(db) as c:
        days = c.execute("""
        SELECT substr(start_time,1,10) day, COALESCE(SUM(region_flash_count),0) flashes,
               COALESCE(SUM(energy_sum),0) energy, COUNT(*) files
        FROM dtrg_r_goes_files WHERE status='OK' GROUP BY substr(start_time,1,10)
        """).fetchall()
        expected_per_day = max(1, int(24 * 60 / cfg.goes_sampling_minutes))
        for r in days:
            coverage = min(1.0, int(r["files"]) / expected_per_day)
            c.execute("""
            INSERT INTO dtrg_r_goes_daily(day,flash_count,energy_sum,file_count,coverage,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET flash_count=excluded.flash_count,
              energy_sum=excluded.energy_sum,file_count=excluded.file_count,coverage=excluded.coverage,updated_at=excluded.updated_at
            """, (r["day"], int(r["flashes"]), float(r["energy"]), int(r["files"]), coverage, utcnow()))
    coverage = processed / max(1, len(selected))
    status = "OK" if processed else ("NO_FILES" if not selected else "ERROR")
    upsert_source(db, "NOAA_GOES_GLM", status, records=processed, coverage=coverage,
                  quality=0.9 * coverage, freshness_hours=max(0.0, (datetime.now(timezone.utc) - end_dt).total_seconds() / 3600),
                  message=f"{processed}/{len(selected)} archivos GLM procesados; {total_flashes} flashes regionales",
                  success=processed > 0, details={"errors": errors[-20:], "sampling_minutes": cfg.goes_sampling_minutes})
    return {"status": status, "selected_files": len(selected), "processed_files": processed,
            "region_flashes": total_flashes, "errors": errors[-10:]}


def ingest_insar_catalog(root: Path, db: Path, cfg: ResearchConfig, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    try:
        import asf_search as asf
        wkt = (f"POLYGON(({cfg.min_lon} {cfg.min_lat},{cfg.max_lon} {cfg.min_lat},"
               f"{cfg.max_lon} {cfg.max_lat},{cfg.min_lon} {cfg.max_lat},{cfg.min_lon} {cfg.min_lat}))")
        result_sets = []
        # Standard Sentinel-1 acquisitions provide the updated observation list.
        result_sets.append(("SENTINEL1", asf.geo_search(
            platform=[asf.PLATFORM.SENTINEL1], intersectsWith=wkt,
            start=start_dt, end=end_dt, maxResults=500,
        )))
        # OPERA-S1 contains derived products. DISP-S1 NetCDF files require Earthdata authentication.
        try:
            opera_dataset = getattr(getattr(asf, "DATASET", object()), "OPERA_S1", "OPERA-S1")
            opera = asf.search(dataset=opera_dataset, intersectsWith=wkt,
                               start=start_dt, end=end_dt, maxResults=500)
            result_sets.append(("OPERA_S1", opera))
        except Exception as opera_exc:
            result_sets.append(("OPERA_S1_ERROR", []))
            opera_error = str(opera_exc)
        else:
            opera_error = None
        count = 0
        opera_count = 0
        downloadable = []
        with connect(db) as c:
            for dataset_name, results in result_sets:
                for item in results:
                    geo = item.geojson()
                    prop = geo.get("properties") or {}
                    scene_id = prop.get("sceneName") or prop.get("fileID") or prop.get("granuleName") or sha256_bytes(safe_json(geo).encode())
                    if dataset_name == "OPERA_S1":
                        opera_count += 1
                        urls = []
                        for u in [prop.get("url")] + list(prop.get("additionalUrls") or []):
                            if u and (str(u).lower().endswith(".nc") or "disp-s1" in str(u).lower()):
                                urls.append(str(u))
                        if urls:
                            downloadable.append((item, urls))
                    c.execute("""
                    INSERT INTO dtrg_r_insar_scenes(scene_id,start_time,stop_time,platform,beam_mode,flight_direction,path_number,frame_number,url,geometry_json,status,raw_json,ingested_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(scene_id) DO UPDATE SET start_time=excluded.start_time,stop_time=excluded.stop_time,
                      url=excluded.url,status=excluded.status,raw_json=excluded.raw_json,ingested_at=excluded.ingested_at
                    """, (scene_id, prop.get("startTime"), prop.get("stopTime"), prop.get("platform") or dataset_name,
                          prop.get("beamModeType"), prop.get("flightDirection"), prop.get("pathNumber"),
                          prop.get("frameNumber"), prop.get("url") or prop.get("browse"), safe_json(geo.get("geometry")),
                          "CATALOG_ONLY", safe_json(geo), utcnow()))
                    count += 1
        downloaded = 0
        download_errors = []
        token = (cfg.earthdata_token or os.environ.get("EARTHDATA_TOKEN", "")).strip()
        if token and downloadable and cfg.insar_auto_download_max > 0:
            dest = root / "data" / "insar" / "opera_disp"
            dest.mkdir(parents=True, exist_ok=True)
            session = asf.ASFSession().auth_with_token(token)
            for _item, urls in downloadable[: cfg.insar_auto_download_max]:
                for url in urls[:1]:
                    try:
                        asf.download_url(url=url, path=str(dest), session=session)
                        downloaded += 1
                    except Exception as exc:
                        download_errors.append(f"{url}: {exc}")
        details = {
            "notice": ASF_NOTICE,
            "opera_products": opera_count,
            "opera_error": opera_error,
            "disp_candidates": len(downloadable),
            "downloaded_with_earthdata_token": downloaded,
            "download_errors": download_errors[-10:],
        }
        upsert_source(db, "ASF_SENTINEL1_CATALOG", "OK", records=count, coverage=1.0 if count else 0.0,
                      quality=0.85, message=f"{count} escenas/productos SAR; {opera_count} OPERA-S1",
                      success=True, details=details)
        return {"status": "OK", "scenes": count, "opera_products": opera_count,
                "disp_candidates": len(downloadable), "downloaded": downloaded,
                "notice": ASF_NOTICE, "download_errors": download_errors[-10:]}
    except Exception as exc:
        upsert_source(db, "ASF_SENTINEL1_CATALOG", "ERROR", message=str(exc), details={"notice": ASF_NOTICE})
        return {"status": "ERROR", "error": str(exc), "scenes": 0, "notice": ASF_NOTICE}


def _date_from_name(name: str) -> str:
    m = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else date.today().isoformat()


def ingest_local_insar(root: Path, db: Path) -> dict[str, Any]:
    folder = root / "data" / "insar"
    folder.mkdir(parents=True, exist_ok=True)
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in (".csv", ".tif", ".tiff", ".nc")]
    done = 0
    errors = []
    for p in files:
        try:
            day = _date_from_name(p.name)
            displacement = None
            coherence = None
            valid_pixels = None
            quality = None
            details: dict[str, Any] = {"suffix": p.suffix.lower()}
            if p.suffix.lower() == ".csv":
                with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
                    recs = list(csv.DictReader(fh))
                vals, cohs = [], []
                for r in recs:
                    for key in ("displacement_mm", "displacement", "los_mm", "value"):
                        v = finite(r.get(key))
                        if v is not None:
                            vals.append(v)
                            break
                    v = finite(r.get("coherence"))
                    if v is not None:
                        cohs.append(v)
                    if r.get("date"):
                        day = str(r["date"])[:10]
                if vals:
                    vals.sort()
                    displacement = vals[len(vals) // 2]
                    valid_pixels = len(vals)
                if cohs:
                    cohs.sort()
                    coherence = cohs[len(cohs) // 2]
            elif p.suffix.lower() in (".tif", ".tiff"):
                import numpy as np
                import rasterio
                with rasterio.open(p) as src:
                    arr = src.read(1, masked=True).compressed().astype(float)
                    if arr.size:
                        # Heuristic: files with meter-like magnitudes are converted to mm.
                        med = float(np.nanmedian(arr))
                        displacement = med * 1000.0 if abs(med) < 10.0 else med
                        valid_pixels = int(arr.size)
                        quality = min(1.0, valid_pixels / 10000.0)
                        details.update({"crs": str(src.crs), "shape": [src.height, src.width]})
            else:
                import numpy as np
                from netCDF4 import Dataset
                with Dataset(str(p), "r") as ds:
                    candidates = [n for n, v in ds.variables.items() if getattr(v, "ndim", 0) >= 2 and n.lower() not in ("lat", "lon", "latitude", "longitude")]
                    preferred = [n for n in candidates if any(k in n.lower() for k in ("displacement", "velocity", "los"))]
                    candidates = preferred or candidates
                    if candidates:
                        arr = np.asarray(ds.variables[candidates[0]][:], dtype=float)
                        arr = arr[np.isfinite(arr)]
                        if arr.size:
                            med = float(np.nanmedian(arr))
                            displacement = med * 1000.0 if abs(med) < 10.0 else med
                            valid_pixels = int(arr.size)
                            details["variable"] = candidates[0]
            if displacement is None:
                raise ValueError("No se encontró una variable de desplazamiento utilizable")
            abs_disp = abs(displacement)
            if quality is None:
                quality = max(0.0, min(1.0, (coherence if coherence is not None else 0.6)))
            oid = sha256_bytes((str(p.resolve()) + str(p.stat().st_mtime_ns)).encode())
            with connect(db) as c:
                c.execute("""
                INSERT INTO dtrg_r_insar_obs(obs_id,obs_date,source_file,displacement_mm,abs_displacement_mm,coherence,valid_pixels,quality,details_json,ingested_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(obs_id) DO UPDATE SET displacement_mm=excluded.displacement_mm,
                  abs_displacement_mm=excluded.abs_displacement_mm,coherence=excluded.coherence,
                  valid_pixels=excluded.valid_pixels,quality=excluded.quality,details_json=excluded.details_json,
                  ingested_at=excluded.ingested_at
                """, (oid, day, str(p), displacement, abs_disp, coherence, valid_pixels, quality, safe_json(details), utcnow()))
            done += 1
        except Exception as exc:
            errors.append(f"{p.name}: {exc}")
    status = "OK" if done else "WAITING_FOR_PRODUCTS"
    coverage = min(1.0, done / 12.0)
    upsert_source(db, "LOCAL_INSAR_PRODUCTS", status, records=done, coverage=coverage,
                  quality=0.8 * coverage, message=f"{done}/{len(files)} productos locales analizados",
                  success=done > 0, details={"folder": str(folder), "errors": errors[-20:]})
    return {"status": status, "files_found": len(files), "processed": done, "errors": errors[-10:], "folder": str(folder)}

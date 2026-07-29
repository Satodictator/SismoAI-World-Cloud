from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCIENTIFIC_NOTICE, __version__
from .bulletin import build_bulletin
from .regions import load_regions
from .patterns_page import PATTERNS_HTML
from .sensors_page import SENSORS_HTML


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "INVALID_JSON", "error": str(exc), "region": {"id": path.stem, "name": path.stem}}


def _rank_key(item: dict[str, Any]):
    cur = item.get("current") or {}
    val = cur.get("iedc_provisional")
    return (-1 if val is None else float(val), float(cur.get("confidence") or 0), float(cur.get("coverage") or 0))


def build_world(*, regions_path: Path, collected_results_dir: Path, docs_dir: Path,
                state_results_dir: Path | None = None,
                historical_summary_path: Path | None = None,
                previous_world_dir: Path | None = None,
                previous_bulletins_dir: Path | None = None,
                mode: str = "unknown") -> dict[str, Any]:
    meta, configured = load_regions(regions_path)
    docs_dir = Path(docs_dir)
    data_dir = docs_dir / "data"
    region_docs = data_dir / "regions"
    shutil.rmtree(docs_dir, ignore_errors=True)
    region_docs.mkdir(parents=True, exist_ok=True)

    result_sources = [Path(collected_results_dir)]
    if state_results_dir:
        result_sources.append(Path(state_results_dir))

    records: list[dict[str, Any]] = []
    manifests: list[dict[str, str]] = []
    for region in configured:
        src = None
        for base in result_sources:
            candidate = base / f"{region.id}.json"
            if candidate.exists():
                src = candidate
                break
        if src is None:
            payload = {
                "status": "NOT_RUN",
                "generated_at": None,
                "region": {"id": region.id, "name": region.name, "group": region.group, "bbox": region.bbox},
                "current": {"iedc_provisional": None, "state": "NO_DATA", "public_valid": False},
                "sources": [], "counts": {}, "latest_backtest": [], "errors": [],
                "scientific_notice": SCIENTIFIC_NOTICE,
            }
        else:
            payload = _read_json(src)
        target = region_docs / f"{region.id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        manifests.append({"path": f"data/regions/{region.id}.json", "sha256": _sha256(target)})
        cur = payload.get("current") or {}
        sources = payload.get("sources") or []
        latest_event = (payload.get("latest_events") or [{}])[0]
        records.append({
            "region_id": region.id,
            "region_name": region.name,
            "group": region.group,
            "bbox": region.bbox,
            "status": payload.get("status", "UNKNOWN"),
            "generated_at": payload.get("generated_at"),
            "iedc_provisional": cur.get("iedc_provisional"),
            "iedc_public": cur.get("iedc_public"),
            "public_valid": bool(cur.get("public_valid")),
            "state": cur.get("state", "NO_DATA"),
            "confidence": cur.get("confidence", 0),
            "coverage": cur.get("coverage", 0),
            "data_quality": cur.get("data_quality", 0),
            "baseline_progress": cur.get("baseline_progress", 0),
            "available_families": cur.get("available_families", 0),
            "family_scores": cur.get("family_scores", {}),
            "reasons": cur.get("reasons", [])[:5],
            "signal": cur.get("signal", {}),
            "source_summary": [{
                "source": s.get("source"), "status": s.get("status"), "records": s.get("records"),
                "coverage": s.get("coverage"), "quality": s.get("quality"), "last_success": s.get("last_success"),
            } for s in sources],
            "latest_event": latest_event,
            "errors_count": len(payload.get("errors") or []),
        })

    ranking = sorted(records, key=_rank_key, reverse=True)
    for index, item in enumerate(ranking, 1):
        item["rank"] = index
    operational = sum(1 for x in records if x["iedc_provisional"] is not None)
    healthy = sum(1 for x in records if x["status"] in {"OK", "DEGRADED"})
    public_valid = sum(1 for x in records if x["public_valid"])
    world = {
        "schema_version": 1,
        "model_version": meta.get("model_version", f"SismoAI-World-Cloud-{__version__}"),
        "generated_at": utcnow(),
        "operation_mode": mode,
        "regions_configured": len(records),
        "regions_operational": operational,
        "regions_healthy_or_degraded": healthy,
        "regions_public_valid": public_valid,
        "scientific_notice": SCIENTIFIC_NOTICE,
        "ranking": ranking,
    }
    world_path = data_dir / "world.json"
    world_path.write_text(json.dumps(world, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    manifests.append({"path": "data/world.json", "sha256": _sha256(world_path)})
    historical_path = data_dir / "historical.json"
    if historical_summary_path and Path(historical_summary_path).exists():
        shutil.copy2(Path(historical_summary_path), historical_path)
    else:
        historical_path.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": world["generated_at"],
            "state": "WAITING_FOR_FIRST_RUN",
            "run_status": "NOT_AVAILABLE",
            "catalog": {
                "target_start": "1973-01-01", "cursor": "1973-01-01",
                "events": 0, "months_complete": 0, "months_total": 1, "progress": 0,
            },
            "sources": [], "context_controls": [], "patterns": [],
            "pattern_policy": {
                "research_only": True, "modifies_iedc": False, "activates_alerts": False,
            },
            "scientific_notice": (
                "Laboratorio histórico todavía no inicializado; no modifica el IEDC."
            ),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    manifests.append({"path": "data/historical.json", "sha256": _sha256(historical_path)})
    historical_payload = _read_json(historical_path)
    bulletin = build_bulletin(
        world=world,
        historical=historical_payload,
        previous_world_dir=Path(previous_world_dir) if previous_world_dir else None,
    )
    bulletin_path = data_dir / "bulletin.json"
    bulletin_path.write_text(
        json.dumps(bulletin, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    manifests.append({"path": "data/bulletin.json", "sha256": _sha256(bulletin_path)})

    bulletin_docs = data_dir / "bulletins"
    bulletin_docs.mkdir(parents=True, exist_ok=True)
    if previous_bulletins_dir and Path(previous_bulletins_dir).exists():
        previous_paths = sorted(Path(previous_bulletins_dir).glob("*.json"))[-119:]
        for source in previous_paths:
            if _read_json(source).get("generated_at"):
                shutil.copy2(source, bulletin_docs / source.name)
    bulletin_name = world["generated_at"].replace(":", "-") + ".json"
    current_archive = bulletin_docs / bulletin_name
    shutil.copy2(bulletin_path, current_archive)

    archive_entries = []
    for path in sorted(bulletin_docs.glob("*.json"), reverse=True):
        item = _read_json(path)
        if not item.get("generated_at"):
            continue
        archive_entries.append({
            "generated_at": item.get("generated_at"),
            "classification": item.get("classification"),
            "public_gate_approved_regions": item.get("public_gate_approved_regions", 0),
            "file": f"data/bulletins/{path.name}",
        })
    archive_index_path = bulletin_docs / "index.json"
    archive_index_path.write_text(json.dumps({
        "schema_version": 1,
        "generated_at": world["generated_at"],
        "retained_public_bulletins": len(archive_entries),
        "entries": archive_entries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in sorted(bulletin_docs.glob("*.json")):
        manifests.append({
            "path": f"data/bulletins/{path.name}",
            "sha256": _sha256(path),
        })
    manifest = {
        "generated_at": world["generated_at"],
        "model_version": world["model_version"],
        "files": sorted(manifests, key=lambda x: x["path"]),
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "manifest.sha256").write_text(_sha256(manifest_path) + "  manifest.json\n", encoding="utf-8")
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    (docs_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (docs_dir / "404.html").write_text(INDEX_HTML, encoding="utf-8")
    (docs_dir / "patterns.html").write_text(PATTERNS_HTML, encoding="utf-8")
    (docs_dir / "sensors.html").write_text(SENSORS_HTML, encoding="utf-8")
    return world


INDEX_HTML = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08111f"><title>SismoAI World Cloud</title>
<style>
:root{--bg:#07111f;--card:#101f34;--line:#263d5d;--text:#e8f0fb;--muted:#9eb1c8;--ok:#60d394;--warn:#ffd166;--bad:#ff6b6b;--accent:#67a5ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}header{position:sticky;top:0;z-index:5;background:rgba(7,17,31,.96);border-bottom:1px solid var(--line);padding:16px}h1{font-size:21px;margin:0}h2{font-size:18px;margin:0 0 10px}.notice{margin-top:7px;color:var(--warn);font-size:12px;line-height:1.4}.wrap{max-width:1500px;margin:auto;padding:14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}.value{font-size:28px;font-weight:750;margin-top:4px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.section{margin-top:18px;padding:14px;background:#0b192b;border:1px solid var(--line);border-radius:12px}.bulletin{margin:0 0 14px;padding:16px;background:linear-gradient(145deg,#102540,#0b192b);border:1px solid #31547e;border-radius:14px}.bulletinhead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.bulletintools{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.bulletintools select{min-width:165px}.bulletintools button{background:#1b4f86;color:#fff;border:1px solid #4c83bf;border-radius:8px;padding:9px 11px;cursor:pointer}.bulletintools button.secondary{background:#172b45}.bulletintools button:disabled{opacity:.45;cursor:not-allowed}.classification{display:inline-block;margin:5px 0 9px;padding:4px 9px;border-radius:999px;background:#18385e;font-size:12px;font-weight:700}.bulletinbody{line-height:1.55;font-size:14px}.bulletinbody h3{font-size:13px;color:#b9d3f3;margin:14px 0 4px}.bulletinbody p{margin:4px 0}.official{margin-top:13px;padding:9px 11px;border-left:4px solid var(--warn);background:#18263a;color:var(--warn);font-weight:700}.voicehint{font-size:11px;color:var(--muted);margin-top:7px}.researchnotice{font-size:12px;color:var(--warn);line-height:1.5;margin-bottom:12px}.researchgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:10px}.scroll{overflow:auto;max-height:460px;border:1px solid var(--line);border-radius:10px}.tag{display:inline-block;padding:3px 7px;margin:2px;border-radius:999px;background:#1d3553;font-size:10px}input,select{background:#0b192b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;min-width:180px}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;background:var(--card);font-size:12px}th,td{padding:9px;border-bottom:1px solid #20344f;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#12243b;z-index:1}tr:hover{background:#142844;cursor:pointer}.pill{padding:3px 7px;border-radius:999px;background:#1d3553}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}.bar{width:90px;height:6px;background:#263d5d;border-radius:9px;overflow:hidden}.bar span{display:block;height:100%;background:var(--accent)}dialog{width:min(920px,96vw);max-height:90vh;overflow:auto;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:14px;padding:0}dialog::backdrop{background:rgba(0,0,0,.7)}.modalhead{position:sticky;top:0;background:#12243b;padding:14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.modalbody{padding:14px}.close{background:#233c5e;color:#fff;border:0;border-radius:7px;padding:7px 10px}.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}pre{white-space:pre-wrap;word-break:break-word;background:#081522;padding:10px;border-radius:9px;font-size:11px}a{color:#9dc2ff}footer{padding:30px 0;color:var(--muted);font-size:11px}@media(max-width:600px){.bulletintools{width:100%}.bulletintools select{width:100%}}

.roadmapgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;margin-top:10px}
.roadmapitem{background:#101f34;border:1px solid var(--line);border-radius:12px;padding:13px;line-height:1.5}
.roadmapitem h3{margin:0 0 8px;font-size:14px;color:#c7dcf7}
.roadmapitem p{margin:6px 0}
.roadmapitem ul,.roadmapitem ol{margin:7px 0 0;padding-left:20px}
.roadmapitem li{margin:5px 0}
.roadmapexample{border-left:4px solid var(--accent);background:#081522;padding:12px;border-radius:9px;white-space:pre-wrap;line-height:1.55}
.roadmapwarning{border-left:4px solid var(--warn);background:#18263a;color:var(--warn);padding:10px 12px;border-radius:8px;font-weight:700}
.roadmaplinks{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.roadmaplinks a{display:inline-block;padding:7px 9px;border:1px solid #31547e;border-radius:8px;background:#102540;text-decoration:none}


.notificationgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-top:10px}
.notificationitem{background:#101f34;border:1px solid var(--line);border-radius:12px;padding:13px;line-height:1.5}
.notificationitem h3{margin:0 0 7px;font-size:14px;color:#c7dcf7}
.notificationstatus{display:inline-block;padding:4px 8px;border-radius:999px;background:#18385e;font-weight:700;font-size:11px}

</style></head><body>
<header><h1>SismoAI World Cloud · Vigilancia experimental mundial por macroregiones</h1><div class="notice" id="notice">Cargando aviso científico…</div></header>
<main class="wrap"><section class="bulletin" id="bulletin"><div class="bulletinhead"><div><h2 id="bulletinTitle">Boletín SismoAI</h2><span class="classification" id="bulletinClass">Cargando…</span><div class="muted" id="bulletinTime"></div></div><div class="bulletintools"><select id="bulletinLanguage" aria-label="Idioma del boletín"></select><button id="listenButton" type="button">▶ Escuchar boletín</button><button class="secondary" id="pauseButton" type="button" disabled>⏸ Pausar</button><button class="secondary" id="stopButton" type="button" disabled>■ Detener</button></div></div><div class="bulletinbody"><p id="bulletinSummary">Generando explicación de esta actualización…</p><h3 id="changesTitle">Qué cambió</h3><p id="bulletinChanges"></p><h3 id="situationTitle">Situación actual</h3><div id="bulletinRegions"></div><h3 id="historyTitle">Memoria y patrones</h3><p id="bulletinHistory"></p><h3 id="limitationsTitle">Alcance y limitaciones</h3><p id="bulletinLimitations"></p><div class="official" id="officialLabel">No es una alerta oficial</div><div class="voicehint" id="voiceHint"></div><div class="voicehint"><a id="bulletinArchive" href="data/bulletins/index.json">Archivo de boletines</a></div></div></section><section class="cards" id="summary"></section>
<div class="toolbar"><input id="search" placeholder="Buscar región"><select id="group"><option value="">Todos los grupos</option></select><select id="state"><option value="">Todos los estados</option><option>NORMAL</option><option>WATCH</option><option>ELEVATED</option><option>HIGHLY_ATYPICAL</option><option>NO_DATA</option></select></div>
<div class="tablewrap"><table><thead><tr><th>#</th><th>Región</th><th>Grupo</th><th>IEDC</th><th>Estado</th><th>Confianza</th><th>Cobertura</th><th>Calidad</th><th>Familias</th><th>Último evento</th><th>Actualizado</th></tr></thead><tbody id="rows"></tbody></table></div>
<section class="section"><h2>Laboratorio histórico y búsqueda de patrones</h2><div class="roadmaplinks" style="margin-bottom:12px"><a id="patternsCatalogLink" href="patterns.html">Abrir catálogo explicativo de patrones →</a><a id="sensorsGatewayLink" href="sensors.html">Abrir gateway universal de sensores →</a></div><div class="researchnotice" id="historicalNotice">Cargando reconstrucción histórica…</div><div class="cards" id="historicalSummary"></div><div class="researchgrid"><div><h3>Cobertura real de fuentes</h3><div class="scroll" id="historicalSources"></div></div><div><h3>Patrones candidatos</h3><div class="scroll" id="historicalPatterns"></div></div></div><h3>Fuentes contextuales separadas</h3><div id="contextControls"></div></section>

<section class="section" id="scientificRoadmap">
  <h2>Próxima etapa científica: ventanas probabilísticas en modo sombra</h2>
  <div class="researchnotice" id="roadmapProgress">Cargando el estado actual de la memoria histórica…</div>
  <div class="cards" id="roadmapSummary"></div>

  <div class="roadmapgrid">
    <div class="roadmapitem">
      <h3>Formato responsable de una ventana</h3>
      <div class="roadmapexample">Ventana experimental: 3–10 de agosto de 2026.
Región: Centroamérica.
Objetivo: evento M≥5.
Posibilidad calculada por el modelo: 8 %.
Nivel sísmico habitual de referencia: 2 %.
Confianza o calidad estimada: 76 %.
Estado: investigación en modo sombra.
No es una predicción ni una alerta oficial.</div>
      <p class="muted">Las cifras son solamente un ejemplo de presentación. No deben mostrarse como resultados reales hasta implementar, calibrar y evaluar prospectivamente el módulo.</p>
    </div>

    <div class="roadmapitem">
      <h3>Lo que el sistema no debe afirmar</h3>
      <div class="roadmapwarning">No debe anunciar: “Habrá un terremoto el 5 de agosto”.</div>
      <p>Una predicción exacta intentaría anticipar fecha, lugar y magnitud. SismoAI debe limitarse a regiones, umbrales de magnitud, ventanas temporales, probabilidades calibradas, referencias regionales e incertidumbre.</p>
    </div>

    <div class="roadmapitem">
      <h3>Predicción, pronóstico y alerta temprana</h3>
      <ul>
        <li><b>Predicción exacta:</b> fecha, lugar y magnitud concretos; actualmente no existe un método fiable para lograrlo.</li>
        <li><b>Pronóstico probabilístico:</b> estima una posibilidad para una región y una ventana, comparándola con el nivel habitual.</li>
        <li><b>Alerta temprana:</b> detecta un terremoto que ya comenzó y puede avisar antes de la llegada de la sacudida fuerte; no predice días antes.</li>
      </ul>
      <div class="roadmaplinks">
        <a href="https://www.usgs.gov/faqs/can-you-predict-earthquakes" target="_blank" rel="noopener">USGS: predicción sísmica</a>
        <a href="https://www.usgs.gov/faqs/what-difference-between-earthquake-early-warning-earthquake-forecasts-earthquake-probabilities" target="_blank" rel="noopener">USGS: diferencias científicas</a>
        <a href="https://www.usgs.gov/programs/earthquake-hazards/science/earthquake-early-warning-overview" target="_blank" rel="noopener">USGS: alerta temprana</a>
      </div>
    </div>

    <div class="roadmapitem">
      <h3>Calendario responsable</h3>
      <ol>
        <li><b>Estimación inicial del 24 de julio de 2026:</b> con 24 de 643 meses procesados, completar el histórico podía tomar aproximadamente 6–9 días, entre el 30 de julio y el 2 de agosto, si las ejecuciones continuaban sin errores. Es una estimación, no una garantía.</li>
        <li><b>Después del histórico:</b> iniciar ventanas internas de 24 horas, 72 horas, 7 días y 30 días, exclusivamente en modo sombra.</li>
        <li><b>Después de 3–6 meses:</b> mostrar resultados experimentales en una sección separada solamente si existen suficientes ventanas cerradas y pruebas aprobadas.</li>
        <li><b>Después de 12–24 meses:</b> realizar una evaluación prospectiva más seria y comparar estabilidad entre regiones y versiones.</li>
        <li><b>Alerta pública:</b> únicamente si supera el gate científico estricto. Puede que nunca lo supere; en ese caso, lo correcto es no emitirla.</li>
      </ol>
    </div>

    <div class="roadmapitem">
      <h3>Evaluación prospectiva</h3>
      <p>Cada cálculo debe registrarse antes de conocer qué ocurrirá, quedar sellado con versión, fecha y hash, permanecer inalterable durante la ventana y evaluarse al cierre con reglas fijadas previamente.</p>
      <p>Esto ayuda a distinguir capacidad real fuera de muestra de coincidencias encontradas al revisar el pasado.</p>
      <div class="roadmaplinks">
        <a href="https://cseptesting.org/" target="_blank" rel="noopener">CSEP Testing</a>
        <a href="https://docs.cseptesting.org/" target="_blank" rel="noopener">Documentación CSEP</a>
      </div>
    </div>

    <div class="roadmapitem">
      <h3>Marcador científico público</h3>
      <p>Debe mostrar todos los resultados, no solamente los favorables:</p>
      <ul>
        <li>ventanas generadas, pendientes y cerradas;</li>
        <li>HIT, NEAR, MISS y NO EVENT;</li>
        <li>aciertos, fallos, falsas alarmas y omisiones;</li>
        <li>precision, recall, especificidad y F1;</li>
        <li>Brier, Brier base, log-loss y calibración;</li>
        <li>calidad, cobertura, confianza y número de muestras.</li>
      </ul>
    </div>

    <div class="roadmapitem">
      <h3>Mapa y línea temporal mundial</h3>
      <p>La evolución prevista incluye un mapa interactivo con terremotos, macroregiones, límites tectónicos, fallas, estaciones GNSS, cobertura GOES, catálogo InSAR, productos procesados y salud de las fuentes.</p>
      <p>Cada región debe explicar por qué el IEDC subió o bajó, qué familias contribuyeron, qué datos faltaban y si la señal estaba dominada por una secuencia posterior.</p>
    </div>

    <div class="roadmapitem">
      <h3>Estado real de las fuentes e InSAR</h3>
      <p>Las fuentes deben identificarse como OK, CACHED, DEGRADED, DELAYED, MISSING, ERROR, DISABLED, CATALOG_ONLY o PREPARED_NOT_IMPLEMENTED.</p>
      <p>Un catálogo ASF/Sentinel-1/OPERA no equivale a desplazamiento procesado. Deben separarse descubrimiento, descarga, procesamiento, validación y señal utilizable.</p>
    </div>

    <div class="roadmapitem">
      <h3>Comparación contra modelos básicos</h3>
      <p>Las ventanas deben compararse con frecuencia histórica regional, tasa constante, promedio móvil, persistencia de actividad reciente y otros baselines definidos antes de evaluar.</p>
      <p>Un patrón solo aporta valor si mejora prospectivamente la calibración o la discriminación frente a una regla sencilla.</p>
    </div>

    <div class="roadmapitem">
      <h3>Registro inalterable</h3>
      <p>Cada pronóstico debe guardar identificador, versión, región, objetivo, inicio, cierre, probabilidad, referencia, calidad, cobertura, fuentes, estado del gate y SHA-256.</p>
      <p>La evaluación posterior debe quedar en un registro separado. El pronóstico original nunca debe reescribirse después de conocer el resultado.</p>
    </div>
  </div>

  <div class="roadmapwarning" style="margin-top:12px">Los patrones históricos actuales son candidatos preliminares. No justifican fechas exactas, avisos de peligro, evacuaciones ni la apertura del gate público.</div>
</section>


<section class="section" id="privateNotificationCenter">
  <h2>Avisos automáticos por GitHub y correo</h2>
  <div class="researchnotice" id="notificationNotice">Cargando estado de notificaciones…</div>
  <div class="cards" id="notificationSummary"></div>
  <div class="notificationgrid">
    <div class="notificationitem">
      <h3>GitHub Issues · canal gratuito activo</h3>
      <p>Cuando aparezca una condición nueva incluida en la política, SismoAI crea un Issue, lo asigna a <code>Satodictator</code> y menciona esa cuenta.</p>
      <p>El aviso aparece en las notificaciones de GitHub. El correo depende de que la cuenta tenga habilitadas las notificaciones por email.</p>
      <div class="roadmapwarning">Los Issues son públicos porque este repositorio es público. No contienen teléfonos, tokens ni credenciales.</div>
    </div>
    <div class="notificationitem">
      <h3>Cuándo genera un aviso</h3>
      <ul>
        <li>cambio regional a WATCH, ELEVATED o HIGHLY_ATYPICAL;</li>
        <li>aumento del IEDC de al menos 10 puntos mientras la región siga en observación;</li>
        <li>nuevo evento observado M≥5 en una macroregión;</li>
        <li>futura ventana en modo sombra con <code>notification_eligible=true</code>.</li>
      </ul>
      <p>La primera ejecución es silenciosa y cada novedad queda deduplicada. Varias novedades de la misma ejecución se agrupan en un solo Issue.</p>
    </div>
    <div class="notificationitem">
      <h3>Frecuencia y contenido</h3>
      <p>La evaluación se realiza en cada ejecución mundial de SismoAI, normalmente cada seis horas.</p>
      <p>El Issue informa región, hora evaluada, actividad observada, lugar reportado o ventana experimental, según el tipo de novedad.</p>
    </div>
    <div class="notificationitem">
      <h3>Otros canales</h3>
      <p>Telegram queda opcional y desactivado mientras no exista un bot. WhatsApp y llamadas continúan desactivados porque no existe una vía oficial automática completamente gratuita.</p>
    </div>
    <div class="notificationitem">
      <h3>Alcance científico</h3>
      <p>Un aviso puede informar actividad ya observada, cambios del IEDC o una ventana probabilística experimental. No afirmará una fecha, localidad y magnitud exactas como hecho futuro.</p>
      <div class="roadmapwarning">No es una predicción, una alerta oficial ni una orden de evacuación.</div>
    </div>
  </div>
</section>

<footer>Resultados provisionales para investigación. Consulte organismos oficiales para información de seguridad y emergencia. Integridad: <a href="data/manifest.json">manifest.json</a>.</footer></main>
<dialog id="detail"><div class="modalhead"><strong id="detailTitle"></strong><button class="close" onclick="detail.close()">Cerrar</button></div><div class="modalbody" id="detailBody"></div></dialog>
<script>
let WORLD=null,HISTORICAL=null,BULLETIN=null,currentUtterance=null; const $=s=>document.querySelector(s); const pct=v=>((Number(v)||0)*100).toFixed(1)+'%'; const num=(v,d=1)=>v===null||v===undefined?'—':Number(v).toFixed(d); const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function cls(s){return s==='OK'||s==='NORMAL'?'ok':s==='ERROR'||s==='FATAL'?'bad':'warn'}
function detectedLanguage(){let available=new Set((BULLETIN?.languages||[]).map(x=>x.code));let candidates=navigator.languages?.length?navigator.languages:[navigator.language||'es'];for(let value of candidates){let code=String(value).toLowerCase().split('-')[0];if(available.has(code))return code}return available.has('en')?'en':'es'}
function selectedLanguage(){let selected=$('#bulletinLanguage').value||'auto';return selected==='auto'?detectedLanguage():selected}
function bulletinMessage(){let code=selectedLanguage();return BULLETIN?.messages?.[code]||BULLETIN?.messages?.es||{}}
function renderBulletin(){if(!BULLETIN)return;let m=bulletinMessage(),detected=detectedLanguage(),paused=Boolean(window.speechSynthesis?.paused);$('#bulletinTitle').textContent=m.headline||'SismoAI';$('#bulletinClass').textContent=m.classification||BULLETIN.classification;$('#bulletinTime').textContent=(m.generated_label||'Actualizado')+': '+(BULLETIN.generated_at||'—');$('#bulletinSummary').textContent=m.summary||'';$('#changesTitle').textContent=m.changes_label||'';$('#bulletinChanges').textContent=m.changes||'';$('#situationTitle').textContent=m.situation_label||'';$('#bulletinRegions').innerHTML=(m.regions||[]).map(x=>`<p>${esc(x)}</p>`).join('');$('#historyTitle').textContent=m.history_label||'';$('#bulletinHistory').textContent=m.historical||'';$('#limitationsTitle').textContent=m.limitations_label||'';$('#bulletinLimitations').textContent=m.limitations||'';$('#officialLabel').textContent=m.official_label||'';$('#listenButton').textContent='▶ '+(m.listen_label||'Listen');$('#pauseButton').textContent=(paused?'▶ ':'⏸ ')+(paused?(m.resume_label||'Resume'):(m.pause_label||'Pause'));$('#stopButton').textContent='■ '+(m.stop_label||'Stop');$('#bulletinArchive').textContent=m.archive_label||'Archive';let auto=$('#bulletinLanguage option[value="auto"]');if(auto)auto.textContent=(m.automatic_label||'Auto')+' · '+((BULLETIN.languages||[]).find(x=>x.code===detected)?.name||detected)}
function setupBulletinLanguages(){let stored='auto';try{stored=localStorage.getItem('sismoai_bulletin_language')||'auto'}catch(e){}let valid=new Set(['auto',...(BULLETIN.languages||[]).map(x=>x.code)]);if(!valid.has(stored))stored='auto';$('#bulletinLanguage').innerHTML='<option value="auto">Automático</option>'+(BULLETIN.languages||[]).map(x=>`<option value="${esc(x.code)}">${esc(x.name)}</option>`).join('');$('#bulletinLanguage').value=stored}
function setSpeechButtons(speaking=false){$('#pauseButton').disabled=!speaking;$('#stopButton').disabled=!speaking}
function playBulletin(){if(!('speechSynthesis'in window))return;let synth=window.speechSynthesis,m=bulletinMessage();if(synth.paused){synth.resume();renderBulletin();return}synth.cancel();let utterance=new SpeechSynthesisUtterance(m.spoken||'');utterance.lang=m.voice_prefix||selectedLanguage();let voices=synth.getVoices(),prefix=String(m.voice_prefix||selectedLanguage()).toLowerCase();let voice=voices.find(x=>x.lang.toLowerCase()===prefix)||voices.find(x=>x.lang.toLowerCase().startsWith(prefix+'-'));if(voice){utterance.voice=voice;$('#voiceHint').textContent=''}else{$('#voiceHint').textContent=m.no_voice||''}utterance.rate=0.98;utterance.onstart=()=>setSpeechButtons(true);utterance.onend=()=>{currentUtterance=null;setSpeechButtons(false);renderBulletin()};utterance.onerror=()=>{currentUtterance=null;setSpeechButtons(false);renderBulletin()};currentUtterance=utterance;synth.speak(utterance)}
function pauseBulletin(){if(!('speechSynthesis'in window))return;let synth=window.speechSynthesis;if(synth.paused)synth.resume();else if(synth.speaking)synth.pause();renderBulletin()}
function stopBulletin(){if('speechSynthesis'in window)window.speechSynthesis.cancel();currentUtterance=null;setSpeechButtons(false);renderBulletin()}
async function load(){let [wr,hr,br]=await Promise.all([fetch('data/world.json?'+Date.now()),fetch('data/historical.json?'+Date.now()),fetch('data/bulletin.json?'+Date.now())]);WORLD=await wr.json();HISTORICAL=await hr.json();BULLETIN=await br.json();$('#notice').textContent=WORLD.scientific_notice;$('#summary').innerHTML=`<div class=card><div class=label>Generado</div><div class=value style="font-size:17px">${esc(WORLD.generated_at)}</div></div><div class=card><div class=label>Regiones configuradas</div><div class=value>${WORLD.regions_configured}</div></div><div class=card><div class=label>Regiones operativas</div><div class=value>${WORLD.regions_operational}</div></div><div class=card><div class=label>Gate público aprobado</div><div class=value>${WORLD.regions_public_valid}</div></div><div class=card><div class=label>Modo de ejecución</div><div class=value style="font-size:20px">${esc(WORLD.operation_mode)}</div></div>`;let groups=[...new Set(WORLD.ranking.map(x=>x.group))].sort();$('#group').innerHTML='<option value="">Todos los grupos</option>'+groups.map(x=>`<option>${esc(x)}</option>`).join('');setupBulletinLanguages();renderBulletin();render();renderHistorical();renderScientificRoadmap();renderPrivateNotifications();if(!('speechSynthesis'in window)){let m=bulletinMessage();$('#listenButton').disabled=true;$('#voiceHint').textContent=m.no_voice||'Audio no disponible.'}}
function render(){let q=$('#search').value.toLowerCase(),g=$('#group').value,s=$('#state').value;let rows=WORLD.ranking.filter(x=>(!q||(x.region_name+' '+x.region_id).toLowerCase().includes(q))&&(!g||x.group===g)&&(!s||x.state===s));$('#rows').innerHTML=rows.map(x=>`<tr onclick="openRegion('${esc(x.region_id)}')"><td>${x.rank}</td><td><b>${esc(x.region_name)}</b><br><span class=muted>${esc(x.region_id)}</span></td><td>${esc(x.group)}</td><td><b>${num(x.iedc_provisional)}</b></td><td class=${cls(x.state)}>${esc(x.state)}</td><td>${pct(x.confidence)}<div class=bar><span style="width:${pct(x.confidence)}"></span></div></td><td>${pct(x.coverage)}</td><td>${pct(x.data_quality)}</td><td>${x.available_families}</td><td>${x.latest_event?.magnitude?('M '+num(x.latest_event.magnitude,1)+' · '+esc(x.latest_event.event_time).slice(0,10)):'—'}</td><td>${esc(x.generated_at||'—')}</td></tr>`).join('')}
function renderHistorical(){let h=HISTORICAL||{},c=h.catalog||{},patterns=h.patterns||[],sources=h.sources||[],controls=h.context_controls||[];let patternLink=$('#patternsCatalogLink');if(patternLink)patternLink.textContent=`Abrir catálogo explicativo de ${patterns.length} patrones →`;$('#historicalNotice').textContent=h.scientific_notice||'Laboratorio separado del IEDC.';$('#historicalSummary').innerHTML=`<div class=card><div class=label>Estado histórico</div><div class="value ${cls(h.run_status)}" style="font-size:18px">${esc(h.state||'INICIANDO')}</div></div><div class=card><div class=label>Progreso desde 1973</div><div class=value>${pct(c.progress)}</div><div class=muted>${Number(c.months_complete||0)} / ${Number(c.months_total||0)} meses</div></div><div class=card><div class=label>Eventos USGS M≥4.5</div><div class=value>${Number(c.events||0).toLocaleString()}</div><div class=muted>${esc(c.earliest_event||'cargando')}</div></div><div class=card><div class=label>Patrones candidatos</div><div class=value>${patterns.length}</div><div class=muted>Investigación; gate público 0</div></div>`;$('#historicalSources').innerHTML=sources.length?`<table><thead><tr><th>Fuente</th><th>Familia</th><th>Regiones</th><th>Registros</th><th>Estado</th><th>Función</th></tr></thead><tbody>${sources.map(x=>`<tr><td>${esc(x.source)}</td><td>${esc(x.family)}</td><td>${Number(x.regions||0)}</td><td>${Number(x.records||0).toLocaleString()}</td><td class=${cls(x.status)}>${esc(x.status)}</td><td>${esc(x.role)}${x.affects_iedc?' · IEDC':' · separado'}</td></tr>`).join('')}</tbody></table>`:'<div class=card>El inventario de fuentes se está construyendo.</div>';$('#historicalPatterns').innerHTML=patterns.length?`<table><thead><tr><th>Estado</th><th>Objetivo</th><th>Regla</th><th>Prueba posterior</th></tr></thead><tbody>${patterns.map(x=>{let m=x.test_metrics||{};return `<tr><td>${esc(x.status)}<br><span class=muted>SOLO INVESTIGACIÓN</span></td><td>${esc(x.target)}<br><span class=muted>${esc(x.scope)}</span></td><td>${esc(x.expression)}</td><td>Precisión ${m.precision==null?'—':pct(m.precision)}<br>Recall ${m.recall==null?'—':pct(m.recall)}<br>Lift ${m.lift==null?'—':num(m.lift,2)}<br>TP ${Number(m.tp||0)} / FP ${Number(m.fp||0)}</td></tr>`}).join('')}</tbody></table>`:'<div class=card>Aún no hay candidatos con suficientes datos y prueba temporal posterior.</div>';$('#contextControls').innerHTML=controls.map(x=>`<span class=tag title="${esc(x.note)}">${esc(x.source)} · ${esc(x.status)} · NO ACTIVA ALERTAS</span>`).join('')}

function renderScientificRoadmap(){
  let h=HISTORICAL||{},c=h.catalog||{},patterns=h.patterns||[],sources=h.sources||[];
  let done=Number(c.months_complete||0),total=Number(c.months_total||0);
  let progress=total>0?done/total:Number(c.progress||0);
  let insar=sources.find(x=>x.source==='LOCAL_INSAR_PRODUCTS')||{};
  let insarRecords=Number(insar.records||0);
  let gate=Number(WORLD?.regions_public_valid||0);
  let progressText=`Estado actual: ${done} de ${total} meses históricos (${pct(progress)}). `;
  progressText+=`La estimación inicial del 24 de julio de 2026 situaba la finalización entre el 30 de julio y el 2 de agosto si las ejecuciones continuaban sin errores; debe recalcularse con el progreso real y no es una garantía.`;
  $('#roadmapProgress').textContent=progressText;
  $('#roadmapSummary').innerHTML=
    `<div class=card><div class=label>Memoria histórica</div><div class=value>${pct(progress)}</div><div class=muted>${done} / ${total} meses</div></div>`+
    `<div class=card><div class=label>Patrones candidatos</div><div class=value>${patterns.length}</div><div class=muted>Preliminares · investigación</div></div>`+
    `<div class=card><div class=label>InSAR procesado</div><div class=value>${insarRecords.toLocaleString()}</div><div class=muted>Productos de desplazamiento utilizables</div></div>`+
    `<div class=card><div class=label>Gate público aprobado</div><div class=value>${gate}</div><div class=muted>Debe permanecer cerrado sin validación prospectiva</div></div>`+
    `<div class=card><div class=label>Ventanas previstas</div><div class=value style="font-size:18px">24 h · 72 h · 7 d · 30 d</div><div class=muted>Modo sombra</div></div>`;
}


function renderPrivateNotifications(){
  let n=WORLD?.private_notifications||{},channels=n.channels||{},github=channels.github_issue||{},telegram=channels.telegram||{},last=n.last_run||{},policy=n.activation_policy||{};
  let status=n.status||'PREPARANDO';
  let generalClass=status==='ACTIVE'?'ok':'warn';
  let githubClass=github.enabled?'ok':'warn';
  $('#notificationNotice').textContent=(n.scientific_notice||'Avisos experimentales.')+' '+(n.privacy||'');
  $('#notificationSummary').innerHTML=
    `<div class=card><div class=label>Estado general</div><div class="value ${generalClass}" style="font-size:17px">${esc(status)}</div></div>`+
    `<div class=card><div class=label>GitHub Issues</div><div class="value ${githubClass}" style="font-size:18px">${esc(github.status||'PENDIENTE')}</div><div class=muted>Asignado a ${esc(github.assignee||'Satodictator')} · Issue público</div></div>`+
    `<div class=card><div class=label>Correo</div><div class="value ${githubClass}" style="font-size:17px">${github.enabled?'DISPONIBLE':'PENDIENTE'}</div><div class=muted>Depende de la configuración de notificaciones de GitHub</div></div>`+
    `<div class=card><div class=label>Telegram</div><div class="value warn" style="font-size:17px">${esc(telegram.status||'OPTIONAL_DISABLED')}</div><div class=muted>Opcional · no requerido</div></div>`+
    `<div class=card><div class=label>Última evaluación</div><div class=value>${Number(last.issues_created||0)}</div><div class=muted>Issues creados · ${Number(last.candidates||0)} candidatos · ${Number(last.errors||0)} errores</div></div>`+
    `<div class=card><div class=label>Política</div><div class=value style="font-size:16px">${esc((policy.states||[]).join(' · ')||'WATCH · ELEVATED · HIGHLY_ATYPICAL')}</div><div class=muted>Nuevo M≥${Number(policy.observed_event_min_magnitude||5).toFixed(1)} · aumento IEDC ${Number(policy.iedc_increase_points||10).toFixed(0)} puntos</div></div>`;
}

async function openRegion(id){let r=await fetch('data/regions/'+id+'.json?'+Date.now()),x=await r.json(),c=x.current||{};$('#detailTitle').textContent=(x.region?.name||id)+' · IEDC '+num(c.iedc_provisional);let reasons=c.reasons||[],sources=x.sources||[];$('#detailBody').innerHTML=`<div class=grid2><div class=card><div class=label>Estado</div><div class="value ${cls(c.state)}">${esc(c.state||'NO_DATA')}</div></div><div class=card><div class=label>Confianza / Cobertura / Calidad</div><div class=value style="font-size:19px">${pct(c.confidence)} · ${pct(c.coverage)} · ${pct(c.data_quality)}</div></div><div class=card><div class=label>Valor público</div><div class="value ${c.public_valid?'ok':'warn'}" style="font-size:19px">${c.public_valid?num(c.iedc_public):'NO VALIDADO'}</div></div><div class=card><div class=label>Familias</div><div class=value>${c.available_families||0}</div></div></div><h3>Razones del cambio</h3><pre>${esc(JSON.stringify(reasons,null,2))}</pre><h3>Puntuación por familia</h3><pre>${esc(JSON.stringify(c.family_scores||{},null,2))}</pre><h3>Estado de fuentes</h3><pre>${esc(JSON.stringify(sources,null,2))}</pre><h3>Backtest más reciente</h3><pre>${esc(JSON.stringify((x.latest_backtest||[])[0]||{},null,2))}</pre><h3>Conteos</h3><pre>${esc(JSON.stringify(x.counts||{},null,2))}</pre><h3>Errores operacionales</h3><pre>${esc(JSON.stringify(x.errors||[],null,2))}</pre>`;detail.showModal()}
$('#search').addEventListener('input',render);$('#group').addEventListener('change',render);$('#state').addEventListener('change',render);$('#bulletinLanguage').addEventListener('change',()=>{stopBulletin();try{localStorage.setItem('sismoai_bulletin_language',$('#bulletinLanguage').value)}catch(e){}renderBulletin()});$('#listenButton').addEventListener('click',playBulletin);$('#pauseButton').addEventListener('click',pauseBulletin);$('#stopButton').addEventListener('click',stopBulletin);window.addEventListener('beforeunload',()=>{if('speechSynthesis'in window)window.speechSynthesis.cancel()});load().catch(e=>{$('#notice').textContent='No se pudo cargar la actualización: '+e});
</script></body></html>'''

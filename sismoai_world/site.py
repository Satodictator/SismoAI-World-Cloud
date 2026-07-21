from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCIENTIFIC_NOTICE, __version__
from .regions import load_regions


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
                state_results_dir: Path | None = None, mode: str = "unknown") -> dict[str, Any]:
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
    return world


INDEX_HTML = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08111f"><title>SismoAI World Cloud</title>
<style>
:root{--bg:#07111f;--card:#101f34;--line:#263d5d;--text:#e8f0fb;--muted:#9eb1c8;--ok:#60d394;--warn:#ffd166;--bad:#ff6b6b;--accent:#67a5ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}header{position:sticky;top:0;z-index:5;background:rgba(7,17,31,.96);border-bottom:1px solid var(--line);padding:16px}h1{font-size:21px;margin:0}.notice{margin-top:7px;color:var(--warn);font-size:12px;line-height:1.4}.wrap{max-width:1500px;margin:auto;padding:14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}.value{font-size:28px;font-weight:750;margin-top:4px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}input,select{background:#0b192b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;min-width:180px}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;background:var(--card);font-size:12px}th,td{padding:9px;border-bottom:1px solid #20344f;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#12243b;z-index:1}tr:hover{background:#142844;cursor:pointer}.pill{padding:3px 7px;border-radius:999px;background:#1d3553}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}.bar{width:90px;height:6px;background:#263d5d;border-radius:9px;overflow:hidden}.bar span{display:block;height:100%;background:var(--accent)}dialog{width:min(920px,96vw);max-height:90vh;overflow:auto;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:14px;padding:0}dialog::backdrop{background:rgba(0,0,0,.7)}.modalhead{position:sticky;top:0;background:#12243b;padding:14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.modalbody{padding:14px}.close{background:#233c5e;color:#fff;border:0;border-radius:7px;padding:7px 10px}.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}pre{white-space:pre-wrap;word-break:break-word;background:#081522;padding:10px;border-radius:9px;font-size:11px}a{color:#9dc2ff}footer{padding:30px 0;color:var(--muted);font-size:11px}
</style></head><body>
<header><h1>SismoAI World Cloud · Vigilancia experimental mundial por macroregiones</h1><div class="notice" id="notice">Cargando aviso científico…</div></header>
<main class="wrap"><section class="cards" id="summary"></section>
<div class="toolbar"><input id="search" placeholder="Buscar región"><select id="group"><option value="">Todos los grupos</option></select><select id="state"><option value="">Todos los estados</option><option>NORMAL</option><option>WATCH</option><option>ELEVATED</option><option>HIGHLY_ATYPICAL</option><option>NO_DATA</option></select></div>
<div class="tablewrap"><table><thead><tr><th>#</th><th>Región</th><th>Grupo</th><th>IEDC</th><th>Estado</th><th>Confianza</th><th>Cobertura</th><th>Calidad</th><th>Familias</th><th>Último evento</th><th>Actualizado</th></tr></thead><tbody id="rows"></tbody></table></div>
<footer>Resultados provisionales para investigación. Consulte organismos oficiales para información de seguridad y emergencia. Integridad: <a href="data/manifest.json">manifest.json</a>.</footer></main>
<dialog id="detail"><div class="modalhead"><strong id="detailTitle"></strong><button class="close" onclick="detail.close()">Cerrar</button></div><div class="modalbody" id="detailBody"></div></dialog>
<script>
let WORLD=null; const $=s=>document.querySelector(s); const pct=v=>((Number(v)||0)*100).toFixed(1)+'%'; const num=(v,d=1)=>v===null||v===undefined?'—':Number(v).toFixed(d); const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function cls(s){return s==='OK'||s==='NORMAL'?'ok':s==='ERROR'||s==='FATAL'?'bad':'warn'}
async function load(){let r=await fetch('data/world.json?'+Date.now());WORLD=await r.json();$('#notice').textContent=WORLD.scientific_notice;$('#summary').innerHTML=`<div class=card><div class=label>Generado</div><div class=value style="font-size:17px">${esc(WORLD.generated_at)}</div></div><div class=card><div class=label>Regiones configuradas</div><div class=value>${WORLD.regions_configured}</div></div><div class=card><div class=label>Regiones operativas</div><div class=value>${WORLD.regions_operational}</div></div><div class=card><div class=label>Gate público aprobado</div><div class=value>${WORLD.regions_public_valid}</div></div><div class=card><div class=label>Modo de ejecución</div><div class=value style="font-size:20px">${esc(WORLD.operation_mode)}</div></div>`;let groups=[...new Set(WORLD.ranking.map(x=>x.group))].sort();$('#group').innerHTML='<option value="">Todos los grupos</option>'+groups.map(x=>`<option>${esc(x)}</option>`).join('');render()}
function render(){let q=$('#search').value.toLowerCase(),g=$('#group').value,s=$('#state').value;let rows=WORLD.ranking.filter(x=>(!q||(x.region_name+' '+x.region_id).toLowerCase().includes(q))&&(!g||x.group===g)&&(!s||x.state===s));$('#rows').innerHTML=rows.map(x=>`<tr onclick="openRegion('${esc(x.region_id)}')"><td>${x.rank}</td><td><b>${esc(x.region_name)}</b><br><span class=muted>${esc(x.region_id)}</span></td><td>${esc(x.group)}</td><td><b>${num(x.iedc_provisional)}</b></td><td class=${cls(x.state)}>${esc(x.state)}</td><td>${pct(x.confidence)}<div class=bar><span style="width:${pct(x.confidence)}"></span></div></td><td>${pct(x.coverage)}</td><td>${pct(x.data_quality)}</td><td>${x.available_families}</td><td>${x.latest_event?.magnitude?('M '+num(x.latest_event.magnitude,1)+' · '+esc(x.latest_event.event_time).slice(0,10)):'—'}</td><td>${esc(x.generated_at||'—')}</td></tr>`).join('')}
async function openRegion(id){let r=await fetch('data/regions/'+id+'.json?'+Date.now()),x=await r.json(),c=x.current||{};$('#detailTitle').textContent=(x.region?.name||id)+' · IEDC '+num(c.iedc_provisional);let reasons=c.reasons||[],sources=x.sources||[];$('#detailBody').innerHTML=`<div class=grid2><div class=card><div class=label>Estado</div><div class="value ${cls(c.state)}">${esc(c.state||'NO_DATA')}</div></div><div class=card><div class=label>Confianza / Cobertura / Calidad</div><div class=value style="font-size:19px">${pct(c.confidence)} · ${pct(c.coverage)} · ${pct(c.data_quality)}</div></div><div class=card><div class=label>Valor público</div><div class="value ${c.public_valid?'ok':'warn'}" style="font-size:19px">${c.public_valid?num(c.iedc_public):'NO VALIDADO'}</div></div><div class=card><div class=label>Familias</div><div class=value>${c.available_families||0}</div></div></div><h3>Razones del cambio</h3><pre>${esc(JSON.stringify(reasons,null,2))}</pre><h3>Puntuación por familia</h3><pre>${esc(JSON.stringify(c.family_scores||{},null,2))}</pre><h3>Estado de fuentes</h3><pre>${esc(JSON.stringify(sources,null,2))}</pre><h3>Backtest más reciente</h3><pre>${esc(JSON.stringify((x.latest_backtest||[])[0]||{},null,2))}</pre><h3>Conteos</h3><pre>${esc(JSON.stringify(x.counts||{},null,2))}</pre><h3>Errores operacionales</h3><pre>${esc(JSON.stringify(x.errors||[],null,2))}</pre>`;detail.showModal()}
$('#search').addEventListener('input',render);$('#group').addEventListener('change',render);$('#state').addEventListener('change',render);load().catch(e=>{$('#notice').textContent='No se pudo cargar world.json: '+e});
</script></body></html>'''

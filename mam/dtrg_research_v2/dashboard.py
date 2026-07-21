from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template_string

from .engine import ResearchEngine
from .db import rows

HTML = r'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SismoAI DTRG Investigación</title><style>
body{font-family:Segoe UI,Arial,sans-serif;background:#0b1020;color:#e8edf7;margin:0}header{padding:18px 24px;background:#121a2e;position:sticky;top:0}h1{margin:0;font-size:22px}.notice{font-size:12px;color:#ffcf70;margin-top:6px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;padding:16px}.card{background:#151f36;border:1px solid #283754;border-radius:10px;padding:14px}.big{font-size:34px;font-weight:700}.muted{color:#aab7cf;font-size:12px}.ok{color:#68e0a5}.warn{color:#ffcf70}.bad{color:#ff7d86}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:7px;border-bottom:1px solid #293752;text-align:left;vertical-align:top}.wide{grid-column:1/-1}.bar{height:8px;background:#293752;border-radius:5px;overflow:hidden}.bar span{display:block;height:100%;background:#7ca7ff}button{background:#355b9d;color:white;border:0;border-radius:6px;padding:8px 12px;cursor:pointer}pre{white-space:pre-wrap;word-break:break-word}</style></head>
<body><header><h1>SismoAI · DTRG Modo de investigación</h1><div class="notice" id="notice"></div></header><div class="grid" id="app"><div class="card">Cargando datos…</div></div>
<script>
function pct(x){return ((Number(x)||0)*100).toFixed(1)+'%'}function n(x,d=2){return x===null||x===undefined?'—':Number(x).toFixed(d)}
async function load(){let r=await fetch('/api/research/overview');let x=await r.json();let c=x.current||{};let src=x.sources||[];let reasons=c.reasons||[];let fs=c.family_scores||{};
document.getElementById('notice').textContent=c.scientific_notice||'';
let html=`<div class="card"><div class="muted">IEDC provisional real</div><div class="big">${n(c.iedc_provisional,1)}</div><div>${c.state||'NO_DATA'}</div></div>
<div class="card"><div class="muted">Confianza experimental</div><div class="big">${pct(c.confidence)}</div><div class="bar"><span style="width:${pct(c.confidence)}"></span></div></div>
<div class="card"><div class="muted">Cobertura multifuente</div><div class="big">${pct(c.coverage)}</div><div class="bar"><span style="width:${pct(c.coverage)}"></span></div></div>
<div class="card"><div class="muted">Calidad de datos</div><div class="big">${pct(c.data_quality)}</div><div class="bar"><span style="width:${pct(c.data_quality)}"></span></div></div>
<div class="card"><div class="muted">Valor público validado</div><div class="big ${c.public_valid?'ok':'warn'}">${c.public_valid?n(c.iedc_public,1):'NO VALIDADO'}</div><div>${c.public_valid?'Gate científico aprobado':'Resultado visible solo para investigación'}</div></div>
<div class="card"><div class="muted">Línea base</div><div class="big">${pct(c.baseline_progress)}</div><div>Familias disponibles: ${c.available_families||0}</div></div>
<div class="card wide"><h3>Puntuaciones por familia</h3><table><tr><th>Familia</th><th>Puntuación</th></tr>${Object.entries(fs).map(([k,v])=>`<tr><td>${k}</td><td>${n(v,2)}</td></tr>`).join('')}</table></div>
<div class="card wide"><h3>Razones principales del cambio detectado</h3><table><tr><th>Familia</th><th>Señal</th><th>Puntuación</th><th>Razón</th></tr>${reasons.map(q=>`<tr><td>${q.family}</td><td>${q.feature}</td><td>${n(q.score,1)}</td><td>${q.reason}</td></tr>`).join('')||'<tr><td colspan=4>Sin cambios robustos destacados.</td></tr>'}</table></div>
<div class="card wide"><h3>Fuentes actualizadas</h3><table><tr><th>Fuente</th><th>Estado</th><th>Registros</th><th>Cobertura</th><th>Calidad</th><th>Último éxito</th><th>Mensaje</th></tr>${src.map(s=>`<tr><td>${s.source}</td><td class="${s.status==='OK'?'ok':s.status==='ERROR'?'bad':'warn'}">${s.status}</td><td>${s.records}</td><td>${pct(s.coverage)}</td><td>${pct(s.quality)}</td><td>${s.last_success||'—'}</td><td>${s.message||''}</td></tr>`).join('')}</table></div>
<div class="card wide"><h3>Backtest más reciente</h3><pre>${JSON.stringify((x.latest_backtest||[])[0]||{},null,2)}</pre></div>
<div class="card wide"><h3>Conteos y listas</h3><pre>${JSON.stringify(x.counts||{},null,2)}</pre><p><a style="color:#9fc0ff" href="/api/research/events">Eventos</a> · <a style="color:#9fc0ff" href="/api/research/gnss">GNSS</a> · <a style="color:#9fc0ff" href="/api/research/goes">GOES</a> · <a style="color:#9fc0ff" href="/api/research/insar">InSAR</a> · <a style="color:#9fc0ff" href="/api/research/scores">Historial IEDC</a></p></div>`;
document.getElementById('app').innerHTML=html;}load();setInterval(load,60000);
</script></body></html>'''


def create_app(root: Path):
    eng = ResearchEngine(root)
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(HTML)

    @app.get("/api/research/overview")
    def overview():
        return jsonify(eng.status())

    @app.get("/api/research/current")
    def current():
        return jsonify(eng.status()["current"])

    @app.get("/api/research/sources")
    def sources():
        return jsonify(eng.source_status())

    @app.get("/api/research/events")
    def events():
        return jsonify(rows(eng.db, "SELECT event_id,event_time,magnitude,depth_km,latitude,longitude,place,url FROM dtrg_r_events ORDER BY event_time DESC LIMIT 500"))

    @app.get("/api/research/gnss")
    def gnss():
        return jsonify(rows(eng.db, "SELECT * FROM dtrg_r_gnss_stations ORDER BY status,station LIMIT 500"))

    @app.get("/api/research/goes")
    def goes():
        return jsonify(rows(eng.db, "SELECT * FROM dtrg_r_goes_daily ORDER BY day DESC LIMIT 365"))

    @app.get("/api/research/insar")
    def insar():
        return jsonify({
            "observations": rows(eng.db, "SELECT * FROM dtrg_r_insar_obs ORDER BY obs_date DESC LIMIT 500"),
            "catalog": rows(eng.db, "SELECT scene_id,start_time,platform,flight_direction,path_number,url,status FROM dtrg_r_insar_scenes ORDER BY start_time DESC LIMIT 500"),
        })

    @app.get("/api/research/scores")
    def scores():
        return jsonify(rows(eng.db, "SELECT day,iedc_provisional,iedc_public,public_valid,state,confidence,coverage,data_quality,baseline_progress FROM dtrg_r_scores ORDER BY day DESC LIMIT 1000"))

    return app

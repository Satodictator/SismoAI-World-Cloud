
from __future__ import annotations

SENSORS_HTML = r'''
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08111f">
<title>SismoAI · Gateway universal de sensores</title>
<style>
:root{--bg:#07111f;--card:#101f34;--line:#263d5d;--text:#e8f0fb;--muted:#9eb1c8;--ok:#60d394;--warn:#ffd166;--bad:#ff6b6b;--accent:#67a5ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif;line-height:1.5}
header{position:sticky;top:0;z-index:10;background:rgba(7,17,31,.97);border-bottom:1px solid var(--line);padding:14px}
.wrap{max-width:1450px;margin:auto;padding:14px}.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:0 0 9px}h3{font-size:14px;margin:12px 0 6px}
a{color:#a8c9ff}.back{display:inline-block;text-decoration:none;border:1px solid #31547e;background:#102540;border-radius:9px;padding:8px 10px}
.notice{color:var(--warn);font-size:12px}.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:10px;margin:14px 0}
.card,.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}.label{font-size:11px;color:var(--muted);text-transform:uppercase}.value{font-size:27px;font-weight:750;margin-top:3px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}.source{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}
.badges{display:flex;gap:6px;flex-wrap:wrap}.badge{display:inline-block;padding:3px 7px;border-radius:999px;background:#1d3553;font-size:10px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:12px;background:var(--card)}th,td{padding:8px;border-bottom:1px solid #20344f;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#12243b}
.scroll{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:12px}.warning{border-left:4px solid var(--warn);background:#18263a;color:var(--warn);padding:10px 12px;border-radius:8px}
code,pre{font-family:Consolas,monospace;background:#081522;border-radius:8px}code{padding:2px 5px}pre{padding:10px;white-space:pre-wrap;word-break:break-word}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}input,select{background:#0b192b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;min-width:190px}
footer{padding:30px 0;color:var(--muted);font-size:11px}@media(max-width:600px){.grid{grid-template-columns:1fr}.value{font-size:23px}}
</style>
</head>
<body>
<header><div class="wrap" style="padding-top:0;padding-bottom:0"><div class="top">
<div><a class="back" href="./">← Volver al panel mundial</a><h1>Gateway universal de sensores de SismoAI</h1><div class="notice" id="statusLine">Cargando estado…</div></div>
<div><a href="patterns.html">Patrones</a> · <a href="data/sensors.json">JSON</a></div>
</div></div></header>
<main class="wrap">
<section class="cards" id="summary"></section>
<section class="box">
<h2>Qué integra esta capa</h2>
<p>Registra estaciones y observaciones abiertas o autorizadas, normaliza toda fecha a UTC, deduplica, calcula latencia y calidad, asigna macroregión, conserva licencia y procedencia y genera características diarias para investigación.</p>
<div class="warning">Las nuevas familias permanecen aisladas del IEDC, las alertas y las ventanas prospectivas. Las corrientes SeedLink, NTRIP, MQTT, WebSocket, teléfonos, cámaras, DAS y otros equipos continuos requieren un agente persistente externo y autorización del propietario.</div>
</section>
<div class="toolbar">
<input id="search" type="search" placeholder="Buscar fuente, familia o estado">
<select id="statusFilter"><option value="">Todos los estados</option></select>
<select id="roleFilter"><option value="">Todos los roles</option><option>PRE_EVENT_RESEARCH</option><option>EVENT_DETECTION</option><option>TSUNAMI_CONFIRMATION</option><option>CONTEXT_CONTROL</option></select>
</div>
<section><h2>Fuentes y conectores</h2><div id="sources" class="grid"></div></section>
<section class="grid" style="margin-top:14px">
<div><h2>Familias observadas</h2><div class="scroll"><table><thead><tr><th>Familia</th><th>Rol</th><th>Fuentes</th><th>Nodos</th><th>Observaciones</th><th>Última</th></tr></thead><tbody id="families"></tbody></table></div></div>
<div><h2>Cobertura regional</h2><div class="scroll"><table><thead><tr><th>Región</th><th>Familias</th><th>Fuentes</th><th>Nodos</th><th>Última</th></tr></thead><tbody id="regions"></tbody></table></div></div>
</section>
<section style="margin-top:14px"><h2>Observaciones recientes</h2><div class="scroll"><table><thead><tr><th>Hora UTC</th><th>Fuente</th><th>Nodo</th><th>Región</th><th>Familia</th><th>Rol</th><th>Medición</th><th>Valor</th><th>Calidad</th><th>Latencia</th></tr></thead><tbody id="recent"></tbody></table></div></section>
<section class="box" style="margin-top:14px">
<h2>Puente para equipos propios y accesos autorizados</h2>
<p>El agente persistente acepta objetos JSON autenticados y los deposita en una cola append-only. Ejemplo:</p>
<pre>{
  "node_id": "identificador-autorizado",
  "family": "PHONE_IMU",
  "role": "EVENT_DETECTION",
  "observed_at": "2026-07-29T00:00:00Z",
  "measurement": "acceleration_peak",
  "value": 0.012,
  "unit": "m/s2",
  "quality": 0.75,
  "privacy": "PRIVATE"
}</pre>
<p class="muted">Los nodos privados se publican con identificador anonimizado y coordenadas reducidas. La recepción por Internet debe situarse detrás de HTTPS o VPN.</p>
</section>
<footer>Gateway experimental. No constituye predicción determinista, alerta oficial ni orden de evacuación.</footer>
</main>
<script>
let DATA={};
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const n=v=>Number(v||0).toLocaleString('es-ES');
const pct=v=>v==null?'—':(Number(v)*100).toFixed(1)+' %';
const dt=v=>{if(!v)return'—';const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString('es-ES',{dateStyle:'short',timeStyle:'medium',timeZone:'UTC'})+' UTC'};
const secs=v=>v==null?'—':Number(v)<120?Math.round(Number(v))+' s':Number(v)<7200?(Number(v)/60).toFixed(1)+' min':(Number(v)/3600).toFixed(1)+' h';
function cls(status){status=String(status||'');return ['OK','ACTIVE','DISCOVERY_ONLY'].includes(status)?'ok':status.includes('WAITING')||status.includes('REQUIRED')||status.includes('EXTERNAL')||status.includes('DISABLED')?'warn':'bad'}
function renderSummary(){
 const t=DATA.totals||{},r=DATA.last_run||{};
 $('#summary').innerHTML=
 `<div class=card><div class=label>Fuentes registradas</div><div class=value>${n(t.sources_registered)}</div><div class=muted>${n(t.sources_active_or_available)} disponibles o activas</div></div>`+
 `<div class=card><div class=label>Nodos conocidos</div><div class=value>${n(t.nodes)}</div><div class=muted>Estaciones, boyas y nodos autorizados</div></div>`+
 `<div class=card><div class=label>Observaciones guardadas</div><div class=value>${n(t.observations)}</div><div class=muted>${n(r.observations_new)} nuevas en el último ciclo</div></div>`+
 `<div class=card><div class=label>Características</div><div class=value>${n(t.features)}</div><div class=muted>${n(r.features_written)} escritas en el último ciclo</div></div>`+
 `<div class=card><div class=label>Errores 24 h</div><div class=value>${n(t.errors_24h)}</div><div class=muted>Una fuente degradada no detiene las demás</div></div>`+
 `<div class=card><div class=label>Ciclo</div><div class=value>${n(r.run_number)}</div><div class=muted>${esc(r.status||'—')}</div></div>`;
 $('#statusLine').textContent=`Actualizado ${dt(DATA.generated_at)} · Estado ${DATA.status||'—'} · UTC y deduplicación activas`;
}
function setupFilters(){
 const states=[...new Set((DATA.sources||[]).map(x=>x.status).filter(Boolean))].sort();
 $('#statusFilter').innerHTML='<option value="">Todos los estados</option>'+states.map(x=>`<option>${esc(x)}</option>`).join('');
}
function renderSources(){
 const q=$('#search').value.trim().toLowerCase(),st=$('#statusFilter').value,role=$('#roleFilter').value;
 const list=(DATA.sources||[]).filter(s=>{
  const text=[s.source_id,s.name,s.family,s.role,s.status,s.access_mode,s.message,s.license].join(' ').toLowerCase();
  return(!q||text.includes(q))&&(!st||s.status===st)&&(!role||s.role===role);
 });
 $('#sources').innerHTML=list.length?list.map(s=>`<article class=source>
 <div class=badges><span class="badge ${cls(s.status)}">${esc(s.status)}</span><span class=badge>${esc(s.role)}</span><span class=badge>${esc(s.family)}</span></div>
 <h3>${esc(s.name||s.source_id)}</h3>
 <div class=muted>${esc(s.source_id)} · ${esc(s.access_mode)}</div>
 <p>${esc(s.message||'')}</p>
 <div><b>Nodos:</b> ${n(s.nodes)} · <b>Observaciones:</b> ${n(s.observations)} · <b>Calidad:</b> ${pct(s.quality)} · <b>Latencia:</b> ${secs(s.latency_seconds)}</div>
 <div class=muted>Último éxito: ${dt(s.last_success)}${s.requires_secret?' · Requiere secreto '+esc(s.requires_secret):''}</div>
 ${s.license?`<div class=muted>Licencia/condición: ${esc(s.license)}</div>`:''}
 </article>`).join(''):'<div class=box>No hay fuentes que coincidan con el filtro.</div>';
}
function renderTables(){
 $('#families').innerHTML=(DATA.families||[]).map(x=>`<tr><td>${esc(x.family)}</td><td>${esc(x.role)}</td><td>${n(x.sources)}</td><td>${n(x.nodes)}</td><td>${n(x.observations)}</td><td>${dt(x.latest)}</td></tr>`).join('');
 $('#regions').innerHTML=(DATA.region_coverage||[]).map(x=>`<tr><td>${esc(x.region_id)}</td><td>${n(x.families)}</td><td>${n(x.sources)}</td><td>${n(x.nodes)}</td><td>${dt(x.latest)}</td></tr>`).join('');
 $('#recent').innerHTML=(DATA.recent_observations||[]).map(x=>`<tr><td>${dt(x.observed_at)}</td><td>${esc(x.source_id)}</td><td>${esc(x.node_id)}</td><td>${esc(x.region_id||'—')}</td><td>${esc(x.family)}</td><td>${esc(x.role)}</td><td>${esc(x.measurement)}</td><td>${x.value==null?'—':esc(Number(x.value).toLocaleString('es-ES',{maximumFractionDigits:6}))} ${esc(x.unit||'')}</td><td>${pct(x.quality)}</td><td>${secs(x.latency_seconds)}</td></tr>`).join('');
}
async function load(){
 try{const r=await fetch('data/sensors.json?v='+Date.now(),{cache:'no-store'});DATA=r.ok?await r.json():{}}
 catch(e){DATA={status:'NO_DISPONIBLE',sources:[],totals:{}}}
 renderSummary();setupFilters();renderSources();renderTables();
}
['search','statusFilter','roleFilter'].forEach(id=>$('#'+id).addEventListener(id==='search'?'input':'change',renderSources));
load();setInterval(load,300000);
</script>
</body>
</html>
'''

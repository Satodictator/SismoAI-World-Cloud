from __future__ import annotations

PATTERNS_HTML = r'''
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08111f">
<title>SismoAI · Catálogo explicativo de patrones</title>
<style>
:root{--bg:#07111f;--card:#101f34;--line:#263d5d;--text:#e8f0fb;--muted:#9eb1c8;--ok:#60d394;--warn:#ffd166;--bad:#ff6b6b;--accent:#67a5ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif;line-height:1.5}
header{position:sticky;top:0;z-index:10;background:rgba(7,17,31,.97);border-bottom:1px solid var(--line);padding:14px}
.wrap{max-width:1280px;margin:auto;padding:14px}.topline{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:0 0 8px}h3{font-size:15px;margin:16px 0 7px}
a{color:#a8c9ff}.back{display:inline-block;text-decoration:none;border:1px solid #31547e;background:#102540;border-radius:9px;padding:8px 10px}
.notice{color:var(--warn);font-size:12px}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:14px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px}.label{font-size:11px;color:var(--muted);text-transform:uppercase}.value{font-size:27px;font-weight:750;margin-top:3px}
.explain{background:#0b192b;border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0}
.warning{border-left:4px solid var(--warn);background:#18263a;color:var(--warn);padding:10px 12px;border-radius:8px}
.toolbar{display:grid;grid-template-columns:minmax(220px,1fr) repeat(3,minmax(160px,.35fr));gap:8px;margin:14px 0}
input,select,button{font:inherit;background:#0b192b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
button{cursor:pointer;background:#1b4f86;border-color:#4c83bf}button.secondary{background:#172b45}button:disabled{opacity:.45;cursor:not-allowed}
.patterns{display:grid;grid-template-columns:repeat(auto-fit,minmax(315px,1fr));gap:12px}
.pattern{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:14px;display:flex;flex-direction:column;gap:8px}
.pattern.active{border-color:var(--ok);box-shadow:0 0 0 1px rgba(96,211,148,.22)}
.badges{display:flex;gap:6px;flex-wrap:wrap}.badge{display:inline-block;padding:3px 7px;border-radius:999px;background:#1d3553;font-size:10px;font-weight:700}.badge.ok{color:var(--ok)}.badge.warn{color:var(--warn)}
.rule{background:#081522;border-radius:9px;padding:9px;font-family:Consolas,monospace;font-size:11px;word-break:break-word}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.metric{background:#0b192b;border-radius:8px;padding:7px;text-align:center}.metric b{display:block;font-size:16px}
.actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:auto}.actions button{flex:1;min-width:135px}
.empty{padding:30px;text-align:center;background:var(--card);border:1px solid var(--line);border-radius:12px}
dialog{width:min(1000px,96vw);max-height:92vh;overflow:auto;background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:14px;padding:0}
dialog::backdrop{background:rgba(0,0,0,.72)}.modalhead{position:sticky;top:0;z-index:2;background:#12243b;padding:13px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px;align-items:center}
.modalbody{padding:15px}.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}.sectionbox{background:#0b192b;border:1px solid var(--line);border-radius:10px;padding:12px}
.close{background:#233c5e}.audio{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}.audio button{min-width:145px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px;border-bottom:1px solid #20344f;text-align:left}th{color:#b9d3f3}
pre{white-space:pre-wrap;word-break:break-word;background:#081522;padding:10px;border-radius:9px;font-size:11px}
footer{padding:30px 0;color:var(--muted);font-size:11px}
@media(max-width:800px){.toolbar{grid-template-columns:1fr 1fr}.patterns{grid-template-columns:1fr}}
@media(max-width:520px){.toolbar{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr 1fr}h1{font-size:18px}.value{font-size:23px}}
</style>
</head>
<body>
<header>
  <div class="wrap" style="padding-top:0;padding-bottom:0">
    <div class="topline">
      <div>
        <a class="back" href="./">← Volver al panel mundial</a>
        <h1>Catálogo explicativo de patrones de SismoAI</h1>
        <div class="notice" id="statusLine">Cargando la lista actual de patrones…</div>
      </div>
      <a href="data/historical.json">Ver datos históricos JSON</a>
    </div>
  </div>
</header>
<main class="wrap">
  <section class="cards" id="summary"></section>

  <section class="explain">
    <h2>Cómo funciona esta lista</h2>
    <p>Esta página muestra solamente los candidatos seleccionados en el análisis histórico más reciente. Cuando una nueva ejecución encuentra otro candidato, aparece automáticamente. Cuando una regla deja de superar los criterios del análisis más reciente, se retira de esta lista pública.</p>
    <p class="muted">La base histórica puede conservar ejecuciones anteriores para auditoría científica, pero aquí se presenta únicamente la lista vigente. Un patrón candidato es una asociación estadística preliminar: no demuestra causalidad ni garantiza un terremoto futuro.</p>
    <div class="warning">Los patrones son de investigación. No constituyen predicciones deterministas, alertas oficiales ni órdenes de evacuación.</div>
  </section>

  <section class="toolbar">
    <input id="search" type="search" placeholder="Buscar regla, variable, objetivo o región">
    <select id="statusFilter"><option value="">Todos los estados</option><option value="PROMISING_CANDIDATE">Prometedores</option><option value="EXPLORATORY_CANDIDATE">Exploratorios</option></select>
    <select id="targetFilter"><option value="">Todos los objetivos</option></select>
    <select id="activityFilter"><option value="">Activos e inactivos</option><option value="active">Activos en ventana</option><option value="inactive">Sin ventana activa</option></select>
  </section>

  <section id="patterns" class="patterns"></section>
  <footer>Lista generada automáticamente desde el laboratorio histórico vigente de SismoAI. Consulte organismos oficiales para información de seguridad.</footer>
</main>

<dialog id="detail">
  <div class="modalhead">
    <strong id="detailTitle">Patrón</strong>
    <button class="close" id="closeDialog" type="button">Cerrar</button>
  </div>
  <div class="modalbody">
    <div class="audio">
      <button id="listenPattern" type="button">▶ Escuchar explicación</button>
      <button class="secondary" id="pausePattern" type="button" disabled>⏸ Pausar</button>
      <button class="secondary" id="stopPattern" type="button" disabled>■ Detener</button>
    </div>
    <div id="detailBody"></div>
  </div>
</dialog>

<script>
let HISTORICAL={},SHADOW={},PATTERNS=[],CURRENT_SPEECH=null,CURRENT_SPOKEN='';
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const pct=v=>v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+' %';
const num=(v,d=2)=>v===null||v===undefined||Number.isNaN(Number(v))?'—':Number(v).toLocaleString('es-ES',{minimumFractionDigits:d,maximumFractionDigits:d});
const dateText=v=>{if(!v)return'—';let d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString('es-ES',{dateStyle:'medium',timeStyle:'short',timeZone:'UTC'})+' UTC'};
async function getJson(url,fallback){try{let r=await fetch(url+'?v='+Date.now(),{cache:'no-store'});if(!r.ok)return fallback;return await r.json()}catch(e){return fallback}}

function statusName(s){return s==='PROMISING_CANDIDATE'?'Candidato prometedor':'Candidato exploratorio'}
function targetName(t){
  const map={
    M6_WITHIN_72H_SAME_10DEG_CELL:'M≥6 dentro de 72 horas en la misma celda de 10°',
    M7_WITHIN_7D_SAME_10DEG_CELL:'M≥7 dentro de 7 días en la misma celda de 10°',
    REGIONAL_THRESHOLD_EVENT_WITHIN_7D:'Evento que supera el umbral regional dentro de 7 días'
  };
  return map[t]||String(t||'Objetivo no especificado');
}
function scopeName(s){
  if(s==='GLOBAL_SEISMIC_HISTORY')return'Ámbito mundial, evaluado por celdas geográficas de 10° × 10°';
  if(s==='WORLD_REGIONAL_MULTISOURCE')return'Macroregiones mundiales configuradas con variables sísmicas y geofísicas';
  return String(s||'Ámbito no especificado');
}
function featureName(f){
  const exact={
    count_3:'cantidad de sismos registrados durante los últimos 3 días',
    count_7:'cantidad de sismos registrados durante los últimos 7 días',
    count_14:'cantidad de sismos registrados durante los últimos 14 días',
    count_acceleration:'aceleración de la actividad sísmica de los últimos 7 días frente a los 7 anteriores',
    energy_ratio_7:'cambio de energía sísmica agregada entre los últimos 7 días y los 7 anteriores',
    max_mag_14:'magnitud máxima observada durante los últimos 14 días',
    mean_depth_14:'profundidad media de los sismos durante los últimos 14 días',
    shallow_ratio_14:'proporción de sismos superficiales, de 50 km o menos, durante los últimos 14 días'
  };
  if(exact[f])return exact[f];
  const parts=String(f||'').split('__');
  if(parts.length>=4){
    const families={seismic:'sismicidad',gnss:'deformación GNSS',insar:'deformación InSAR',goes_lightning_control:'control atmosférico GOES/GLM'};
    const features={seismic_count:'tasa sísmica',seismic_energy_log10:'energía sísmica agregada',seismic_max_mag:'magnitud máxima',gnss_residual_mm:'desplazamiento residual GNSS',insar_abs_displacement_mm:'desplazamiento absoluto InSAR',goes_flash_count:'cantidad de rayos',goes_energy:'energía óptica de rayos'};
    const family=families[parts[0]]||parts[0];
    const feature=features[parts[1]]||parts[1].replaceAll('_',' ');
    const measure=parts[2]==='quality'?'calidad de la medición':'puntuación anómala';
    const period=parts[3]==='delta7'?'cambio de los últimos 7 días respecto de los 7 anteriores':'promedio de los últimos 7 días';
    return `${period} de la ${measure} de ${feature} dentro de la familia ${family}`;
  }
  return String(f||'variable desconocida').replaceAll('_',' ');
}
function parseExpression(expression){
  return String(expression||'').split(/\s+AND\s+/i).map(part=>{
    const m=part.match(/^\s*([A-Za-z0-9_]+)\s*>=\s*([-+0-9.eE]+)\s*$/);
    return m?{feature:m[1],threshold:Number(m[2])}:null;
  }).filter(Boolean);
}
function ruleExplanation(p){
  const conditions=parseExpression(p.expression);
  if(!conditions.length)return'La regla técnica no pudo traducirse automáticamente. Consulte la expresión original.';
  return 'La regla se activa cuando '+conditions.map(c=>`${featureName(c.feature)} alcanza o supera el umbral ${num(c.threshold,3)}`).join(' y ')+'.';
}
function activeWindows(p){
  return (SHADOW.windows||[]).filter(w=>Array.isArray(w.pattern_ids)&&w.pattern_ids.includes(p.pattern_id));
}
function geographicExplanation(p,windows){
  if(windows.length){
    return 'Actualmente este patrón participa en '+windows.length+' ventana(s): '+windows.map(w=>`${w.region_name||w.region_id}, desde ${dateText(w.window_start)} hasta ${dateText(w.window_end)}`).join('; ')+'.';
  }
  if(p.scope==='GLOBAL_SEISMIC_HISTORY')return'No corresponde a un único país. Se prueba de forma mundial y separada dentro de cada celda de aproximadamente 10° × 10°.';
  if(p.scope==='WORLD_REGIONAL_MULTISOURCE')return'No está fijado a una sola región. Puede activarse en cualquiera de las macroregiones configuradas cuando sus variables actuales cumplen la regla.';
  return scopeName(p.scope)+'. No participa actualmente en una ventana abierta.';
}
function metricMeaning(p){
  const m=p.test_metrics||{},parts=[];
  parts.push(`En la prueba histórica posterior tuvo ${Number(m.tp||0)} verdaderos positivos, ${Number(m.fp||0)} falsas alarmas, ${Number(m.fn||0)} eventos omitidos y ${Number(m.tn||0)} verdaderos negativos.`);
  if(m.precision!=null)parts.push(`Su precisión fue ${pct(m.precision)}: de todas las veces que la regla se activó, esa proporción fue seguida por el evento objetivo.`);
  if(m.recall!=null)parts.push(`Su recall fue ${pct(m.recall)}: detectó esa proporción de todos los eventos objetivo disponibles.`);
  if(m.base_rate!=null&&m.lift!=null)parts.push(`La frecuencia habitual era ${pct(m.base_rate)} y el lift fue ${num(m.lift,2)}, es decir, la asociación observada fue aproximadamente ${num(m.lift,2)} veces la frecuencia de referencia dentro de esa prueba.`);
  return parts.join(' ');
}
function whySelected(p){
  const m=p.test_metrics||{};
  if(p.status==='PROMISING_CANDIDATE')return`Se conserva como prometedor porque superó los mínimos internos de rendimiento en entrenamiento y prueba posterior. En la prueba obtuvo lift ${num(m.lift,2)} y ${Number(m.tp||0)} verdaderos positivos.`;
  return`Se conserva como exploratorio porque produjo coincidencias suficientes para continuar estudiándolo, pero todavía no cumple todos los criterios internos para considerarse prometedor.`;
}
function periodText(p){
  if(p.train_start||p.test_start)return`Entrenamiento: ${p.train_start||'—'} a ${p.train_end||'—'}. Prueba cronológicamente posterior: ${p.test_start||'—'} a ${p.test_end||'—'}. Muestras: ${Number(p.samples||0).toLocaleString('es-ES')}; positivos: ${Number(p.positives||0).toLocaleString('es-ES')}.`;
  return'La metodología separa aproximadamente el 70 % inicial para construir la regla y el 30 % cronológicamente posterior para probarla.';
}
function spokenText(p,windows){
  return [
    `Explicación del patrón ${p.pattern_id}.`,
    statusName(p.status)+'.',
    `Objetivo: ${targetName(p.target)}.`,
    `Ámbito: ${scopeName(p.scope)}.`,
    ruleExplanation(p),
    whySelected(p),
    metricMeaning(p),
    periodText(p),
    geographicExplanation(p,windows),
    'Este patrón muestra una asociación estadística preliminar. No demuestra causalidad, no garantiza un terremoto y no constituye una alerta oficial.'
  ].join(' ');
}
function renderSummary(){
  const c=HISTORICAL.catalog||{},activeIds=new Set((SHADOW.windows||[]).flatMap(w=>w.pattern_ids||[]));
  const promising=PATTERNS.filter(p=>p.status==='PROMISING_CANDIDATE').length;
  $('#summary').innerHTML=
    `<div class=card><div class=label>Patrones vigentes</div><div class=value>${PATTERNS.length}</div><div class=muted>Último análisis únicamente</div></div>`+
    `<div class=card><div class=label>Prometedores</div><div class=value>${promising}</div><div class=muted>${PATTERNS.length-promising} exploratorios</div></div>`+
    `<div class=card><div class=label>Activos en ventanas</div><div class=value>${PATTERNS.filter(p=>activeIds.has(p.pattern_id)).length}</div><div class=muted>${Number((SHADOW.windows||[]).length)} ventanas abiertas</div></div>`+
    `<div class=card><div class=label>Memoria histórica</div><div class=value>${pct(c.progress||0)}</div><div class=muted>${Number(c.months_complete||0)} / ${Number(c.months_total||0)} meses</div></div>`;
  $('#statusLine').textContent=`Actualizado: ${HISTORICAL.generated_at||'—'} · Lista vigente y automática · Estado histórico: ${HISTORICAL.state||'—'}`;
}
function setupTargets(){
  const targets=[...new Set(PATTERNS.map(p=>p.target).filter(Boolean))];
  $('#targetFilter').innerHTML='<option value="">Todos los objetivos</option>'+targets.map(t=>`<option value="${esc(t)}">${esc(targetName(t))}</option>`).join('');
}
function filteredPatterns(){
  const q=$('#search').value.trim().toLowerCase(),status=$('#statusFilter').value,target=$('#targetFilter').value,activity=$('#activityFilter').value;
  return PATTERNS.filter(p=>{
    const wins=activeWindows(p),hay=wins.length>0;
    const text=[p.pattern_id,p.expression,p.scope,p.target,statusName(p.status),targetName(p.target),scopeName(p.scope),ruleExplanation(p),...wins.map(w=>w.region_name||w.region_id)].join(' ').toLowerCase();
    return(!q||text.includes(q))&&(!status||p.status===status)&&(!target||p.target===target)&&(!activity||(activity==='active'?hay:!hay));
  }).sort((a,b)=>{
    const aa=activeWindows(a).length?1:0,bb=activeWindows(b).length?1:0;
    if(aa!==bb)return bb-aa;
    return Number((b.test_metrics||{}).lift||0)-Number((a.test_metrics||{}).lift||0);
  });
}
function renderPatterns(){
  const list=filteredPatterns();
  if(!list.length){$('#patterns').innerHTML='<div class=empty>No hay patrones que coincidan con los filtros seleccionados.</div>';return}
  $('#patterns').innerHTML=list.map((p,index)=>{
    const m=p.test_metrics||{},wins=activeWindows(p),active=wins.length>0;
    return`<article class="pattern ${active?'active':''}" data-pattern-id="${esc(p.pattern_id)}">
      <div class=badges><span class="badge ${p.status==='PROMISING_CANDIDATE'?'ok':'warn'}">${esc(statusName(p.status))}</span>${active?'<span class="badge ok">ACTIVO EN VENTANA</span>':'<span class=badge>SIN VENTANA ACTIVA</span>'}</div>
      <h2>Patrón ${index+1}: ${esc(targetName(p.target))}</h2>
      <div class=muted>${esc(scopeName(p.scope))}</div>
      <div class=rule>${esc(p.expression)}</div>
      <p>${esc(ruleExplanation(p))}</p>
      <div class=metrics><div class=metric><span class=muted>Precisión</span><b>${pct(m.precision)}</b></div><div class=metric><span class=muted>Recall</span><b>${pct(m.recall)}</b></div><div class=metric><span class=muted>Lift</span><b>${num(m.lift,2)}</b></div></div>
      <div class=actions><button type=button class=open-pattern>Leer explicación completa</button><button type=button class="secondary listen-card">▶ Escuchar</button></div>
    </article>`;
  }).join('');
  document.querySelectorAll('.pattern').forEach(card=>{
    const id=card.dataset.patternId;
    card.querySelector('.open-pattern').addEventListener('click',()=>openPattern(id,false));
    card.querySelector('.listen-card').addEventListener('click',()=>openPattern(id,true));
  });
}
function openPattern(id,autoplay){
  const p=PATTERNS.find(x=>x.pattern_id===id);if(!p)return;
  const train=p.train_metrics||{},test=p.test_metrics||{},wins=activeWindows(p);
  $('#detailTitle').textContent=`${statusName(p.status)} · ${targetName(p.target)}`;
  const winHtml=wins.length?`<table><thead><tr><th>Región</th><th>Inicio</th><th>Cierre</th><th>Probabilidad</th></tr></thead><tbody>${wins.map(w=>`<tr><td>${esc(w.region_name||w.region_id)}</td><td>${esc(dateText(w.window_start))}</td><td>${esc(dateText(w.window_end))}</td><td>${pct(w.probability)}</td></tr>`).join('')}</tbody></table>`:'<p>No participa actualmente en ninguna ventana probabilística abierta.</p>';
  $('#detailBody').innerHTML=`
    <div class=grid2>
      <div class=sectionbox><div class=label>Identificador</div><b>${esc(p.pattern_id)}</b><br><span class=muted>Generado: ${esc(p.created_at||'—')}</span></div>
      <div class=sectionbox><div class=label>Estado</div><b>${esc(statusName(p.status))}</b><br><span class=muted>Solo investigación</span></div>
      <div class=sectionbox><div class=label>Objetivo</div><b>${esc(targetName(p.target))}</b></div>
      <div class=sectionbox><div class=label>Ubicación o ámbito</div><b>${esc(scopeName(p.scope))}</b></div>
    </div>
    <h3>¿Qué condición encontró?</h3><div class=sectionbox><div class=rule>${esc(p.expression)}</div><p>${esc(ruleExplanation(p))}</p></div>
    <h3>¿Por qué continúa en la lista?</h3><div class=sectionbox><p>${esc(whySelected(p))}</p><p>${esc(periodText(p))}</p></div>
    <h3>¿Qué significan sus resultados?</h3><div class=sectionbox><p>${esc(metricMeaning(p))}</p>
      <table><thead><tr><th>Periodo</th><th>TP</th><th>FP</th><th>TN</th><th>FN</th><th>Precisión</th><th>Recall</th><th>Lift</th><th>Base</th></tr></thead>
      <tbody><tr><td>Entrenamiento</td><td>${Number(train.tp||0)}</td><td>${Number(train.fp||0)}</td><td>${Number(train.tn||0)}</td><td>${Number(train.fn||0)}</td><td>${pct(train.precision)}</td><td>${pct(train.recall)}</td><td>${num(train.lift,2)}</td><td>${pct(train.base_rate)}</td></tr>
      <tr><td>Prueba posterior</td><td>${Number(test.tp||0)}</td><td>${Number(test.fp||0)}</td><td>${Number(test.tn||0)}</td><td>${Number(test.fn||0)}</td><td>${pct(test.precision)}</td><td>${pct(test.recall)}</td><td>${num(test.lift,2)}</td><td>${pct(test.base_rate)}</td></tr></tbody></table>
    </div>
    <h3>¿Dónde está activo ahora?</h3><div class=sectionbox><p>${esc(geographicExplanation(p,wins))}</p>${winHtml}</div>
    <h3>Interpretación responsable</h3><div class=warning>Una coincidencia histórica no demuestra que esta condición cause terremotos. Puede fallar, producir falsas alarmas u omitir eventos. Debe evaluarse prospectivamente antes de cualquier uso público.</div>
    <details><summary>Datos técnicos completos</summary><pre>${esc(JSON.stringify(p,null,2))}</pre></details>`;
  CURRENT_SPOKEN=spokenText(p,wins);
  $('#detail').showModal();
  if(autoplay)setTimeout(playPattern,120);
}
function speechButtons(active){
  $('#pausePattern').disabled=!active;$('#stopPattern').disabled=!active;
}
function playPattern(){
  if(!('speechSynthesis'in window)||!CURRENT_SPOKEN)return;
  const synth=window.speechSynthesis;
  if(synth.paused){synth.resume();$('#pausePattern').textContent='⏸ Pausar';return}
  synth.cancel();
  const u=new SpeechSynthesisUtterance(CURRENT_SPOKEN);u.lang='es-ES';u.rate=.96;
  const voices=synth.getVoices();const v=voices.find(x=>x.lang.toLowerCase()==='es-es')||voices.find(x=>x.lang.toLowerCase().startsWith('es'));
  if(v)u.voice=v;
  u.onstart=()=>speechButtons(true);u.onend=()=>{CURRENT_SPEECH=null;speechButtons(false)};u.onerror=()=>{CURRENT_SPEECH=null;speechButtons(false)};
  CURRENT_SPEECH=u;synth.speak(u);
}
function pausePattern(){if(!('speechSynthesis'in window))return;const s=window.speechSynthesis;if(s.paused){s.resume();$('#pausePattern').textContent='⏸ Pausar'}else if(s.speaking){s.pause();$('#pausePattern').textContent='▶ Continuar'}}
function stopPattern(){if('speechSynthesis'in window)window.speechSynthesis.cancel();CURRENT_SPEECH=null;speechButtons(false);$('#pausePattern').textContent='⏸ Pausar'}
async function load(){
  [HISTORICAL,SHADOW]=await Promise.all([getJson('data/historical.json',{}),getJson('data/shadow.json',{windows:[],status:'NO_DISPONIBLE'})]);
  PATTERNS=Array.isArray(HISTORICAL.patterns)?HISTORICAL.patterns:[];
  renderSummary();setupTargets();renderPatterns();
  if(!('speechSynthesis'in window)){$('#listenPattern').disabled=true;$('#pausePattern').disabled=true;$('#stopPattern').disabled=true}
}
['search','statusFilter','targetFilter','activityFilter'].forEach(id=>$('#'+id).addEventListener(id==='search'?'input':'change',renderPatterns));
$('#closeDialog').addEventListener('click',()=>$('#detail').close());
$('#detail').addEventListener('close',stopPattern);
$('#listenPattern').addEventListener('click',playPattern);
$('#pausePattern').addEventListener('click',pausePattern);
$('#stopPattern').addEventListener('click',stopPattern);
load().catch(e=>{$('#statusLine').textContent='No se pudo cargar el catálogo: '+e;$('#patterns').innerHTML='<div class=empty>Error al cargar los datos.</div>'});
</script>
</body>
</html>

'''

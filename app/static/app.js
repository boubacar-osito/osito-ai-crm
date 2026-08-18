const stages=[['nouvelle','Nouvelles'],['qualifiee','Qualifiées'],['contact','Contact établi'],['entretien','Entretien'],['proposition','Proposition']];
const leadStages=[['nouvelle','Nouvelle'],['a_contacter','À contacter'],['message_envoye','Message envoyé'],['echange_en_cours','Échange en cours'],['mission_detectee','Mission détectée'],['a_reactiver','À réactiver'],['hors_cible','Hors cible']];
let opportunities=[],leads=[],activeView='pipeline';
const $=s=>document.querySelector(s);
const escapeHtml=s=>String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
async function api(path,options={}){const response=await fetch(path,{headers:{'Content-Type':'application/json'},...options});if(!response.ok){const e=await response.json().catch(()=>({detail:'Erreur inattendue'}));throw new Error(e.detail)}return response.json()}
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2600)}
function scoreClass(score){return score>=75?'':score>=50?'mid':'low'}
function renderStats(){
  if(activeView==='leads'){
    const active=leads.filter(l=>l.stage!=='hors_cible');
    const inProgress=leads.filter(l=>['message_envoye','echange_en_cours','mission_detectee'].includes(l.stage));
    $('#stats').classList.remove('hidden');
    $('#stats').innerHTML=`<div class="stat"><b>${active.length}</b><small>Pistes actives</small></div><div class="stat"><b>${leads.filter(l=>l.score>=75&&l.stage!=='hors_cible').length}</b><small>Priorité haute</small></div><div class="stat"><b>${leads.filter(l=>['nouvelle','a_contacter'].includes(l.stage)).length}</b><small>À contacter</small></div><div class="stat"><b>${inProgress.length}</b><small>Process engagé</small></div>`;
    return;
  }
  if(activeView==='profile'){$('#stats').classList.add('hidden');return}
  const active=opportunities.filter(o=>!['gagnee','perdue'].includes(o.stage));
  const top=active.filter(o=>o.score>=75).length;
  $('#stats').classList.remove('hidden');
  $('#stats').innerHTML=`<div class="stat"><b>${active.length}</b><small>Missions actives</small></div><div class="stat"><b>${top}</b><small>Match ≥ 75%</small></div><div class="stat"><b>${active.filter(o=>o.stage==='entretien').length}</b><small>Entretiens</small></div><div class="stat"><b>${active.length?Math.round(active.reduce((a,o)=>a+o.score,0)/active.length):0}%</b><small>Score moyen</small></div>`;
}
function render(){
  renderStats();
  $('#kanban').innerHTML=stages.map(([key,label])=>{const items=opportunities.filter(o=>o.stage===key);return `<div class="column"><div class="column-head"><span>${label}</span><span class="count">${items.length}</span></div>${items.map(card).join('')}</div>`}).join('');
  $('#mission-list').innerHTML=opportunities.map(o=>`<article class="mission-row"><div><h3>${escapeHtml(o.title)}</h3><span class="muted">${escapeHtml(o.company||'Entreprise non renseignée')} · ${escapeHtml(o.location||'Localisation à préciser')}</span></div><span class="score ${scoreClass(o.score)}">${o.score}</span><button class="primary ats" data-id="${o.id}">Adapter le CV</button></article>`).join('')||'<div class="panel muted">Ajoute ta première mission pour commencer le scoring.</div>';
  $('#lead-list').innerHTML=leads.map(l=>`<article class="mission-row"><div><p class="eyebrow">Priorité ${escapeHtml((l.score_details||{}).priorite||'à qualifier')}</p><h3>${escapeHtml(l.name)}</h3><span class="muted">${escapeHtml(l.headline)}${l.company?' · '+escapeHtml(l.company):''}</span></div><span class="score ${scoreClass(l.score)}">${l.score}</span><div class="lead-actions"><select class="lead-stage" data-id="${l.id}" aria-label="Étape de ${escapeHtml(l.name)}">${leadStages.map(([value,label])=>`<option value="${value}" ${l.stage===value?'selected':''}>${label}</option>`).join('')}</select><a class="text-button" href="${escapeHtml(l.linkedin_url)}" target="_blank" rel="noopener">Voir LinkedIn ↗</a></div></article>`).join('')||'<div class="panel muted">Aucune piste importée.</div>';
  document.querySelectorAll('.ats').forEach(b=>b.onclick=()=>analyzeATS(+b.dataset.id));
  document.querySelectorAll('.advance').forEach(b=>b.onclick=()=>advance(+b.dataset.id));
  document.querySelectorAll('.lead-stage').forEach(s=>s.onchange=()=>changeLeadStage(+s.dataset.id,s.value));
}
function card(o){return `<article class="mission-card"><span class="score ${scoreClass(o.score)}">${o.score}</span><h3>${escapeHtml(o.title)}</h3><p>${escapeHtml(o.company||'Entreprise à préciser')}<br>${escapeHtml(o.location||'Lieu à préciser')}${o.daily_rate?' · '+o.daily_rate+' €/j':''}</p><div class="card-foot"><button class="text-button ats" data-id="${o.id}">Analyse ATS</button><button class="text-button advance" data-id="${o.id}">→</button></div></article>`}
async function load(){[opportunities,leads]=await Promise.all([api('/api/opportunities'),api('/api/leads')]);render()}
async function advance(id){const o=opportunities.find(x=>x.id===id);const i=stages.findIndex(s=>s[0]===o.stage);if(i<stages.length-1){await api(`/api/opportunities/${id}/stage`,{method:'PATCH',body:JSON.stringify({stage:stages[i+1][0]})});await load()}}
async function changeLeadStage(id,stage){try{await api(`/api/leads/${id}/stage`,{method:'PATCH',body:JSON.stringify({stage})});await load();toast('Étape de la piste mise à jour')}catch(e){toast(e.message);await load()}}
async function analyzeATS(id){try{const r=await api('/api/ats/analyze',{method:'POST',body:JSON.stringify({opportunity_id:id})});$('#ats-content').innerHTML=`<div class="modal-head"><div><p class="eyebrow">ANALYSE ATS</p><h2>CV adapté à la mission</h2></div><button class="icon" onclick="document.querySelector('#ats-modal').close()">×</button></div><div class="ats-grid"><div class="ats-score" style="--score:${r.match_score}%"><span>${r.match_score}%</span></div><div><b>Mots-clés présents</b><div class="chips">${r.matched_keywords.map(x=>`<span class="chip">${escapeHtml(x)}</span>`).join('')||'Aucun'}</div><br><b>À vérifier dans ton expérience</b><div class="chips">${r.missing_keywords.map(x=>`<span class="chip missing">${escapeHtml(x)}</span>`).join('')||'Aucun'}</div></div></div><label>Titre recommandé<input value="${escapeHtml(r.suggested_title)}" readonly></label><label>Résumé proposé<textarea rows="6" readonly>${escapeHtml(r.tailored_summary)}</textarea></label><p class="muted">${r.warnings.map(escapeHtml).join(' ')}</p>`;$('#ats-modal').showModal()}catch(e){toast(e.message)}}
document.querySelectorAll('.nav').forEach(button=>button.onclick=()=>{activeView=button.dataset.view;document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));button.classList.add('active');document.querySelectorAll('.view').forEach(x=>x.classList.add('hidden'));$(`#${activeView}-view`).classList.remove('hidden');$('#page-title').textContent={pipeline:'Pipeline de missions',opportunities:'Toutes les missions',leads:'Pistes LinkedIn',profile:'Mon profil & CV'}[activeView];$('#open-modal').classList.toggle('hidden',!['pipeline','opportunities'].includes(activeView));renderStats()});
$('#open-modal').onclick=()=>$('#mission-modal').showModal();
$('#mission-form').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.daily_rate=data.daily_rate?+data.daily_rate:null;try{await api('/api/opportunities',{method:'POST',body:JSON.stringify(data)});e.target.reset();$('#mission-modal').close();await load();toast('Mission ajoutée et scorée')}catch(err){toast(err.message)}};
async function loadProfile(){const p=await api('/api/profile');const f=$('#profile-form');for(const [k,v] of Object.entries(p)){if(f.elements[k])f.elements[k].value=Array.isArray(v)?v.join(', '):(v??'')}}
$('#profile-form').onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));['skills','preferred_roles','preferred_locations'].forEach(k=>d[k]=d[k].split(',').map(x=>x.trim()).filter(Boolean));d.minimum_daily_rate=d.minimum_daily_rate?+d.minimum_daily_rate:null;try{await api('/api/profile',{method:'PUT',body:JSON.stringify(d)});await load();toast('Profil enregistré, scores recalculés')}catch(err){toast(err.message)}};
load();loadProfile();

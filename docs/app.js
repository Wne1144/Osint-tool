'use strict';
/* Local OSINT Research Suite — Web-Port (docs/app.js)
   1:1-Port der Logik aus LocalOSINT/app.py (Score, Wikipedia-Adapter,
   2 Iterationen, Review, Report, Backup). Unterschied: statt SQLite auf
   127.0.0.1 speichert diese statische Seite in localStorage; die
   Wikipedia-API wird direkt aus dem Browser aufgerufen (CORS: origin=*).
   Keine Abhängigkeiten, kein Tracking, keine Cookies. */
const LS_KEY = 'localosint.web.v1';
const MAX_ITERATIONS = 2;               // wie app.py
const UNCERTAINTY = 'Wikipedia search relevance is not evidence of identity. Names may refer to different people; snippets are incomplete and require review.';
const TABLES = ['projects','inputs','runs','queries','sources','hits','facts','reviews','contradictions','reports'];
const $ = id => document.getElementById(id);
const now = () => new Date().toISOString().replace(/\.\d+Z$/, '+00:00');
const cleanText = (v, limit = 500) => (typeof v !== 'string' ? '' : v.replace(/\s+/g, ' ').trim().slice(0, limit));
const hexId = () => [...crypto.getRandomValues(new Uint8Array(12))].map(b => b.toString(16).padStart(2, '0')).join('');

let seq = 1;
let db = load();
let current = null;

function emptyDb() { const d = { _seq: 1 }; TABLES.forEach(t => d[t] = []); return d; }
function load() {
  try { const d = JSON.parse(localStorage.getItem(LS_KEY)); if (d && TABLES.every(t => Array.isArray(d[t]))) { seq = d._seq || 1; return d; } } catch (e) { /* defekte Daten -> leer starten */ }
  return emptyDb();
}
function save() { db._seq = seq; try { localStorage.setItem(LS_KEY, JSON.stringify(db)); } catch (e) { alert('Speichern fehlgeschlagen (Browser-Speicher voll/blockiert): ' + e.message); } }
const nextId = () => seq++;

/* ---------- Score: Port von score_result() aus app.py ---------- */
function scoreResult(query, title, snippet) {
  const qtokens = new Set((query.match(/[\p{L}\p{N}_@.\-]+/gu) || []).filter(x => x.length > 1).map(x => x.toLowerCase()));
  const text = (title + ' ' + snippet).toLowerCase();
  const matched = [...qtokens].filter(t => text.includes(t)).sort();
  const overlap = matched.length / Math.max(1, qtokens.size);
  const exactPhrase = !!query.trim() && text.includes(query.toLowerCase());
  const factors = {
    text_overlap: Math.round(overlap * 45),
    exact_phrase: exactPhrase ? 20 : 0,
    source_quality: 15, // Official Wikimedia article, not identity evidence
    specificity: Math.min(10, matched.length * 2),
    independent_evidence: 0,
    uncertainty_penalty: -10
  };
  const total = Math.max(0, Math.min(100, Object.values(factors).reduce((a, b) => a + b, 0)));
  return { score: total, factors, matched };
}

/* ---------- Wikipedia-Adapter: Port von wikipedia_search() ---------- */
async function wikipediaSearch(query) {
  const params = new URLSearchParams({ action: 'query', list: 'search', srsearch: query, srlimit: '5', format: 'json', utf8: '1', origin: '*' });
  const ctrl = new AbortController(); const timer = setTimeout(() => ctrl.abort(), 10000);
  try {
    const r = await fetch('https://en.wikipedia.org/w/api.php?' + params, { signal: ctrl.signal, headers: { 'Accept': 'application/json' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    return ((data.query && data.query.search) || []).map(item => {
      const title = cleanText(item.title || '', 250);
      const snippet = cleanText(String(item.snippet || '').replace(/<[^>]*>/g, ' '), 700);
      return { title, snippet, url: 'https://en.wikipedia.org/wiki/' + encodeURIComponent(title.replace(/ /g, '_')), type: 'encyclopedia', origin: 'Wikipedia MediaWiki public search API' };
    });
  } finally { clearTimeout(timer); }
}

function selectedSearchTerms(projectId) {
  const terms = [];
  for (const r of db.inputs.filter(i => i.project_id === projectId && i.selected)) {
    const v = cleanText(r.value, 180);
    if (['name', 'username', 'email'].includes(r.kind) && v && !terms.includes(v)) terms.push(v);
  }
  return terms.slice(0, 8);
}

/* ---------- Recherche: Port von run_research() ---------- */
async function runResearch(projectId) {
  const project = db.projects.find(p => p.id === projectId); if (!project) return;
  const run = { id: hexId(), project_id: projectId, started: now(), finished: null, status: 'running', iteration: 1, summary: null };
  db.runs.push(run); project.status = 'running'; project.updated = now(); save(); renderAll();
  const terms = selectedSearchTerms(projectId);
  if (!terms.length) return finishRun(run, project, 'error', 'No selected name, username or email search term was supplied.');
  const statusRows = []; let inserted = 0; const seen = new Set();
  for (let iteration = 1; iteration <= MAX_ITERATIONS; iteration++) {
    let batch = iteration === 1
      ? terms.map(t => ({ query: t, derived_from: null, rationale: 'User-selected input term.' }))
      : db.hits.filter(h => h.run_id === run.id && h.score >= 60)
          .sort((a, b) => b.score - a.score)
          .map(h => db.sources.find(s => s.id === h.source_id)).filter(Boolean)
          .filter((s, i, a) => a.findIndex(x => x.title === s.title) === i).slice(0, 2)
          .map(s => ({ query: s.title, derived_from: s.url, rationale: 'Derived from a sourced result title with heuristic score >=60; this is a search lead, not an established fact.' }))
          .filter(x => x.query && !seen.has(x.query.toLowerCase()));
    if (!batch.length) break;
    for (const qitem of batch.slice(0, 8)) {
      const query = cleanText(qitem.query, 180);
      if (!query || seen.has(query.toLowerCase())) continue;
      seen.add(query.toLowerCase());
      let results = [], qstatus, qdetail;
      try { results = await wikipediaSearch(query); qstatus = results.length ? 'success_with_hits' : 'success_no_hits'; qdetail = results.length + ' result(s) from the Wikipedia public search API.'; }
      catch (e) { qstatus = 'technical_error'; qdetail = (e.name || 'Error') + ': external source unavailable or request failed.'; }
      db.queries.push({ id: nextId(), run_id: run.id, adapter: 'Wikipedia API', query, status: qstatus, detail: qdetail, created: now(), iteration, derived_from: qitem.derived_from, rationale: qitem.rationale });
      for (const result of results) {
        let src = db.sources.find(s => s.project_id === projectId && s.url === result.url);
        if (!src) { src = { id: nextId(), project_id: projectId, url: result.url, title: result.title, source_type: result.type, origin: result.origin, first_seen: now() }; db.sources.push(src); }
        const { score, factors, matched } = scoreResult(query, result.title, result.snippet);
        if (!db.hits.some(h => h.run_id === run.id && h.source_id === src.id)) {
          const hit = { id: nextId(), project_id: projectId, run_id: run.id, source_id: src.id, title: result.title, snippet: result.snippet, match_kind: 'name_or_text', score, score_json: JSON.stringify({ factors, matched_terms: matched, query }), uncertainties: UNCERTAINTY, review: 'pending', created: now() };
          db.hits.push(hit); inserted++;
          db.facts.push({ id: nextId(), project_id: projectId, hit_id: hit.id, claim: result.title, evidence: result.snippet, provenance: 'Search snippet from Wikipedia API; primary-source status not established.', created: now() });
        }
      }
      save(); statusRows.push(query + ': ' + qstatus); renderAll();
    }
  }
  finishRun(run, project, 'completed', inserted + ' new source result(s). ' + statusRows.join('; '));
}
function finishRun(run, project, status, summary) {
  run.finished = now(); run.status = status; run.summary = summary;
  project.status = status === 'completed' ? 'ready' : status; project.updated = now(); save(); renderAll();
}

/* ---------- Projekt-Daten & Report: Port von project_data()/build_report() ---------- */
function projectData(pid) {
  const p = db.projects.find(x => x.id === pid); if (!p) return null;
  const runs = db.runs.filter(r => r.project_id === pid).sort((a, b) => b.started.localeCompare(a.started));
  const runIds = new Set(runs.map(r => r.id));
  return { ...p,
    inputs: db.inputs.filter(i => i.project_id === pid),
    runs,
    queries: db.queries.filter(q => runIds.has(q.run_id)).sort((a, b) => b.id - a.id),
    hits: db.hits.filter(h => h.project_id === pid).map(h => { const s = db.sources.find(x => x.id === h.source_id) || {}; return { ...h, url: s.url || '#', origin: s.origin || '', source_type: s.source_type || '' }; }).sort((a, b) => b.score - a.score || b.created.localeCompare(a.created)),
    reviews: db.reviews.filter(rv => db.hits.some(h => h.id === rv.hit_id && h.project_id === pid)) };
}
function buildReport(pid) {
  const d = projectData(pid); if (!d) return null;
  const report = {
    summary: 'This report contains public-source search leads, not an identity determination.',
    project: { name: d.name, purpose: d.purpose, created: d.created, report_created: now(), scope: 'Wikipedia public search API only; searches reflect only selected inputs.' },
    sections: { input_and_scope: 'See project metadata and selected inputs.', public_profiles_and_websites: 'Only results listed in leads; no profile ownership verified.', professional_background: 'Not independently investigated in this MVP.', public_contact_information: 'Not independently investigated in this MVP.', source_links: 'See leads and source register.', timeline: 'Only retrieval timestamps are available; no biographical timeline inferred.', contradictions: [], open_questions: ['Are any leads about the same subject? Human assessment required.'], tools_and_time: 'LocalOSINT Web-MVP using Wikipedia MediaWiki public search API; timestamps are UTC.', unavailable_sources: ['General web search provider APIs, social profile APIs, image/face search, and additional archives are not integrated.'] },
    confirmed_facts: [], leads: [], limitations: ['Search results and name matches do not confirm identity.', 'No face recognition, login attempts, breach-data searches, or paid services are used.', 'A technical failure or zero results does not establish that information does not exist.'],
    sources: [], runs: d.runs, queries: d.queries };
  for (const h of d.hits) {
    report.leads.push({ status: h.review === 'matched' ? 'User-reviewed: matched' : 'Lead — human review required', title: h.title, excerpt: h.snippet, url: h.url, score: h.score, score_components: JSON.parse(h.score_json), uncertainty: h.uncertainties, review: h.review });
    if (h.review === 'matched') report.confirmed_facts.push({ claim: h.title, evidence: h.snippet, source: h.url, qualification: 'User-reviewed assessment; not an objective identity proof.' });
    report.sources.push({ title: h.title, url: h.url, origin: h.origin, retrieved: h.created });
  }
  return report;
}

/* ---------- Rendering ---------- */
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function safeLink(s) { try { const u = new URL(s); return (u.protocol === 'https:' || u.protocol === 'http:') ? esc(u.href) : '#'; } catch (e) { return '#'; } }
function renderAll() { renderProjects(); if (current) renderProject(current, false); }
function renderProjects() {
  const list = [...db.projects].sort((a, b) => b.updated.localeCompare(a.updated));
  $('projects').innerHTML = list.length ? list.map(p => {
    const n = db.hits.filter(h => h.project_id === p.id).length;
    return `<button type="button" class="project ${p.id === current ? 'active' : ''}" data-project="${esc(p.id)}"><span style="flex:1"><b>${esc(p.name)}</b><span class="small" style="display:block">${esc(p.status)} · ${esc(p.created.slice(0, 10))}</span></span><span class="badge">${n} Treffer</span></button>`;
  }).join('') : '<div class="small">Noch keine Projekte.</div>';
  document.querySelectorAll('[data-project]').forEach(el => el.addEventListener('click', () => selectProject(el.dataset.project)));
}
function renderProject(id, rerenderList = true) {
  const d = projectData(id); if (!d) return;
  $('currentTitle').textContent = d.name; $('pstatus').textContent = d.status;
  $('projectActions').classList.remove('hidden'); $('noProjectHint').classList.add('hidden');
  $('runSummary').textContent = 'Zweck: ' + d.purpose + (d.inputs.length ? ' · ' + d.inputs.map(x => x.kind + ': ' + x.value + (x.selected ? '' : ' (nicht für Suche ausgewählt)')).join(' · ') : '');
  $('runStatus').textContent = d.runs.map(x => `${x.started}: ${x.status} — ${x.summary || ''}`).join('\n');
  $('runBtn').disabled = d.status === 'running';
  renderHits(d.hits);
  $('queries').innerHTML = d.queries.length ? d.queries.map(q => `<div class="query"><b>${esc(q.query)}</b> <span class="badge">${esc(q.status)}</span><br>Iteration ${q.iteration} · ${esc(q.adapter)} · ${esc(q.created)}<br>${esc(q.detail || '')}${q.rationale ? '<br><i>' + esc(q.rationale) + '</i>' : ''}${q.derived_from ? '<br>Abgeleitet von: ' + esc(q.derived_from) : ''}</div>`).join('') : 'Noch keine Recherche ausgeführt. Jede Wikipedia-Abfrage (Iteration, Herkunft, Status) wird hier protokolliert — auch technische Fehler.';
  if (rerenderList) renderProjects();
}
function renderHits(hits) {
  let list = hits || (current ? projectData(current).hits : []);
  const f = $('filter').value;
  if (f === 'pending') list = list.filter(h => h.review === 'pending');
  if (f === 'matched') list = list.filter(h => h.review === 'matched');
  if (f === 'low') list = list.filter(h => h.score < 40);
  if ($('sort').value === 'new') list = [...list].reverse();
  if (!list.length) { $('hits').innerHTML = '<div class="small">Keine passenden Treffer. Beachte: Das bedeutet nicht, dass Informationen nicht existieren.</div>'; return; }
  $('hits').innerHTML = list.map(h => {
    const sc = JSON.parse(h.score_json);
    return `<article class="hit"><div class="row"><a target="_blank" rel="noopener noreferrer" href="${safeLink(h.url)}">${esc(h.title)}</a><span class="score ${h.score < 40 ? 'low' : ''}">Score ${h.score}/100</span><span class="badge">${esc(h.review)}</span></div><p>${esc(h.snippet)}</p><div class="details"><b>Bewertung:</b> ${esc(JSON.stringify(sc.factors))} · Treffer-Begriffe: ${esc((sc.matched_terms || []).join(', ') || '—')}<br><b>Unsicherheit:</b> ${esc(h.uncertainties)}<br><b>Quelle:</b> ${esc(h.origin)} · ${esc(h.created)}</div><div class="row">${['matched', 'not_matching', 'unclear', 'more_evidence'].map(v => `<button type="button" class="secondary" data-review="${h.id}:${v}">${{ matched: 'Passend', not_matching: 'Nicht passend', unclear: 'Unklar', more_evidence: 'Weitere Belege' }[v]}</button>`).join('')}<button type="button" class="secondary" data-manual="${esc(encodeURIComponent(h.title))}">Manuell weitersuchen</button></div></article>`;
  }).join('');
  document.querySelectorAll('[data-review]').forEach(b => b.addEventListener('click', () => { const [id, dec] = b.dataset.review.split(':'); review(Number(id), dec); }));
  document.querySelectorAll('[data-manual]').forEach(b => b.addEventListener('click', () => window.open('https://www.google.com/search?q=' + b.dataset.manual, '_blank', 'noopener')));
}
function selectProject(id) { current = id; renderProjects(); renderProject(id, false); }

/* ---------- Aktionen ---------- */
function collectInputs(prefixValues) {
  const inputs = [];
  for (const [id, kind, useId] of [['name', 'name', 'use-name'], ['email', 'email', 'use-email'], ['username', 'username', 'use-username'], ['profile', 'profile_url', null], ['notes', 'notes', null]]) {
    const value = (prefixValues ? prefixValues[kind === 'profile_url' ? 'profile' : kind] : $(id).value).trim();
    if (value) inputs.push({ kind, value, selected: useId ? $(useId).checked : false });
  }
  return inputs;
}
function validateAndCreate(name, purpose, inputs) {
  if (!name || !purpose) throw new Error('Projektname und Zweck sind erforderlich.');
  if (!$('consent').checked) throw new Error('Bitte bestätige den zulässigen Zweck und die Berechtigung.');
  if (inputs.some(x => x.selected && ['name', 'email', 'username'].includes(x.kind)) && !$('external').checked) throw new Error('Für externe Recherche bitte die gesonderte Übermittlungsbestätigung aktivieren; alternativ erfasse nur Hinweise ohne Suchbegriff.');
  for (const item of inputs) {
    if (item.kind === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(item.value)) throw new Error('Invalid email format');
    if (item.kind === 'profile_url') { let u; try { u = new URL(item.value); } catch (e) { throw new Error('Profile URL must be an HTTPS URL without credentials'); } if (u.protocol !== 'https:' || !u.hostname || u.username || u.password) throw new Error('Profile URL must be an HTTPS URL without credentials'); }
  }
  const stamp = now(); const pid = hexId();
  db.projects.push({ id: pid, name, purpose, created: stamp, updated: stamp, status: 'ready' });
  for (const item of inputs.slice(0, 12)) db.inputs.push({ id: nextId(), project_id: pid, kind: item.kind, value: cleanText(item.value, 500), selected: item.selected ? 1 : 0 });
  save(); return pid;
}
function download(name, type, text) { const a = document.createElement('a'); const u = URL.createObjectURL(new Blob([text], { type })); a.href = u; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(u), 1000); }

$('createBtn').addEventListener('click', () => {
  try { const pid = validateAndCreate(cleanText($('pname').value, 100), cleanText($('purpose').value, 1000), collectInputs()); selectProject(pid); $('createMsg').textContent = 'Projekt angelegt. Keine Suche wurde gestartet.'; }
  catch (e) { alert(e.message); }
});
$('exampleBtn').addEventListener('click', () => {
  $('pname').value = 'Example public-source verification'; $('purpose').value = 'Demonstrate the local project fields using an invented identity; do not search for a real person without appropriate purpose and authority.';
  $('name').value = 'Mira Exampleperson'; $('email').value = 'mira.exampleperson@example.invalid'; $('use-email').checked = false;
  $('username').value = 'mira-example-demo'; $('notes').value = 'Entirely fictional sample record.';
  $('createMsg').textContent = 'Fiktives Beispiel eingefüllt (examples/synthetic-project.json). Jetzt Bestätigungen setzen und „Projekt anlegen“.';
});
$('runBtn').addEventListener('click', async () => { if (!current) return; $('runStatus').textContent = 'Recherche läuft. Wikipedia-Abfragen werden extern übermittelt; bitte warten …'; await runResearch(current); });
$('deleteProjectBtn').addEventListener('click', () => {
  if (!current || !confirm('Projekt und zugehörige lokale Daten unwiderruflich löschen?')) return;
  const pid = current; const runIds = new Set(db.runs.filter(r => r.project_id === pid).map(r => r.id)); const hitIds = new Set(db.hits.filter(h => h.project_id === pid).map(h => h.id));
  db.projects = db.projects.filter(p => p.id !== pid); db.inputs = db.inputs.filter(i => i.project_id !== pid);
  db.runs = db.runs.filter(r => r.project_id !== pid); db.queries = db.queries.filter(q => !runIds.has(q.run_id));
  db.sources = db.sources.filter(s => s.project_id !== pid); db.hits = db.hits.filter(h => h.project_id !== pid);
  db.facts = db.facts.filter(f => f.project_id !== pid); db.reviews = db.reviews.filter(r => !hitIds.has(r.hit_id));
  db.contradictions = db.contradictions.filter(c => c.project_id !== pid); db.reports = db.reports.filter(r => r.project_id !== pid);
  current = null; save(); $('projectActions').classList.add('hidden'); $('noProjectHint').classList.remove('hidden');
  $('currentTitle').textContent = 'Wähle ein Projekt'; $('pstatus').textContent = ''; $('hits').textContent = 'Noch keine Treffer.'; $('queries').textContent = 'Noch keine Recherche ausgeführt.'; renderProjects();
});
function review(id, decision) {
  const note = prompt('Optionale Begründung (wird separat als Nutzerbewertung gespeichert):', '') || '';
  const hit = db.hits.find(h => h.id === id); if (!hit) return;
  hit.review = decision; db.reviews.push({ id: nextId(), hit_id: id, decision, note: cleanText(note, 1000), created: now() }); save(); renderProject(current);
}
function exportReport(fmt) {
  if (!current) return; const r = buildReport(current); if (!r) return;
  db.reports.push({ id: nextId(), project_id: current, version: db.reports.filter(x => x.project_id === current).length + 1, body: JSON.stringify(r), created: now() }); save();
  if (fmt === 'json') download('osint-report.json', 'application/json', JSON.stringify(r, null, 2));
  else download('osint-report.html', 'text/html', `<!doctype html><meta charset="utf-8"><title>OSINT-Bericht</title><h1>${esc(r.project.name)}</h1><p>${esc(r.summary)}</p><h2>Quellenbasierte Hinweise</h2>` + r.leads.map(x => `<article><h3>${esc(x.title)} — ${x.score}/100</h3><p>${esc(x.excerpt)}</p><p>${esc(x.status)} · ${esc(x.uncertainty)}</p><a href="${safeLink(x.url)}" rel="noopener noreferrer">${esc(x.url)}</a></article>`).join('') + `<h2>Einschränkungen</h2><ul>${r.limitations.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`);
}
$('reportJsonBtn').addEventListener('click', () => exportReport('json'));
$('reportHtmlBtn').addEventListener('click', () => exportReport('html'));
$('filter').addEventListener('change', () => current && renderHits());
$('sort').addEventListener('change', () => current && renderHits());
$('backupBtn').addEventListener('click', () => { const data = {}; TABLES.forEach(t => data[t] = db[t]); download('localosint-backup.json', 'application/json', JSON.stringify({ format: 'LocalOSINT backup v1', created: now(), data }, null, 2)); });
$('restoreBtn').addEventListener('click', () => $('restoreFile').click());
$('restoreFile').addEventListener('change', async e => {
  const f = e.target.files[0]; if (!f) return;
  if (f.size > 950000) { alert('Backup ist zu groß (max. 950 KB).'); e.target.value = ''; return; }
  if (!confirm('Wiederherstellen ersetzt alle derzeitigen Projektdaten. Fortfahren?')) { e.target.value = ''; return; }
  try {
    const payload = JSON.parse(await f.text());
    if (payload.format !== 'LocalOSINT backup v1' || typeof payload.data !== 'object') throw new Error('Ungültiges Backupformat');
    if (TABLES.some(t => !Array.isArray(payload.data[t] || [])) || TABLES.reduce((n, t) => n + (payload.data[t] || []).length, 0) > 10000) throw new Error('Backup enthält ungültige Daten oder zu viele Einträge');
    const fresh = emptyDb(); TABLES.forEach(t => fresh[t] = payload.data[t] || []);
    fresh.projects.forEach(p => { if (p.status === 'running') p.status = 'interrupted'; });
    // laufende Nummern sicher über die wiederhergestellten IDs setzen
    seq = 1 + TABLES.flatMap(t => fresh[t].map(r => r.id)).filter(x => Number.isInteger(x)).reduce((a, b) => Math.max(a, b), 0);
    db = fresh; current = null; save(); location.reload();
  } catch (err) { alert('Wiederherstellung fehlgeschlagen: ' + err.message); } finally { e.target.value = ''; }
});
$('eraseBtn').addEventListener('click', () => { if (prompt('Zur Bestätigung exakt ALLES LÖSCHEN eingeben:') === 'ALLES LÖSCHEN') { localStorage.removeItem(LS_KEY); db = emptyDb(); seq = 1; current = null; location.reload(); } });

renderProjects();

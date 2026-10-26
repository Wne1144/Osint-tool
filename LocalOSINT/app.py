#!/usr/bin/env python3
"""Local OSINT Research Suite — dependency-free, localhost-only MVP."""
from __future__ import annotations
import json, os, re, secrets, sqlite3, threading, time, webbrowser, urllib.parse, urllib.request, urllib.error, html
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = 'Local OSINT Research Suite'
HOST = '127.0.0.1'
PORT_START = 8765
MAX_BODY = 1_000_000
MAX_ITERATIONS = 2
ROOT = Path(__file__).resolve().parent


def default_data_dir():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LocalOSINT'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'localosint'


DATA = Path(os.environ.get('LOCALOSINT_DATA_DIR') or default_data_dir()).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'localosint.sqlite3'
TOKEN = secrets.token_urlsafe(32)


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def db_conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    with db_conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,purpose TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ready');
        CREATE TABLE IF NOT EXISTS inputs(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,kind TEXT NOT NULL,value TEXT NOT NULL,selected INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,started TEXT NOT NULL,finished TEXT,status TEXT NOT NULL,iteration INTEGER NOT NULL DEFAULT 1,summary TEXT);
        CREATE TABLE IF NOT EXISTS queries(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,adapter TEXT NOT NULL,query TEXT NOT NULL,status TEXT NOT NULL,detail TEXT,created TEXT NOT NULL,iteration INTEGER NOT NULL DEFAULT 1,derived_from TEXT,rationale TEXT);
        CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,url TEXT NOT NULL,title TEXT NOT NULL,source_type TEXT NOT NULL,origin TEXT NOT NULL,first_seen TEXT NOT NULL,UNIQUE(project_id,url));
        CREATE TABLE IF NOT EXISTS hits(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,title TEXT NOT NULL,snippet TEXT NOT NULL,match_kind TEXT NOT NULL,score INTEGER NOT NULL,score_json TEXT NOT NULL,uncertainties TEXT NOT NULL,review TEXT NOT NULL DEFAULT 'pending',created TEXT NOT NULL,UNIQUE(run_id,source_id));
        CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,hit_id INTEGER NOT NULL REFERENCES hits(id) ON DELETE CASCADE,claim TEXT NOT NULL,evidence TEXT NOT NULL,provenance TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,hit_id INTEGER NOT NULL REFERENCES hits(id) ON DELETE CASCADE,decision TEXT NOT NULL,note TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contradictions(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,hit_a INTEGER,hit_b INTEGER,description TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,version INTEGER NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
        ''')
        # Additive, data-preserving migrations for earlier MVP databases.
        existing={r['name'] for r in c.execute('PRAGMA table_info(queries)').fetchall()}
        for column,definition in [('iteration','INTEGER NOT NULL DEFAULT 1'),('derived_from','TEXT'),('rationale','TEXT')]:
            if column not in existing:c.execute(f'ALTER TABLE queries ADD COLUMN {column} {definition}')


def clean_text(v, limit=500):
    if not isinstance(v, str): return ''
    return re.sub(r'\s+', ' ', v).strip()[:limit]


def score_result(query, title, snippet, url):
    qtokens = {x.casefold() for x in re.findall(r'[\w@.-]+', query) if len(x) > 1}
    text = (title + ' ' + snippet).casefold()
    matched = sorted(t for t in qtokens if t in text)
    overlap = len(matched) / max(1, len(qtokens))
    exact_phrase = bool(query.strip()) and query.casefold() in text
    factors = {
        'text_overlap': round(overlap * 45),
        'exact_phrase': 20 if exact_phrase else 0,
        'source_quality': 15, # Official Wikimedia article, not identity evidence
        'specificity': min(10, len(matched) * 2),
        'independent_evidence': 0,
        'uncertainty_penalty': -10,
    }
    total = max(0, min(100, sum(factors.values())))
    return total, factors, matched


def wikipedia_search(query):
    params = urllib.parse.urlencode({'action':'query','list':'search','srsearch':query,'srlimit':'5','format':'json','utf8':'1'})
    url = 'https://en.wikipedia.org/w/api.php?' + params
    req = urllib.request.Request(url, headers={'User-Agent':'LocalOSINTResearchSuite/0.1 (local research tool; contact: none)', 'Accept':'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        if r.status != 200: raise RuntimeError(f'HTTP {r.status}')
        data = json.loads(r.read(1_000_000).decode('utf-8'))
    output=[]
    for item in data.get('query',{}).get('search',[]):
        title=clean_text(item.get('title',''),250)
        snippet=re.sub(r'<[^>]*>', ' ', item.get('snippet',''))
        snippet=html.unescape(clean_text(snippet,700))
        page='https://en.wikipedia.org/wiki/'+urllib.parse.quote(title.replace(' ','_'))
        output.append({'title':title,'snippet':snippet,'url':page,'type':'encyclopedia','origin':'Wikipedia MediaWiki public search API'})
    return output


def selected_search_terms(project_id):
    with db_conn() as c:
        rows=c.execute('SELECT kind,value FROM inputs WHERE project_id=? AND selected=1',(project_id,)).fetchall()
    terms=[]
    for r in rows:
        value=clean_text(r['value'],180)
        if r['kind'] in ('name','username','email') and value and value not in terms: terms.append(value)
    return terms[:8]


def run_research(project_id):
    run_id=secrets.token_hex(12)
    with db_conn() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        if not p: return
        c.execute("INSERT INTO runs(id,project_id,started,status,iteration) VALUES(?,?,?,'running',1)",(run_id,project_id,now()))
        c.execute("UPDATE projects SET status='running',updated=? WHERE id=?",(now(),project_id))
        c.commit()
    terms=selected_search_terms(project_id)
    if not terms:
        finish_run(run_id,project_id,'error','No selected name, username or email search term was supplied.')
        return
    status_rows=[]; inserted=0; seen_queries=set()
    for iteration in range(1, MAX_ITERATIONS+1):
        batch=[{'query':t,'derived_from':None,'rationale':'User-selected input term.'} for t in terms] if iteration==1 else []
        if iteration==2:
            # Iteration is deliberately conservative: only names of relevant, sourced article titles.
            with db_conn() as c:
                prior=c.execute('SELECT DISTINCT s.title,s.url FROM hits h JOIN sources s ON s.id=h.source_id WHERE h.run_id=? AND h.score>=60 ORDER BY h.score DESC LIMIT 2',(run_id,)).fetchall()
            batch=[{'query':r['title'],'derived_from':r['url'],'rationale':'Derived from a sourced result title with heuristic score >=60; this is a search lead, not an established fact.'} for r in prior if r['title'] and r['title'].casefold() not in seen_queries]
        if not batch: break
        for qitem in batch[:8]:
            query=clean_text(qitem['query'],180)
            if not query or query.casefold() in seen_queries: continue
            seen_queries.add(query.casefold())
            try:
                results=wikipedia_search(query)
                qstatus='success_with_hits' if results else 'success_no_hits'
                qdetail=f'{len(results)} result(s) from the Wikipedia public search API.'
            except Exception as e:
                results=[]; qstatus='technical_error'; qdetail=f'{type(e).__name__}: external source unavailable or request failed.'
            with db_conn() as c:
                c.execute('INSERT INTO queries(run_id,adapter,query,status,detail,created,iteration,derived_from,rationale) VALUES(?,?,?,?,?,?,?,?,?)',(run_id,'Wikipedia API',query,qstatus,qdetail,now(),iteration,qitem['derived_from'],qitem['rationale']))
                for result in results:
                    c.execute('INSERT OR IGNORE INTO sources(project_id,url,title,source_type,origin,first_seen) VALUES(?,?,?,?,?,?)',(project_id,result['url'],result['title'],result['type'],result['origin'],now()))
                    src=c.execute('SELECT id FROM sources WHERE project_id=? AND url=?',(project_id,result['url'])).fetchone()
                    score,factors,matched=score_result(query,result['title'],result['snippet'],result['url'])
                    uncertainty='Wikipedia search relevance is not evidence of identity. Names may refer to different people; snippets are incomplete and require review.'
                    cur=c.execute('INSERT OR IGNORE INTO hits(project_id,run_id,source_id,title,snippet,match_kind,score,score_json,uncertainties,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(project_id,run_id,src['id'],result['title'],result['snippet'],'name_or_text',score,json.dumps({'factors':factors,'matched_terms':matched,'query':query},ensure_ascii=False),uncertainty,now()))
                    if cur.rowcount:
                        inserted+=1
                        hit_id=cur.lastrowid
                        c.execute('INSERT INTO facts(project_id,hit_id,claim,evidence,provenance,created) VALUES(?,?,?,?,?,?)',(project_id,hit_id,result['title'],result['snippet'],'Search snippet from Wikipedia API; primary-source status not established.',now()))
                c.commit()
            status_rows.append(f'{query}: {qstatus}')
    summary=f'{inserted} new source result(s). ' + ('; '.join(status_rows) if status_rows else 'No query was executed.')
    finish_run(run_id,project_id,'completed',summary)


def finish_run(run_id,project_id,status,summary):
    with db_conn() as c:
        c.execute('UPDATE runs SET finished=?,status=?,summary=? WHERE id=?',(now(),status,summary,run_id))
        c.execute('UPDATE projects SET status=?,updated=? WHERE id=?',('ready' if status=='completed' else status,now(),project_id))
        c.commit()


def project_data(pid):
    with db_conn() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not p:return None
        out=dict(p)
        out['inputs']=[dict(r) for r in c.execute('SELECT kind,value,selected FROM inputs WHERE project_id=?',(pid,))]
        out['runs']=[dict(r) for r in c.execute('SELECT * FROM runs WHERE project_id=? ORDER BY started DESC',(pid,))]
        out['queries']=[dict(r) for r in c.execute('SELECT q.* FROM queries q JOIN runs r ON r.id=q.run_id WHERE r.project_id=? ORDER BY q.id DESC',(pid,))]
        out['hits']=[dict(r) for r in c.execute('SELECT h.*,s.url,s.origin,s.source_type FROM hits h JOIN sources s ON s.id=h.source_id WHERE h.project_id=? ORDER BY h.score DESC,h.created DESC',(pid,))]
        out['reviews']=[dict(r) for r in c.execute('SELECT rv.* FROM reviews rv JOIN hits h ON h.id=rv.hit_id WHERE h.project_id=? ORDER BY rv.created DESC',(pid,))]
        return out


def build_report(pid):
    d=project_data(pid)
    if not d:return None
    hits=d['hits']
    facts=[h for h in hits if h['score']>=60 and h['review']=='matched']
    sections={
      'summary':'This report contains public-source search leads, not an identity determination.',
      'project':{'name':d['name'],'purpose':d['purpose'],'created':d['created'],'report_created':now(),'scope':'Wikipedia public search API only; searches reflect only selected inputs.'},
      'sections':{'input_and_scope':'See project metadata and selected inputs.','public_profiles_and_websites':'Only results listed in leads; no profile ownership verified.','professional_background':'Not independently investigated in this MVP.','public_contact_information':'Not independently investigated in this MVP.','source_links':'See leads and source register.','timeline':'Only retrieval timestamps are available; no biographical timeline inferred.','contradictions':[],'open_questions':['Are any leads about the same subject? Human assessment required.'],'tools_and_time':'LocalOSINT MVP using Wikipedia MediaWiki public search API; timestamps are UTC.','unavailable_sources':['General web search provider APIs, social profile APIs, image/face search, and additional archives are not integrated.']},
      'confirmed_facts':[], 'leads':[], 'limitations':['Search results and name matches do not confirm identity.','No face recognition, login attempts, breach-data searches, or paid services are used.','A technical failure or zero results does not establish that information does not exist.'],
      'sources':[], 'runs':d['runs'],'queries':d['queries']}
    for h in hits:
        item={'status':('User-reviewed: matched' if h['review']=='matched' else 'Lead — human review required'),'title':h['title'],'excerpt':h['snippet'],'url':h['url'],'score':h['score'],'score_components':json.loads(h['score_json']),'uncertainty':h['uncertainties'],'review':h['review']}
        sections['leads'].append(item)
        if h['review']=='matched': sections['confirmed_facts'].append({'claim':h['title'],'evidence':h['snippet'],'source':h['url'],'qualification':'User-reviewed assessment; not an objective identity proof.'})
        sections['sources'].append({'title':h['title'],'url':h['url'],'origin':h['origin'],'retrieved':h['created']})
    return sections


HTML_PAGE = r'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>Local OSINT Research Suite</title><style>
:root{--ink:#172526;--muted:#647678;--teal:#087f76;--line:#dbe5e3;--paper:#f4f7f6;--card:#fff;--amber:#8b5d06;--red:#963f3f}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}header{padding:19px max(22px,calc((100vw - 1180px)/2));background:#102c2d;color:#f6fffd;display:flex;justify-content:space-between;align-items:center}header h1{font-size:20px;margin:0}header span{font-size:12px;color:#b2d2ce}.wrap{max-width:1180px;margin:25px auto;padding:0 20px}.notice{background:#fff7e7;border:1px solid #ead4a3;padding:12px 15px;border-radius:10px;color:#59420e;margin-bottom:18px}.grid{display:grid;grid-template-columns:360px 1fr;gap:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 4px 16px #15333308;margin-bottom:16px}h2{font-size:17px;margin:0 0 14px}h3{font-size:15px;margin:0 0 8px}label{display:block;font-size:13px;font-weight:650;margin:11px 0 4px}input,textarea,select{font:inherit;width:100%;padding:10px 11px;border:1px solid #cbd8d6;border-radius:8px;background:white;color:var(--ink)}textarea{min-height:73px;resize:vertical}.check{display:flex;align-items:flex-start;gap:9px;font-weight:450}.check input{width:auto;margin-top:5px}button,.btn{border:0;border-radius:8px;background:var(--teal);color:white;font-weight:650;padding:10px 14px;font:inherit;cursor:pointer}button:hover{filter:brightness(.92)}button.secondary{background:#e9f1ef;color:#23514e}button.danger{background:#9e4545}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.small{font-size:12px;color:var(--muted)}.project{display:flex;gap:10px;align-items:center;padding:10px;border:1px solid var(--line);border-radius:9px;margin:7px 0;cursor:pointer}.project.active{border-color:var(--teal);background:#eff9f7}.badge{font-size:11px;padding:3px 7px;border-radius:99px;background:#edf3f2;color:#3a5654}.hit{padding:14px 0;border-top:1px solid var(--line)}.hit:first-child{border-top:0}.hit a{color:#116c66;font-weight:700}.score{font-size:12px;border-radius:50px;padding:3px 9px;background:#e6f4ef}.score.low{background:#f0f1ee}.hit p{margin:7px 0;color:#415453}.details{font-size:12px;color:var(--muted);margin:7px 0}.hidden{display:none!important}.status{font-size:13px;color:var(--muted);margin:8px 0}.footer{margin:20px 0;color:var(--muted);font-size:12px}@media(max-width:850px){.grid{grid-template-columns:1fr}header{align-items:flex-start;gap:6px;flex-direction:column}}
</style></head><body><header><h1>Local OSINT Research Suite</h1><span>Lokale Anwendung · Daten bleiben grundsätzlich auf diesem Gerät</span></header><main class="wrap"><div class="notice"><b>Wichtiger Hinweis:</b> MVP für zulässige, verhältnismäßige Recherche. Bei einer Suche werden die ausgewählten Begriffe an die öffentliche Wikipedia-Such-API übermittelt. Treffer sind Hinweise, keine Identitätsbestätigung. Keine Gesichtssuche.</div><div class="grid"><aside><section class="card"><h2>Neues Rechercheprojekt</h2><label>Projektname</label><input id="pname" placeholder="z. B. Quellenprüfung"><label>Zulässiger Zweck</label><textarea id="purpose" placeholder="Konkreter, notwendiger Zweck"></textarea><label>Vorname und Nachname</label><input id="name" placeholder="Name (optional)"><label class="check"><input type="checkbox" id="use-name" checked><span>Name als Suchbegriff verwenden</span></label><label>E-Mail-Adresse</label><input id="email" placeholder="name@example.org"><label class="check"><input type="checkbox" id="use-email" checked><span>E-Mail als Suchbegriff verwenden</span></label><label>Benutzername / Handle</label><input id="username" placeholder="@handle"><label class="check"><input type="checkbox" id="use-username" checked><span>Handle als Suchbegriff verwenden</span></label><label>Öffentliche Profil-URL (nur dokumentiert, keine Abrufprüfung)</label><input id="profile" placeholder="https://…"><label>Zusätzliche Hinweise (lokal, nicht an Suchdienst gesendet)</label><textarea id="notes"></textarea><label class="check"><input type="checkbox" id="consent"><span>Ich bestätige einen zulässigen Zweck und erforderliche Berechtigungen. Mir ist bewusst, dass dies keine rechtliche Prüfung ersetzt.</span></label><label class="check"><input type="checkbox" id="external"><span>Ich willige ein, ausgewählte Namen, E-Mail oder Handles an Wikipedia für die öffentliche Suche zu übermitteln.</span></label><div style="margin-top:13px"><button onclick="createProject()">Projekt anlegen</button></div><div class="small" id="createMsg"></div></section><section class="card"><h2>Projekte</h2><div id="projects"></div><div class="row" style="margin-top:12px"><button class="secondary" onclick="backup()">Backup herunterladen</button><button class="secondary" onclick="document.getElementById('restoreFile').click()">Backup wiederherstellen</button><input id="restoreFile" type="file" accept="application/json,.json" class="hidden" onchange="restoreBackup(this)"><button class="danger" onclick="eraseAll()">Alle Daten löschen</button></div></section><section class="card"><h2>Quellen & Grenzen</h2><div class="small"><b>Wikipedia MediaWiki API</b> — kostenlos, ohne Schlüssel, automatisiert; öffentliche Enzyklopädie-Suche, begrenzte Aussagekraft, Anbieterbedingungen beachten. API-Abfrage nur nach Nutzeraktion. Kein Anspruch auf Vollständigkeit.<br><br><b>Manuelle Suche</b> — wird als Suchlink angeboten; der Nutzer verlässt die lokale App.<br><b>PimEyes / Gesichtssuche</b> — nicht integriert; kein Upload/Biometrie.<br><br>Keine externe KI, keine Scraping-Umgehung, keine geleakten Datenbanken.</div></section></aside><div><section class="card"><div class="row" style="justify-content:space-between"><h2 id="currentTitle">Wähle ein Projekt</h2><span class="badge" id="pstatus"></span></div><div id="projectActions" class="hidden"><div id="runSummary" class="small"></div><div class="row" style="margin:12px 0"><button onclick="runResearch()">Recherche starten</button><button class="secondary" onclick="exportReport('json')">JSON-Bericht</button><button class="secondary" onclick="exportReport('html')">HTML-Bericht</button><button class="danger" onclick="deleteProject()">Projekt löschen</button></div><div id="runStatus" class="status"></div></div></section><section class="card"><h2>Treffer & menschliche Prüfung</h2><div class="row"><select id="filter" onchange="renderHits()"><option value="all">Alle Treffer</option><option value="pending">Prüfung ausstehend</option><option value="matched">Als passend markiert</option><option value="low">Score unter 40</option></select><select id="sort" onchange="renderHits()"><option value="score">Score absteigend</option><option value="new">Neueste</option></select></div><div id="hits" style="margin-top:12px" class="small">Noch keine Treffer.</div></section><section class="card"><h2>Lokaler Datenschutz</h2><div class="small">SQLite-Datenbank: <code id="dataPath">wird ermittelt</code>. Backups enthalten personenbezogene Projektdaten und sollten geschützt aufbewahrt werden. Projektlöschung und vollständige Löschung sind verfügbar. Export und Recherche nur nach Nutzeraktion.</div></section></div></div><p class="footer">Local OSINT Research Suite · MVP 0.1 · Keine Rechtsberatung · Relevanzscore ist keine Identitätswahrscheinlichkeit.</p></main><script>
const TOKEN='__TOKEN__';let current=null,projects=[],currentHits=[];async function api(path,method='GET',body){const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-LocalOSINT-Token':TOKEN},body:body?JSON.stringify(body):undefined,cache:'no-store'});if(!r.ok){let e={};try{e=await r.json()}catch{}throw Error(e.error||`HTTP ${r.status}`)}return r.status===204?null:r.json()}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function safeLink(s){try{let u=new URL(s);return (u.protocol==='https:'||u.protocol==='http:')?esc(u.href):'#'}catch{return '#'}}function urlsafe(s){return encodeURIComponent(s)}async function refresh(){projects=await api('/projects');document.getElementById('projects').innerHTML=projects.length?projects.map(p=>`<div class="project ${p.id===current?'active':''}" onclick="selectProject('${p.id}')"><div style="flex:1"><b>${esc(p.name)}</b><div class="small">${esc(p.status)} · ${esc(p.created.slice(0,10))}</div></div><span class="badge">${p.hit_count} Treffer</span></div>`).join(''):'<div class="small">Noch keine Projekte.</div>';if(current)await selectProject(current)}async function createProject(){let name=document.getElementById('pname').value.trim(),purpose=document.getElementById('purpose').value.trim();if(!name||!purpose){alert('Projektname und Zweck sind erforderlich.');return}if(!document.getElementById('consent').checked){alert('Bitte bestätige den zulässigen Zweck und die Berechtigung.');return}let inputs=[];for(let [id,kind,useId] of [['name','name','use-name'],['email','email','use-email'],['username','username','use-username'],['profile','profile_url',null],['notes','notes',null]]){let value=document.getElementById(id).value.trim();if(value)inputs.push({kind,value,selected:useId?document.getElementById(useId).checked:false})}if(inputs.some(x=>x.selected&&['name','email','username'].includes(x.kind))&&!document.getElementById('external').checked){alert('Für externe Recherche bitte die gesonderte Übermittlungsbestätigung aktivieren; alternativ erfasse nur Hinweise ohne Suchbegriff.');return}try{let p=await api('/projects','POST',{name,purpose,inputs,lawful_ack:true,external_ack:document.getElementById('external').checked});current=p.id;await refresh()}catch(e){alert(e.message)}}async function selectProject(id){current=id;let d=await api('/projects/'+id);document.getElementById('currentTitle').textContent=d.name;document.getElementById('pstatus').textContent=d.status;document.getElementById('projectActions').classList.remove('hidden');document.getElementById('runSummary').textContent='Zweck: '+d.purpose+' · '+d.inputs.map(x=>x.kind+': '+x.value+(x.selected?'':' (nicht für Suche ausgewählt)')).join(' · ');document.getElementById('dataPath').textContent=d.data_dir;document.getElementById('runStatus').textContent=d.runs.length?d.runs.map(x=>`${x.started}: ${x.status} — ${x.summary||''}`).join('\n'):'';renderHits(d.hits);await refreshListOnly(d)}async function refreshListOnly(d){projects=await api('/projects');document.getElementById('projects').innerHTML=projects.length?projects.map(p=>`<div class="project ${p.id===current?'active':''}" onclick="selectProject('${p.id}')"><div style="flex:1"><b>${esc(p.name)}</b><div class="small">${esc(p.status)} · ${esc(p.created.slice(0,10))}</div></div><span class="badge">${p.hit_count} Treffer</span></div>`).join(''):'<div class="small">Noch keine Projekte.</div>'}function renderHits(hits){if(hits)currentHits=hits;hits=currentHits;let f=document.getElementById('filter').value;if(f==='pending')hits=hits.filter(h=>h.review==='pending');if(f==='matched')hits=hits.filter(h=>h.review==='matched');if(f==='low')hits=hits.filter(h=>h.score<40);if(document.getElementById('sort').value==='new')hits=[...hits].reverse();let el=document.getElementById('hits');if(!hits.length){el.innerHTML='<div class="small">Keine passenden Treffer. Beachte: Das bedeutet nicht, dass Informationen nicht existieren.</div>';return}el.innerHTML=hits.map(h=>{let sc=JSON.parse(h.score_json);return `<article class="hit"><div class="row"><a target="_blank" rel="noopener noreferrer" href="${safeLink(h.url)}">${esc(h.title)}</a><span class="score ${h.score<40?'low':''}">Score ${h.score}/100</span><span class="badge">${esc(h.review)}</span></div><p>${esc(h.snippet)}</p><div class="details"><b>Bewertung:</b> ${esc(JSON.stringify(sc.factors))}<br><b>Unsicherheit:</b> ${esc(h.uncertainties)}<br><b>Quelle:</b> ${esc(h.origin)} · ${esc(h.created)}</div><div class="row"><button class="secondary" onclick="review(${h.id},'matched')">Passend</button><button class="secondary" onclick="review(${h.id},'not_matching')">Nicht passend</button><button class="secondary" onclick="review(${h.id},'unclear')">Unklar</button><button class="secondary" onclick="review(${h.id},'more_evidence')">Weitere Belege</button><button class="secondary" onclick="manualSearch('${urlsafe(h.title)}')">Manuell weitersuchen</button></div></article>`}).join('')}async function runResearch(){if(!current)return;try{let r=await api('/projects/'+current+'/run','POST',{});document.getElementById('runStatus').textContent='Recherche läuft. Wikipedia-Abfragen werden extern übermittelt; bitte warten …';await poll()}catch(e){alert(e.message)}}async function poll(){for(let i=0;i<90;i++){await new Promise(r=>setTimeout(r,900));let d=await api('/projects/'+current);if(!d.runs.length||d.runs[0].status!=='running'){document.getElementById('runStatus').textContent=d.runs.map(x=>`${x.status}: ${x.summary||''}`).join('\n');renderHits(d.hits);await refreshListOnly(d);return}}document.getElementById('runStatus').textContent='Recherche dauert länger; Status wird beim Aktualisieren angezeigt.'}async function review(id,decision){let note=prompt('Optionale Begründung (wird separat als Nutzerbewertung gespeichert):','')||'';try{await api('/hits/'+id+'/review','POST',{decision,note});await selectProject(current)}catch(e){alert(e.message)}}function download(name,type,text){let a=document.createElement('a'),u=URL.createObjectURL(new Blob([text],{type}));a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}async function exportReport(fmt){let r=await api('/projects/'+current+'/report');if(fmt==='json')download('osint-report.json','application/json',JSON.stringify(r,null,2));else{let h=`<!doctype html><meta charset="utf-8"><title>OSINT-Bericht</title><h1>${esc(r.project.name)}</h1><p>${esc(r.summary)}</p><h2>Quellenbasierte Hinweise</h2>`+r.leads.map(x=>`<article><h3>${esc(x.title)} — ${x.score}/100</h3><p>${esc(x.excerpt)}</p><p>${esc(x.status)} · ${esc(x.uncertainty)}</p><a href="${safeLink(x.url)}" rel="noopener noreferrer">${esc(x.url)}</a></article>`).join('')+`<h2>Einschränkungen</h2><ul>${r.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;download('osint-report.html','text/html',h)}}async function deleteProject(){if(confirm('Projekt und zugehörige lokale Daten unwiderruflich löschen?')){await api('/projects/'+current,'DELETE');current=null;document.getElementById('projectActions').classList.add('hidden');document.getElementById('currentTitle').textContent='Wähle ein Projekt';document.getElementById('hits').textContent='Noch keine Treffer.';await refresh()}}async function eraseAll(){if(prompt('Zur Bestätigung exakt ALLES LÖSCHEN eingeben:')==='ALLES LÖSCHEN'){await api('/erase','POST',{});current=null;location.reload()}}async function backup(){let data=await api('/backup');download('localosint-backup.json','application/json',JSON.stringify(data,null,2))}async function restoreBackup(el){let f=el.files[0];if(!f)return;if(f.size>950000){alert('Backup ist zu groß (max. 950 KB).');el.value='';return}if(!confirm('Wiederherstellen ersetzt alle derzeitigen Projektdaten. Fortfahren?')){el.value='';return}try{let payload=JSON.parse(await f.text());await api('/restore','POST',payload);current=null;alert('Backup wurde wiederhergestellt.');location.reload()}catch(e){alert('Wiederherstellung fehlgeschlagen: '+e.message)}finally{el.value=''}}function manualSearch(q){window.open('https://www.google.com/search?q='+q,'_blank','noopener')}refresh().catch(e=>alert(e.message));
</script></body></html>'''

class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True

class Handler(BaseHTTPRequestHandler):
    server_version='LocalOSINT/0.1'
    def log_message(self,fmt,*args):
        # Local minimal log only; never log request bodies/query strings.
        try:
            with open(DATA/'app.log','a',encoding='utf8') as f:f.write(f'{now()} {self.address_string()} {fmt%args}\n')
        except OSError: pass
    def send_json(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
    def safe(self):
        host=self.headers.get('Host','')
        if not re.fullmatch(r'(127\.0\.0\.1|localhost):\d+',host): return False
        origin=self.headers.get('Origin')
        if origin and not re.fullmatch(r'http://(127\.0\.0\.1|localhost):\d+',origin): return False
        if self.path.startswith('/api/') and self.headers.get('X-LocalOSINT-Token')!=TOKEN:return False
        return True
    def do_GET(self):
        if not self.safe(): return self.send_json({'error':'Request denied'},403)
        path=urllib.parse.urlsplit(self.path).path
        if path=='/':
            raw=HTML_PAGE.replace('__TOKEN__',TOKEN).encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if not path.startswith('/api/'):return self.send_json({'error':'Not found'},404)
        if path=='/api/projects':
            with db_conn() as c: rows=c.execute('SELECT p.*,COUNT(h.id) hit_count FROM projects p LEFT JOIN hits h ON h.project_id=p.id GROUP BY p.id ORDER BY p.updated DESC').fetchall()
            return self.send_json([dict(r) for r in rows])
        if path.startswith('/api/projects/'):
            bits=path.split('/'); pid=bits[3]
            if len(bits)==4:
                d=project_data(pid)
                if d:d['data_dir']=str(DATA)
                return self.send_json(d or {'error':'Project not found'},200 if d else 404)
            if len(bits)==5 and bits[4]=='report':
                report=build_report(pid)
                if report is None:return self.send_json({'error':'Project not found'},404)
                with db_conn() as c:
                    ver=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM reports WHERE project_id=?',(pid,)).fetchone()[0]
                    c.execute('INSERT INTO reports(project_id,version,body,created) VALUES(?,?,?,?)',(pid,ver,json.dumps(report,ensure_ascii=False),now()))
                return self.send_json(report)
        if path=='/api/backup':
            with db_conn() as c:
                tables=['projects','inputs','runs','queries','sources','hits','facts','reviews','contradictions','reports']; data={}
                for t in tables:data[t]=[dict(r) for r in c.execute('SELECT * FROM '+t)]
            return self.send_json({'format':'LocalOSINT backup v1','created':now(),'data':data})
        return self.send_json({'error':'Not found'},404)
    def read_body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>MAX_BODY: raise ValueError('Request too large')
        raw=self.rfile.read(n)
        return json.loads(raw.decode('utf-8')) if raw else {}
    def do_POST(self):
        if not self.safe():return self.send_json({'error':'Request denied'},403)
        try: body=self.read_body()
        except Exception:return self.send_json({'error':'Invalid or oversized request'},400)
        path=urllib.parse.urlsplit(self.path).path
        if path=='/api/projects':
            name=clean_text(body.get('name'),100); purpose=clean_text(body.get('purpose'),1000); inputs=body.get('inputs',[])
            if body.get('lawful_ack') is not True:return self.send_json({'error':'Bestätigung des zulässigen Zwecks erforderlich'},400)
            if not isinstance(inputs,list):return self.send_json({'error':'Invalid inputs'},400)
            if any(isinstance(x,dict) and x.get('selected') and x.get('kind') in ('name','email','username') for x in inputs) and body.get('external_ack') is not True:return self.send_json({'error':'Externe Übermittlung muss ausdrücklich bestätigt werden'},400)
            if not name or not purpose or not isinstance(inputs,list) or len(inputs)>12:return self.send_json({'error':'Project name, purpose and valid input list required'},400)
            pid=secrets.token_hex(12); stamp=now()
            allowed={'name','email','username','profile_url','notes'}
            with db_conn() as c:
                c.execute('INSERT INTO projects(id,name,purpose,created,updated) VALUES(?,?,?,?,?)',(pid,name,purpose,stamp,stamp))
                for item in inputs:
                    if not isinstance(item,dict) or item.get('kind') not in allowed:continue
                    val=clean_text(item.get('value'),500)
                    if not val:continue
                    if item['kind']=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',val):return self.send_json({'error':'Invalid email format'},400)
                    if item['kind']=='profile_url':
                        u=urllib.parse.urlsplit(val)
                        if u.scheme!='https' or not u.hostname or u.username or u.password:return self.send_json({'error':'Profile URL must be an HTTPS URL without credentials'},400)
                    c.execute('INSERT INTO inputs(project_id,kind,value,selected) VALUES(?,?,?,?)',(pid,item['kind'],val,1 if item.get('selected') else 0))
                c.commit()
            return self.send_json({'id':pid},201)
        if path.startswith('/api/projects/') and path.endswith('/run'):
            pid=path.split('/')[3]
            with db_conn() as c:
                p=c.execute('SELECT status FROM projects WHERE id=?',(pid,)).fetchone()
                if not p:return self.send_json({'error':'Project not found'},404)
                if p['status']=='running':return self.send_json({'error':'Research already running'},409)
            threading.Thread(target=run_research,args=(pid,),daemon=True).start()
            return self.send_json({'status':'running'},202)
        if path.startswith('/api/hits/') and path.endswith('/review'):
            try:hid=int(path.split('/')[3])
            except:return self.send_json({'error':'Invalid hit id'},400)
            decision=body.get('decision'); note=clean_text(body.get('note'),1000)
            if decision not in ('matched','not_matching','unclear','more_evidence'):return self.send_json({'error':'Invalid decision'},400)
            with db_conn() as c:
                if not c.execute('SELECT 1 FROM hits WHERE id=?',(hid,)).fetchone():return self.send_json({'error':'Hit not found'},404)
                c.execute('UPDATE hits SET review=? WHERE id=?',(decision,hid));c.execute('INSERT INTO reviews(hit_id,decision,note,created) VALUES(?,?,?,?)',(hid,decision,note,now()))
            return self.send_json({'ok':True})
        if path=='/api/restore':
            if body.get('format')!='LocalOSINT backup v1' or not isinstance(body.get('data'),dict):return self.send_json({'error':'Ungültiges Backupformat'},400)
            columns={
              'projects':['id','name','purpose','created','updated','status'],
              'inputs':['id','project_id','kind','value','selected'],
              'runs':['id','project_id','started','finished','status','iteration','summary'],
              'queries':['id','run_id','adapter','query','status','detail','created','iteration','derived_from','rationale'],
              'sources':['id','project_id','url','title','source_type','origin','first_seen'],
              'hits':['id','project_id','run_id','source_id','title','snippet','match_kind','score','score_json','uncertainties','review','created'],
              'facts':['id','project_id','hit_id','claim','evidence','provenance','created'],
              'reviews':['id','hit_id','decision','note','created'],
              'contradictions':['id','project_id','hit_a','hit_b','description','created'],
              'reports':['id','project_id','version','body','created']}
            data=body['data']
            if any(not isinstance(data.get(t,[]),list) for t in columns) or sum(len(data.get(t,[])) for t in columns)>10000:return self.send_json({'error':'Backup enthält ungültige Daten oder zu viele Einträge'},400)
            try:
                with db_conn() as c:
                    c.execute('DELETE FROM projects')
                    for table,cols in columns.items():
                        rows=data.get(table,[])
                        if any(not isinstance(row,dict) or not set(row).issubset(set(cols)) or any(k not in row for k in cols) for row in rows):raise ValueError('Invalid backup row')
                        marks=','.join('?' for _ in cols); names=','.join(cols)
                        for row in rows:
                            vals=[row[k] for k in cols]
                            if table=='projects':vals[-1]='ready'
                            if table=='runs' and vals[4]=='running':vals[4]='interrupted'
                            c.execute(f'INSERT INTO {table}({names}) VALUES({marks})',vals)
                    c.commit()
            except Exception:return self.send_json({'error':'Backup konnte nicht eingespielt werden; vorhandene Daten wurden nicht verändert'},400)
            return self.send_json({'ok':True})
        if path=='/api/erase':
            with db_conn() as c:
                c.execute('DELETE FROM projects');c.commit()
                c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                c.execute('VACUUM')
            try:(DATA/'app.log').unlink(missing_ok=True)
            except OSError:pass
            return self.send_json({'ok':True})
        return self.send_json({'error':'Not found'},404)
    def do_DELETE(self):
        if not self.safe():return self.send_json({'error':'Request denied'},403)
        path=urllib.parse.urlsplit(self.path).path
        if path.startswith('/api/projects/'):
            pid=path.split('/')[3]
            with db_conn() as c:c.execute('DELETE FROM projects WHERE id=?',(pid,));c.commit()
            self.send_response(204);self.end_headers();return
        return self.send_json({'error':'Not found'},404)

def main():
    init_db(); server=None
    for port in range(PORT_START,PORT_START+20):
        try:server=Server((HOST,port),Handler);break
        except OSError:continue
    if server is None:raise SystemExit('Kein freier localhost-Port im Bereich 8765–8784.')
    url=f'http://127.0.0.1:{server.server_port}/'
    print(f'{APP_NAME} läuft lokal: {url}\nDaten: {DATA}\nBeenden: Ctrl+C')
    threading.Timer(.8,lambda:webbrowser.open(url)).start()
    try:server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:print('\nWird beendet …')
    finally:server.server_close()

if __name__=='__main__':main()

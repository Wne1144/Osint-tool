# Analyse der vorliegenden Software

Stand der Analyse: 2026-10-26. Grundlage sind die hochgeladenen Dateien
(`app.py`, `tests/test_app.py`, `README.md`, `BUILD_STATUS.md`, Starter-Skripte,
Beispiel-JSON), die im Repo unter `LocalOSINT/` ohne die `.txt`-Upload-Endungen
wiederhergestellt wurden.

## Was die Software ist

**Local OSINT Research Suite (MVP 0.1)** — ein lokales Recherche-Notizbuch für
zulässige, verhältnismäßige OSINT-Recherche. Ausschließlich Python-Standardbibliothek
(`http.server`, `sqlite3`, `urllib`), keine pip-Pakete, kein Node/Docker.

Architektur der Python-Version:

| Bauteil | Umsetzung in `app.py` |
|---|---|
| Server | `ThreadingHTTPServer`, bindet **nur** `127.0.0.1`, Ports 8765–8784 |
| UI | Eine eingebettete HTML-Seite (`HTML_PAGE`) mit Inline-CSS/JS, wird vom Server ausgeliefert |
| Daten | SQLite (`localosint.sqlite3`, WAL) außerhalb des Programmordners (`%LOCALAPPDATA%\LocalOSINT` bzw. `~/.local/share/localosint`, Override per `LOCALOSINT_DATA_DIR`) |
| Sicherheit | Per-Prozess-Zufallstoken (`X-LocalOSINT-Token`) für alle API-Calls, Host-/Origin-Check, Body-Limit 1 MB, parametrisiertes SQL, restriktive CSP, Escaping, keine Ausführung abgerufener Inhalte |
| Recherche | Wikipedia MediaWiki Search API (`action=query&list=search`, 5 Treffer/Abfrage, 10 s Timeout), max. 8 Anfangsabfragen, max. 2 Iterationen (2. Iteration nur mit Titeln von Treffern mit Score ≥ 60) |
| Score | Heuristik 0–100: Token-Overlap 45 + exakte Phrase 20 + Quellenbeitrag 15 + Spezifität ≤ 10 + unabhängige Belege 0 − Unsicherheit 10. **Keine** Identitätswahrscheinlichkeit |
| Workflow | Projekt + Zweckauswahl anlegen → Recherche starten → Treffer mit Score-Faktoren/Unsicherheit prüfen → manuelles Review (`matched`/`not_matching`/`unclear`/`more_evidence`) → HTML-/JSON-Report → Backup/Restore/Löschung |
| Tests | 7 Unittests (Score, Dedup, Cascade-Delete, gemockter Adapter, Token-Gate, Backup-Roundtrip, Iterationsdeckel) — lokal erneut ausgeführt: **7/7 OK** (Python 3.11) |

Bewusste Grenzen (laut README/BUILD_STATUS, im Code bestätigt): kein allgemeiner
Web-Search-Adapter, keine Social-/E-Mail-Anreicherung, keine Bild-/Gesichtssuche,
keine Verifikation von Profil-URLs, keine Volltext-Auswertung, keine
selbstständigen Binaries (Python 3.10+ muss installiert sein), kein
Clean-Windows-/Raspberry-Pi-Test, keine Rechts-/Security-Zertifizierung.

## Was daraus als Website gebaut wurde (`docs/`)

GitHub Pages kann **kein Python und keinen Server** ausführen. Die Website ist
daher ein abhängigkeitsfreier Client-Port derselben Logik in Vanilla-JS:

| Python-Version | Web-Version (`docs/index.html` + `app.js` + `style.css`) |
|---|---|
| `http.server` auf 127.0.0.1 + Token-Gate | Statische Seite, kein Server → Token/Host-Check entfällt; dafür verlässt nichts außer den bestätigten Suchbegriffen das Gerät |
| SQLite im Datenverzeichnis | `localStorage` (`localosint.web.v1`), gleiche Tabellenstruktur (`projects`, `inputs`, `runs`, `queries`, `sources`, `hits`, `facts`, `reviews`, `reports`) |
| `wikipedia_search()` per `urllib` (serverseitig) | `fetch()` direkt an `en.wikipedia.org/w/api.php` mit `origin=*` (CORS), gleicher Endpoint, `srlimit=5`, 10-s-Timeout per `AbortController` |
| `score_result()` | `scoreResult()` — Faktoren identisch (45/20/15/10/0/−10, Clamp 0–100) |
| `run_research()`, 2 Iterationen, Dedup über `UNIQUE(project_id,url)` / `UNIQUE(run_id,source_id)` | `runResearch()` — gleiche Iterations-, Dedup- und Statuslogik (`success_with_hits`, `success_no_hits`, `technical_error`) |
| `build_report()` | `buildReport()` — gleiche Reportstruktur, JSON-/HTML-Export |
| Backup `LocalOSINT backup v1` | Gleiches Backup-Format; Web-Backups sind mit der Python-Version strukturkompatibel |
| Token, CSP, Host-Check (Server-Schutz) | Nicht nötig (kein Server); XSS-Escaping (`esc`, `safeLink`) wie im Original übernommen |

Zusätzlich: Button „Beispiel laden“ füllt das fiktive Beispiel aus
`examples/synthetic-project.json` ein; Abfrage-/Laufprotokoll wird sichtbar
angezeigt (war in der Python-UI nur Teil des Projekt-JSONs).

**Wichtig:** Auch in der Web-Version gilt — Treffer sind Leads, keine
Identitätsbestätigung. Daten liegen unverschlüsselt im Browser-Profil; für
sensible Fälle die lokale Python-Version oder verschlüsselte Backups nutzen.

## Veröffentlichung über GitHub

1. `.github/workflows/pages.yml` deployt `docs/` per GitHub Actions nach `main`
   (oder per *Actions → Deploy website to GitHub Pages → Run workflow*).
2. Alternativ ohne Workflow: *Repo → Settings → Pages → Source: Deploy from a
   branch → Branch `main`, Ordner `/docs`*.
3. Ergebnis-URL: `https://wne1144.github.io/Osint-tool/`

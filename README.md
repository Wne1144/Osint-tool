# Osint-tool — Local OSINT Research Suite

Lokales Recherche-Notizbuch für zulässige, verhältnismäßige OSINT-Recherche:
Projekte mit Zweckbindung, Wikipedia-Suche (öffentliche MediaWiki-API),
transparenter Relevanz-Score, menschliches Review, HTML-/JSON-Report,
Backup/Wiederherstellung und vollständige Löschung.

> ⚠️ Treffer sind **Leads, keine Identitätsbestätigung**. Der Score (0–100) ist
> eine offengelegte Heuristik, keine Wahrscheinlichkeit. Keine Gesichtssuche,
> kein Scraping, keine geleakten Datenbanken, keine externe KI. Details und
> Analyse der ursprünglichen Software: [`ANALYSIS.md`](ANALYSIS.md).

## 🌐 Website (GitHub Pages)

Die Software wurde als **statische Website** portiert (kein Server, keine
Abhängigkeiten — Daten bleiben im Browser):

- **Live (nach Aktivierung):** https://wne1144.github.io/Osint-tool/
- **Code:** [`docs/`](docs/) (`index.html`, `app.js`, `style.css`)
- **Deploy:** [`.github/workflows/pages.yml`](.github/workflows/pages.yml)
  deployt `docs/` bei jedem Push auf `main` (oder manuell per
  *Actions → Run workflow*). Alternativ: *Settings → Pages → Deploy from a
  branch → `main` / `/docs`*.
- **Lokal ansehen:** `python3 -m http.server --directory docs 8000`
  → http://127.0.0.1:8000/

Unterschiede zur Python-Version: `localStorage` statt SQLite, Wikipedia-Abruf
direkt aus dem Browser (`origin=*`), kein Loopback-Token nötig (kein Server).
Score, Iterationen (max. 2), Review, Report- und Backup-Logik sind identisch
portiert; Backups nutzen dasselbe `LocalOSINT backup v1`-Format.

## 🖥️ Original (Python, lokal/offline)

Die unveränderte Ausgangssoftware liegt unter [`LocalOSINT/`](LocalOSINT/)
(Details: [`LocalOSINT/README.md`](LocalOSINT/README.md)):

```bash
cd LocalOSINT
python3 app.py            # nur Standardbibliothek, Python 3.10+, bindet nur 127.0.0.1
python3 -m unittest discover -s tests -v   # 7 Tests
```

## Struktur

```text
.
├── docs/                     # Website für GitHub Pages (index.html, app.js, style.css)
├── .github/workflows/pages.yml
├── ANALYSIS.md               # Analyse der Vorlage + Beschreibung des Web-Ports
├── LocalOSINT/               # Original-Software (app.py, Tests, Start-Skripte, Beispiel)
└── PACKAGE-NOTICE.txt        # Herkunftshinweis zum hochgeladenen Source-Paket
```

## Lizenz

MIT — siehe [`LocalOSINT/LICENSE`](LocalOSINT/LICENSE).

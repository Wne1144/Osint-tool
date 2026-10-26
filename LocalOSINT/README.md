# Local OSINT Research Suite — MVP source starter

A small local-first research notebook with SQLite, a loopback-only web UI, a real public-source adapter (Wikipedia's official MediaWiki search API), transparent heuristic relevance scores, review decisions, and HTML/JSON report export.

> **Release status — important:** This workspace contains tested Python source and source ZIPs, **not** the requested self-contained Windows or Raspberry Pi binaries. These packages currently require Python 3.10+ already installed. No clean Windows/Raspberry Pi packaging tests have been run here; the two-minute criterion is therefore **not demonstrated**. Do not treat these archives as turnkey binary releases. Building and testing native PyInstaller packages on each target architecture remains work to do.

## Run

- Windows with Python 3.10+: double-click `Start-Windows.bat`.
- Raspberry Pi OS / Linux with Python 3.10+: `./start-linux.sh` (or `chmod +x start-linux.sh && ./start-linux.sh` if ZIP extraction lost executable permissions).
- Or `python3 app.py`.

Uses only Python standard-library modules; no pip install, Node, Docker, or database server. The application tries ports 8765–8784 and binds **only** to `127.0.0.1`. Ctrl+C stops it. Startup does not make a web request; the browser is opened locally. Data is kept outside the extracted application folder by default:

- Windows: `%LOCALAPPDATA%\LocalOSINT`
- Linux: `~/.local/share/localosint` (or `$XDG_DATA_HOME/localosint`)
- Override with `LOCALOSINT_DATA_DIR` for portable deployments.

Updating/replacing the application folder does not overwrite this data directory. Back up before updates.

## MVP workflow

1. Create a project with a specific purpose. Record the lawful-purpose acknowledgment.
2. If searching name/email/handle, also confirm that the terms may be sent to Wikipedia. No search takes place at project creation.
3. Select a project and click **Recherche starten**. Selected search terms are transmitted to Wikipedia's public API. At most eight initial queries plus a limited second iteration are run; network timeouts are capped at ten seconds per request.
4. Inspect result excerpts, source URLs, query statuses, score factors, and uncertainties. Mark leads manually. A manual review is stored separately and is not objective proof.
5. Export a report as HTML or JSON. Download/restore backups from the project list. Project deletion and full-data deletion are available.

## Adapter and limits

### Wikipedia MediaWiki search API

- Official public API endpoint: `https://en.wikipedia.org/w/api.php` (`action=query&list=search`). No API key, account, or paid service required.
- Automated adapter is implemented; it records the actual result URL, title, API origin, retrieval timestamp, query, snippet, and status. Requests occur only when the user starts research. API/network unavailability is logged as a technical error, never as “no information exists.”
- Public service access is subject to Wikimedia policies, availability, and rate limits. Search coverage is limited to Wikipedia; it is not a general web search engine. The MVP does not fetch full pages or independently verify claims.
- Search terms are not sent to an external AI. No credentials, login attempts, breach data, CAPTCHA bypass, face matching, or automated profile scraping.
- The external manual-search button opens a Google results page only on explicit click; Google then receives the browser request and query.
- Profile URLs are stored as user-provided inputs but not fetched/validated against the target server. Image input/face search is not implemented; no image is uploaded.

## Scoring

A heuristic 0–100 relevance indicator combines token overlap (45 max), phrase overlap (20), a fixed source-type contribution (15), specificity (10 max), independent corroboration (currently 0), and an uncertainty penalty (-10). It is **not** an identity probability or calibrated confidence. The fixed source contribution reflects only the Wikimedia result source, not the truth of the claim. Name matches are always leads requiring review. Even 80+ is not identity confirmation; this MVP does not auto-promote hits into confirmed facts. The report distinguishes unreviewed leads from user-marked matches.

## Data, backup, deletion

SQLite is created automatically in the data directory. Inputs, queries, runs, sources, snippets, score components, facts, reviews and report snapshots are stored locally. Note that search terms are transmitted to Wikimedia after explicit acknowledgment. Backups are JSON and include project data; protect them as personal data. Restore replaces existing projects. Full erase deletes all project rows; for highly sensitive deletion, stop the app and remove the whole data directory (including SQLite WAL files and backups). No image handling is present in this MVP.

## Privacy and safety

The app binds to loopback only, checks Host and Origin headers, requires a per-process random token for API calls, rejects oversized request bodies, uses parameterized SQL, has a restrictive CSP, escapes rendered source text, and does not execute retrieved HTML. It does not expose a LAN service. This is a pragmatic local-app defense, not a substitute for OS account security or a security audit. The user acknowledgment is not a legal determination or a blanket legal basis. The user is responsible for purpose, necessity, legal basis, and applicable privacy rules.

## Dependencies / licenses

| Component | License | Purpose | Cost / key | Notes |
|---|---|---|---|---|
| Python standard library (`http.server`, `sqlite3`, `urllib`, etc.) | Python Software Foundation License | Web server, SQLite, networking, JSON | Free; no key | Requires a preinstalled Python runtime in these source starter archives |
| SQLite (Python module) | Public domain | Local data store | Free | Bundled with typical CPython builds |
| Wikimedia MediaWiki API | Wikimedia service policies | Public encyclopedia search | No key/fee for this public endpoint | Limits/availability apply; not comprehensive |

No third-party Python package is required. There is no software automatic installer or administrator action.

## Tests

Run from this directory: `python3 -m unittest discover -s tests -v`. Tests cover score bounds/factors, mocked adapter normalization, deduplication constraints, foreign-key deletion, loopback API token gate, JSON restore, and the configured iteration ceiling. They do not constitute Windows/ARM clean-system, load, legal, or security certification. The automated adapter unit test uses a mocked response. Separately, during this workspace session a live Wikipedia API smoke request succeeded and a temporary-database end-to-end research run completed; see `BUILD_STATUS.md`. This is a point-in-time sandbox result, not a service guarantee.

Synthetic example: `examples/synthetic-project.json`. It is fictional and not auto-imported.

## Packaging work still required

For actual turnkey delivery: build separate x64 Windows and ARM64 Raspberry Pi packages (and only include ARMv7 if dependency/runtime support is verified) on their respective native or correctly configured build runners; bundle a Python runtime using PyInstaller or an equivalent; test on clean target systems; measure time from extracted folder to UI; record measured results. Current ZIPs are labeled `-source.zip` to avoid claiming otherwise.

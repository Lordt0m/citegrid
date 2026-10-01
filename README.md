# CiteGrid

CiteGrid is a reproducible evidence ledger for development data citations. It compares electricity access across Nigeria, Ghana, and Kenya with immutable data snapshots and visible revision tracking.

The public interface opens on Explore. At narrower widths, use the labelled **Menu** control to reach Revisions, About the data, the example briefing, and the clearly marked simulated fixture replay. Explore opens with the chart and a **Refine comparison** disclosure showing the selected indicator, countries, and year range; its full controls work without JavaScript. Revision, import-history, briefing, and replay tables become labelled records on phones.

Demo imports are identified as **Simulated example** on Explore, About, Revisions, and pinned briefings. Their export packages also state this in `source.txt`, so fixture values are not presented as live World Bank retrievals.

Historical data from the World Bank World Development Indicators (WDI) may be revised or withdrawn over time. CiteGrid ensures that an analyst or researcher can cite a figure, pin their briefing to a specific immutable snapshot, and audit any revisions observed in subsequent complete data releases.

---

## Technical Baseline

- **Language / Framework:** Python 3.12+ / 3.13 (tested on Python 3.13.14), **Django 5.2 LTS (5.2.17)**
- **Dependencies:** Strictly locked in `requirements.lock` and referenced via `requirements.txt`.
- **Database:** PostgreSQL (with `psycopg` 3.3.6) or local SQLite for zero-setup offline tests and development.
- **Security:** In production (`DEBUG=False`), settings reject any insecure development `SECRET_KEY` with `ImproperlyConfigured`.
- **Architecture:** Server-rendered pages with progressive enhancement, transactional complete imports, immutable content-addressed snapshots, concurrency serialization via `PublicationPointer`, and deterministic revision tracking.

---

## Database & Testing Status

- **Tested on SQLite:** Migrations, partial unique index constraints (`WHERE is_latest=1` and `WHERE is_public_example=1`), and the offline automated suite pass against SQLite.
- **PostgreSQL Status:** `citegrid/settings.py` is configured for PostgreSQL using `psycopg` 3.3.6 and `dj-database-url`. Dedicated PostgreSQL concurrency test (`test_two_writer_concurrency_postgresql`) verifies row-level serialization via `PublicationPointer.objects.select_for_update()`. Because no PostgreSQL service is running on this local Windows machine, the live PostgreSQL path remains unverified locally (test is skipped with explicit notice). In environments with PostgreSQL running, set `DATABASE_URL=postgres://user:password@host:port/dbname` to run against PostgreSQL.

---

## Quickstart & Local Setup

### 1. Prerequisites
Ensure Python 3.12+ is installed on your system.

### 2. Setup Environment
From the repository root:

```bash
# Create a Python virtual environment
python -m venv .venv

# Activate the virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install locked dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```bash
# Windows (PowerShell):
Copy-Item .env.example .env
# macOS / Linux:
cp .env.example .env
```
By default, `.env` configures `DATABASE_URL=sqlite:///db.sqlite3` for instant offline execution.
To use PostgreSQL (when a PostgreSQL service is running):
```env
DATABASE_URL=postgres://citegrid:citegrid@localhost:5432/citegrid
```

### 4. Run Offline Automated Tests
All unit, regression, and UI tests run completely offline without external network dependencies:
```bash
python manage.py test
```
*Current test suite: **121 tests found; 120 passed offline, 1 skipped** (`test_two_writer_concurrency_postgresql` skips on SQLite because SQLite ignores row-level `SELECT FOR UPDATE` locks).*

### 5. Setup Demo Database in One Step (`setup_demo`)
CiteGrid protects ordinary/production databases from simulated fixture contamination via a safety guard requiring `CITEGRID_IS_DEMO_DB=True` and a database name containing `'demo'` or `'test'`.

To run migrations, import the baseline 9-series dataset, and seed the public example briefing on a dedicated demo database in a single step:

```bash
# Windows (PowerShell):
$env:CITEGRID_IS_DEMO_DB="True"
$env:DATABASE_URL="sqlite:///demo.sqlite3"
python manage.py setup_demo --allow-simulation

# macOS / Linux:
CITEGRID_IS_DEMO_DB=True DATABASE_URL=sqlite:///demo.sqlite3 python manage.py setup_demo --allow-simulation
```

### 6. Transactional Import & Publication (CLI)
To manually import the fixed 9-series dataset using the CLI:

```bash
# Dry run: validate scope and compute canonical hash with ZERO database writes
python manage.py import_wdi --dry-run --fixture=core/fixtures/provider/simulated_nine_series_complete.json --allow-simulation

# Import into demo database (simulated fixtures require CITEGRID_IS_DEMO_DB=True and a database whose name contains 'demo' or 'test')
# Windows (PowerShell):
$env:DATABASE_URL="sqlite:///demo.sqlite3"
$env:CITEGRID_IS_DEMO_DB="True"
python manage.py import_wdi --fixture=core/fixtures/provider/simulated_nine_series_complete.json --allow-simulation

# macOS / Linux:
DATABASE_URL=sqlite:///demo.sqlite3 CITEGRID_IS_DEMO_DB=True python manage.py import_wdi --fixture=core/fixtures/provider/simulated_nine_series_complete.json --allow-simulation
```


**Publication Invariants:**
- **Strict 9-Series Completeness:** All 9 country-indicator series must succeed. Partial imports abort with zero snapshots created.
- **Single Active Latest Snapshot:** Enforced via `UniqueConstraint(fields=['is_latest'], condition=Q(is_latest=True))` at the database level.
- **Repeat vs. Reversion Handling (A → B → A):** Re-importing identical data records an `ImportRun` pointing to the existing snapshot without incrementing the version. Reverting to older content creates a new snapshot version (v3) with the original content hash, preserving diffability between versions.
- **Precision Policy:** `DecimalField(max_digits=16, decimal_places=8)`. Values are parsed directly via `parse_float=Decimal` and normalized. Values with >8 decimal places are quantized using `ROUND_HALF_UP` while retaining raw string representation in `raw_value_str`. Values >16 digits fail validation.
- **Durable Audit Trail:** `ImportRun` records are preserved even if the publication transaction rolls back.
- **Concurrency Serialization:** Writers acquire a row lock on `PublicationPointer` via `select_for_update()`, preventing race conditions.

### 7. Revision Ledger & Inspection Interface
CiteGrid records audit transitions between adjacent complete snapshots ($v[N-1] \to v[N]$):

- **Web Ledger (`GET /revisions/`):** Full audit interface filtering by country (NGA, GHA, KEN), change type (`CHANGED`, `NEW_VALUE`, `WITHDRAWN`), snapshot version (`?snapshot=N`), or newer snapshots since a pinned version (`?since=N`).
- **Inline Inspection & Deep Links (`GET /revisions/<id>/`):**
  - **Side-by-side snapshot comparison:** Traces before/after values, snapshot version numbers, 8-character content hashes, and CiteGrid retrieval timestamps.
  - **Attributed Delta:** Displays percentage point differences (`+1.70 pp`) only when units are comparable; provides neutral explanations without causal speculation.
  - **Neighbour Context:** Displays rows for $t-2$ to $t+2$ from the respective snapshots, rendering missing values as `"No data"`.
  - **Authoritative Provenance:** Stored source URLs, provider labels, licences, and indicator definitions read strictly from pinned stored records.
  - **Honest States:** Distinguishes empty database ("Data is not ready yet"), initial snapshot v1 ("Initial Complete Published Snapshot — no earlier snapshot to compare"), no revisions found, and active revisions.
  - **Simulation Badges:** Conspicuously labels test fixture replays (`Simulated example`) to avoid claiming real-world provider events.
- **CLI Inspection:**
  ```bash
  # Inspect all revisions recorded across the ledger
  python manage.py inspect_revisions

  # Inspect revisions introduced by a specific snapshot version
  python manage.py inspect_revisions --snapshot=2
  ```

### 8. Pinned Comparisons & Attributed Export
CiteGrid allows comparisons to be permanently pinned to a specific snapshot, ensuring reproducibility even as newer snapshots publish:

```bash
# Seed the public example comparison (idempotently pins to the earliest published snapshot)
python manage.py seed_public_example
```

**Comparison & Export Invariants:**
- **Exact Snapshot Pinning:** A `SavedComparison` is pinned to a specific `Snapshot` foreign key and validated against the snapshot's scope bounds (`scope_start_year <= start_year <= end_year <= scope_end_year`).
- **Newer-Snapshot Notice:** When a newer complete snapshot is published, the pinned briefing (`GET /comparisons/example/` or `/comparisons/<slug>/`) renders an honest notification banner linking to revisions since that snapshot (`/revisions/?since=<version>`) and Explore studio, while the briefing itself remains strictly pinned to its original data.
- **Single Public Example:** Enforced via `UniqueConstraint(fields=['is_public_example'], condition=Q(is_public_example=True))` at the database level.
- **Deterministic Export Package (`export_comparison.zip`):**
  - Downloadable at `GET /comparisons/example/export/` (or `/comparisons/<id>/export/` for public comparisons).
  - Contains `comparison.csv` (RFC 4180 CRLF, sorted columns/rows, missing values formatted as `"No data"`, normalized decimals) and `source.txt` (exact attribution, indicator definitions, source URLs, licenses, and missing-data policy note read strictly from pinned stored records).
  - ZIP file attributes are normalized (fixed timestamp `2026-01-01 00:00:00`, `compresslevel=6`, `create_system=0`) to guarantee byte-identical exports across repeated requests and across subsequent snapshot publications.
- **Read-Only Public Access & Mutation Guards:**
  - `GET /comparisons/example/`: Public example briefing page (renders "Data is not ready yet" empty state if no snapshots exist).
  - Non-public comparisons (`is_public_example=False`) return `403 Forbidden` to anonymous visitors.
  - All comparison endpoints enforce `@require_GET`; `POST`, `PUT`, `PATCH`, and `DELETE` requests are rejected with `405 Method Not Allowed` without modifying any records.

### 9. Interactive Explore Interface (Evidence Studio)
The root route (`GET /`) provides the primary evidence workspace for comparing electricity access:
- **Synchronized Controls**: Indicator selector (Total, Rural, Urban), country checkboxes (NGA, GHA, KEN), and year range (`start_year` to `end_year`) synchronize heading, SVG chart, data table, 4-column Year Lens, and provenance citation.
- **Strict Gap Integrity**: Discontinuous series paths are split into separate SVG segments. Gaps and missing values **never** render as zero percent or connecting lines across missing years.
- **Year Lens**: A raised 4-column lens card displays Nigeria, Ghana, Kenya values for the selected year alongside delta vs Nigeria and the snapshot-wide latest available year.
- **Zero-JavaScript Usability**: Fully operational without client-side scripts. Table row headers contain year pin links (`<th scope="row"><a class="year-pin-link">`), and Previous/Next buttons preserve all active filter parameters.
- **View Mode Switching**: Supports toggling between combined view, chart-first (`?view=chart`), and table-first (`?view=table`), persisted across session requests.
- **Progressive Enhancement**: When JavaScript is enabled, provides seamless client-side year scrubbing, point selection, and URL updating via `history.replaceState`.
- **Responsive Layout**: Adapts from dual-column 1280px desktop layouts down to single-column stacked record cards at 320px, with compact legends, touch targets $\ge 44\text{px}$, and `prefers-reduced-motion` compliance.

### 10. Dedicated Demo Database Setup Command (`setup_demo`)
For local demonstration and recruiter evaluation, CiteGrid includes an optional preparation command:

```bash
# Windows (PowerShell):
$env:CITEGRID_IS_DEMO_DB="True"
$env:DATABASE_URL="sqlite:///demo.sqlite3"
python manage.py setup_demo --allow-simulation

# macOS / Linux:
CITEGRID_IS_DEMO_DB=True DATABASE_URL=sqlite:///demo.sqlite3 python manage.py setup_demo --allow-simulation
```

**Safety Invariants:**
- Requires both `CITEGRID_IS_DEMO_DB=True` and a database filename containing `'demo'` or `'test'`.
- Refuses ordinary development or production databases.
- Requires explicit `--allow-simulation`.
- Idempotent and safe for repeated runs without replacing unrelated data.

### 11. Recruiter Walkthrough (Evidence Ledger Tour)
A reviewer or recruiter can verify the entire product journey in four concise steps without needing private accounts or network access:

1. **Step 1: Explore Studio (`GET /`)**
   - Start the development server using the demo database prepared in Step 5:
     ```bash
     # Windows (PowerShell):
     $env:DATABASE_URL="sqlite:///demo.sqlite3"
     python manage.py runserver

     # macOS / Linux:
     DATABASE_URL=sqlite:///demo.sqlite3 python manage.py runserver
     ```
   - Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in your browser.
   - Select Total, Rural, or Urban electricity access. Observe how the headline, SVG chart, Year Lens, and provenance strip synchronize immediately.
   - Click any year (or scrub with arrow keys) to pin the 4-column Year Lens and observe the latest available year per country. Note that missing years break line segments and never render as zeros.
2. **Step 2: Pinned Example Briefing & Export (`GET /comparisons/example/`)**
   - Click "Example briefing" in the top navigation.
   - Review the citable comparison pinned to Snapshot v1.
   - Click **Download CSV and source note** (`export_comparison.zip`) and extract it. Observe the RFC 4180 CSV and `source.txt` containing full CC BY-4.0 attribution, SDG 7.1.1 underlying dataset attribution, indicator definitions, and missing value notices.
3. **Step 3: Interactive Fixture Replay (`GET /replay/`)**
   - Click "Fixture Replay" (`Simulated`) in the top navigation or visit [http://127.0.0.1:8000/replay/](http://127.0.0.1:8000/replay/).
   - Step through the user-driven sequence:
     - **Step 1 (Baseline)**: Initial baseline observations.
     - **Step 2 (New value)**: Kenya Rural 2023 newly supplied as 75.00% (`NEW_VALUE`).
     - **Step 3 (Changed past value)**: Nigeria Total 2018 revised from 56.50% to 58.20% (`CHANGED`, `+1.70 pp`).
     - **Step 4 (Withdrawn value)**: Ghana Urban 2015 withdrawn from 72.50% to unmeasured (`WITHDRAWN`).
   - Notice the **Analyst Briefing** card: across all 4 steps, its pinned baseline values remain 100% stable and unchanged!
   - Click the **Simulated Failed-Page Scenario** tab: observe how a simulated failed page causes no publication to occur, leaving the previous complete baseline snapshot live with zero partial records.
   - **Zero Mutation Guarantee:** Replay runs entirely in-memory with zero database writes.
4. **Step 4: About the Data & Audit History (`GET /about/`)**
   - Open [http://127.0.0.1:8000/about/](http://127.0.0.1:8000/about/).
   - Review the methodology, temporal distinction between retrieval dates and observation years, and the durable `ImportRun` audit table displaying status, durations, records, and safe failure categories.

### 12. Optional Live Provider Smoke Check
While all routine and CI tests run 100% offline against recorded fixtures, live reachability of the World Bank WDI API can be verified on demand:

```bash
python manage.py check_provider_smoke
```

### 13. Application Route Map
- Explore evidence studio: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- Revisions ledger: [http://127.0.0.1:8000/revisions/](http://127.0.0.1:8000/revisions/)
- Revision detail / deep link: [http://127.0.0.1:8000/revisions/1/](http://127.0.0.1:8000/revisions/1/)
- Fixture replay (simulated): [http://127.0.0.1:8000/replay/](http://127.0.0.1:8000/replay/)
- About the data, methodology & import history: [http://127.0.0.1:8000/about/](http://127.0.0.1:8000/about/)
- Public example briefing: [http://127.0.0.1:8000/comparisons/example/](http://127.0.0.1:8000/comparisons/example/)
- Public example export ZIP: [http://127.0.0.1:8000/comparisons/example/export/](http://127.0.0.1:8000/comparisons/example/export/)
- Health check endpoint: [http://127.0.0.1:8000/healthz/](http://127.0.0.1:8000/healthz/)


---

## Project Structure & Contracts

- `citegrid/`: Django project settings (including production secret rejection and demo DB flag), root URLs, ASGI/WSGI entrypoints.
- `core/adapters/`: `WorldBankAdapter` implementing unified single-series HTTPS requests, lossless Decimal parsing, and atomic all-or-nothing validation across 9 series.
- `core/fixtures/provider/`: Authentic captured response (`captured_wdi_nga_total_2026.json`) and simulated test fixtures (`simulated_*`).
- `core/provenance.py`: Authoritative metadata registry for indicators, definitions, units, sources, and licences.
- `core/services/differ.py`: `compute_snapshot_diff()` pure domain function identifying `NEW_VALUE`, `CHANGED`, and `WITHDRAWN` transitions between adjacent snapshots.
- `core/services/importer.py`: `ImportService` managing canonical content hashing, publication pointer locking, revision ledger generation, and A → B → A snapshot lifecycle.
- `core/services/comparisons.py`: `get_comparison_grid()` briefing query service and `seed_public_example()` service.
- `core/services/exporter.py`: RFC 4180 CSV generation, `source.txt` metadata synthesis, and byte-deterministic ZIP packager.
- `core/services/explore.py`: `get_explore_context()` Explore studio query service, SVG gap-splitting renderer, and Year Lens builder.
- `core/services/revisions.py`: `get_revisions_context()` revision ledger service, truthful scoping, honest states, percentage point delta calculation, and neighbour row extraction ($t-2$ to $t+2$).
- `core/services/replay.py`: `get_replay_context()` in-memory 4-step revision sequence and simulated failed-page rollback service with zero database writes.
- `core/management/commands/`: Management commands (`import_wdi`, `inspect_revisions`, `seed_public_example`, `setup_demo`, `check_provider_smoke`).
- `core/`: Core models (`Snapshot`, `SnapshotValue`, `Revision`, `SavedComparison`, `ImportRun`, `PublicationPointer`), views, and tests (`tests.py`, `tests_adapter.py`, `tests_import.py`, `tests_revisions.py`, `tests_export.py`, `tests_explore.py`, `tests_revisions_ui.py`, `tests_replay.py`).
- `static/css/citegrid.css`: Design tokens, responsive grid layouts, SVG stroke patterns, revision badges, replay cards, drawer animations, 320px stacked cards, and reduced motion overrides.
- `static/js/`: Progressive enhancement scripts (`explore.js`, `revisions.js`, `replay.js`).
- `templates/`: Semantic, accessible HTML templates (`base.html`, `core/explore.html`, `core/explore_empty.html`, `core/comparison_detail.html`, `core/revisions.html`, `core/revision_detail.html`, `core/about.html`, `core/replay.html`).
The public demo is read-only. It includes a pinned example comparison and a clearly labelled simulated replay; analyst sign-in is outside this version's scope.

## PythonAnywhere Beginner deployment

The free `LordTom.pythonanywhere.com` web app uses Python 3.13 and Manual Configuration. In a PythonAnywhere Bash console, run:

```bash
git clone https://github.com/Lordt0m/citegrid.git
cd citegrid
bash setup_pythonanywhere.sh
```

The setup script creates a private virtual environment and `.env` file, installs the locked dependencies, prepares a dedicated SQLite demo database with clearly labelled simulated data, and collects static files. It preserves an existing `.env` on subsequent runs. Never commit `.env` or the demo database.

In the PythonAnywhere **Web** tab for `LordTom.pythonanywhere.com`:

1. Set **Source code** and **Working directory** to `/home/LordTom/citegrid`.
2. Set **Virtualenv** to `/home/LordTom/.virtualenvs/citegrid`.
3. Replace the contents of `/var/www/lordtom_pythonanywhere_com_wsgi.py` with:

   ```python
   import os
   import sys

   project_path = '/home/LordTom/citegrid'
   if project_path not in sys.path:
       sys.path.insert(0, project_path)

   os.environ['DJANGO_SETTINGS_MODULE'] = 'citegrid.settings'
   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```

4. Add a **Static files** mapping from `/static/` to `/home/LordTom/citegrid/staticfiles`.
5. Enable **Force HTTPS**, then click **Reload**. Check the home page, example briefing, replay, and `/healthz/`.

PythonAnywhere's Beginner plan requires a monthly visit to the Web tab to extend the free web app. Its HTTPS redirect is configured at the hosting edge; Django's `SECURE_SSL_REDIRECT` remains off to avoid a duplicate proxy redirect.

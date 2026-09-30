# CiteGrid Provider Fixture Corpus

This directory contains test fixtures used by the CiteGrid adapter and import pipeline to ensure deterministic, reproducible, 100% offline testing.

## Authentic Captured Response

### `captured_wdi_nga_total_2026.json`
- **Origin:** Captured directly from the official World Bank WDI API.
- **Request URL:** `https://api.worldbank.org/v2/country/NGA/indicator/EG.ELC.ACCS.ZS?source=2&date=2000:2024&format=json&page=1&per_page=50&footnote=y`
- **Retrieval Date:** 2026-09-28T19:04:22+01:00
- **HTTP Status:** 200 OK
- **Header Metadata:**
  ```json
  {
    "page": 1,
    "pages": 1,
    "per_page": 50,
    "total": 25,
    "sourceid": "2",
    "lastupdated": "2026-07-13"
  }
  ```
- **Observations:** 25 records covering annual access to electricity (% of population) for Nigeria (`NGA`) across observation years 2000 to 2024 inclusive.
- **Integrity Note:** The payload is stored verbatim without synthetic fields or modifications.

---

## Simulated Test Fixtures

> [!IMPORTANT]
> **SIMULATION NOTICE**: The files listed below are simulated test samples created strictly to test pagination, edge cases, failure modes, and revision algorithms. They do **not** represent real World Bank revisions or live observations. In accordance with CiteGrid provenance rules, simulation labels are kept in filenames and documentation without modifying the provider's JSON shape.

### 1. `simulated_series_multipage_p1.json` and `simulated_series_multipage_p2.json`
- **Purpose:** Exercises multi-page traversal for a single series.
- **Structure:** Page 1 reports `page: 1, pages: 2, per_page: 15, total: 25`, containing years 2024 down to 2010. Page 2 reports `page: 2, pages: 2, per_page: 15, total: 25`, containing years 2009 down to 2000.

### 2. `simulated_series_with_nulls.json`
- **Purpose:** Verifies that legitimate provider `null` values (such as years where data was unavailable or unmeasured) are preserved as explicit `None` values and never converted to zeros or interpolated.
- **Structure:** Observation years 2000 and 2001 contain `"value": null`. Observation year 2002 contains `"value": 0` (numeric zero) to prove `0` is distinct from `null`.

### 3. `simulated_series_conflict.json`
- **Purpose:** Tests duplicate key collision detection.
- **Structure:** Contains two records for the same series and observation year `2020` with conflicting values (`55.4` vs `60.1`). The adapter must reject this as a duplicate conflict.

### 4. `simulated_series_inconsistent_pagination.json`
- **Purpose:** Tests rejection of malformed or corrupted pagination headers.
- **Structure:** Header reports `page: 2` when page 1 was requested, or record counts that do not match header invariants.

### 5. `simulated_series_revision_v2.json`
- **Purpose:** Simulates a successor data release for a single series to test revision detection (Ticket 4):
  - 1 value changed from past figure (2018: 56.5 -> 58.2)
  - 1 previously missing year newly supplied (2001: null -> 28.0)
  - 1 value withdrawn to missing (2002: numeric -> null)
  - Remaining years unchanged.

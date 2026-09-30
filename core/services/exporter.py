"""
CiteGrid Deterministic Attributed Exporter.

Generates reproducible CSV and provenance text (source.txt) packaged
into byte-identical ZIP downloads pinned to an immutable Snapshot.
"""
import csv
import io
import zipfile
from decimal import Decimal
from typing import Optional

from core.models import SavedComparison
from core.services.comparisons import ComparisonGrid, get_comparison_grid


def format_export_decimal(val: Optional[Decimal]) -> str:
    """Format a decimal value losslessly without scientific notation."""
    if val is None:
        return "No data"
    # normalize() might produce scientific notation for powers of 10, so format with 'f'
    normalized = val.normalize()
    formatted = f"{normalized:f}"
    return formatted


def generate_comparison_csv(grid: ComparisonGrid) -> str:
    """
    Generates deterministic RFC 4180 CSV for a comparison grid.
    Columns are sorted alphabetically by ISO3 code; rows are ordered by year ascending.
    Missing values render as 'No data'.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\r\n', quoting=csv.QUOTE_MINIMAL)

    # Header: Observation Year, followed by country names sorted alphabetically by ISO3 code
    header = ['Observation Year']
    for c in grid.countries:
        c_name = grid.country_names.get(c, c)
        header.append(f"{c_name} ({c})")
    writer.writerow(header)

    # Rows ordered ascending by observation year
    for y in grid.years:
        row = [str(y)]
        for c in grid.countries:
            val = grid.matrix[y].get(c)
            row.append(format_export_decimal(val))
        writer.writerow(row)

    return buf.getvalue()


def generate_source_txt(grid: ComparisonGrid) -> str:
    """
    Generates a deterministic attribution and provenance note for a pinned comparison.
    All attribution and identity fields are read directly from the pinned snapshot's records.
    Uses RFC-compliant CRLF line endings without volatile generation timestamps.
    """
    c_scope_str = ", ".join(f"{grid.country_names.get(c, c)} ({c})" for c in grid.countries)

    latest_years_lines = []
    for c in grid.countries:
        c_name = grid.country_names.get(c, c)
        yr = grid.latest_available_years.get(c)
        latest_years_lines.append(f"  - {c_name} ({c}): {yr if yr is not None else 'No data'}")
    latest_years_str = "\r\n".join(latest_years_lines)

    lines = [
        "=== CiteGrid Attributed Evidence Briefing ===",
        "",
        *(
            ["SIMULATED EXAMPLE: Values were loaded from test fixtures, not retrieved live from the World Bank.", ""]
            if grid.is_simulation else []
        ),
        f"Comparison Title:     {grid.comparison.title}",
        f"Comparison ID:        {grid.comparison.id}",
        f"Pinned Snapshot:      v{grid.snapshot.version} (Content Hash: {grid.snapshot.content_hash})",
        f"Snapshot Source:      {grid.source_name} (Source ID: {grid.source_id})",
        f"Observed by CiteGrid: {grid.retrieved_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "--- Indicator Specification ---",
        f"Indicator Code:       {grid.indicator_code}",
        f"Indicator Name:       {grid.indicator_name}",
        f"Unit of Measure:      {grid.unit}",
        f"Official Definition:  {grid.indicator_definition}",
        f"Provider Source URL:  {grid.source_url}",
        "",
        "--- Comparison Scope ---",
        f"Economies Covered:    {c_scope_str}",
        f"Observation Range:    {grid.comparison.start_year} to {grid.comparison.end_year}",
        "Latest Available Year across Entire Snapshot:",
        latest_years_str,
        "",
        "--- Source Attribution & Licence ---",
        f"Provider:             {grid.provider_label}",
        f"Underlying Source:    {grid.underlying_source}",
        f"Licence:              {grid.license}",
        "",
        "--- Missing-Value Policy ---",
        "Missing values are recorded as 'No data'. This indicates that no numeric value",
        "is available in this pinned snapshot (whether the provider returned an explicit",
        "null observation or the observation was absent from the dataset). It does not",
        "represent zero electricity access.",
        "",
    ]
    return "\r\n".join(lines)


def package_comparison_export(comparison: SavedComparison) -> bytes:
    """
    Packages comparison.csv and source.txt into a deterministic, byte-identical ZIP archive.
    Uses normalized timestamps, compression levels, and platform attributes to guarantee
    byte-identical output across repeated invocations and environments.
    """
    grid = get_comparison_grid(comparison)
    csv_bytes = generate_comparison_csv(grid).encode('utf-8')
    txt_bytes = generate_source_txt(grid).encode('utf-8')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 1. comparison.csv
        zinfo_csv = zipfile.ZipInfo(filename='comparison.csv', date_time=(2026, 1, 1, 0, 0, 0))
        zinfo_csv.compress_type = zipfile.ZIP_DEFLATED
        zinfo_csv.create_system = 0  # Windows/DOS standard platform field (avoids OS header drift)
        zinfo_csv.external_attr = 0o644 << 16  # standard POSIX permissions
        zf.writestr(zinfo_csv, csv_bytes)

        # 2. source.txt
        zinfo_txt = zipfile.ZipInfo(filename='source.txt', date_time=(2026, 1, 1, 0, 0, 0))
        zinfo_txt.compress_type = zipfile.ZIP_DEFLATED
        zinfo_txt.create_system = 0
        zinfo_txt.external_attr = 0o644 << 16
        zf.writestr(zinfo_txt, txt_bytes)

    return buf.getvalue()

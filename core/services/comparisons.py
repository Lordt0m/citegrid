"""
CiteGrid Comparison and Briefing Query Service.

Extracts immutable briefing grids and historical attribution directly
from stored SnapshotValue and Snapshot records for a pinned SavedComparison.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core.models import SavedComparison, Snapshot, SnapshotValue
from core.services.revisions import snapshot_simulation_status


@dataclass(frozen=True)
class ComparisonGrid:
    comparison: SavedComparison
    snapshot: Snapshot
    indicator_code: str
    indicator_name: str
    indicator_definition: str
    unit: str
    source_url: str
    provider_label: str
    underlying_source: str
    license: str
    source_id: str
    source_name: str
    retrieved_at: datetime
    is_simulation: bool
    years: list[int]
    countries: list[str]
    country_names: dict[str, str]
    matrix: dict[int, dict[str, Optional[Decimal]]]
    raw_values: dict[int, dict[str, str]]
    latest_available_years: dict[str, Optional[int]]


def get_comparison_grid(comparison: SavedComparison) -> ComparisonGrid:
    """
    Extracts the briefing data grid and stored provenance for a pinned comparison.
    Reads attribution strictly from the pinned snapshot's stored records.
    """
    snapshot = comparison.snapshot

    # Check that records exist for this indicator in the pinned snapshot
    indicator_rows = snapshot.values.filter(indicator_code=comparison.indicator_code)
    if not indicator_rows.exists():
        raise ValueError(
            f"No observation records found for indicator '{comparison.indicator_code}' "
            f"in Snapshot v{snapshot.version}."
        )

    # Provenance conflict check across stored records
    distinct_provenance = list(
        indicator_rows.order_by().values(
            'indicator_name',
            'indicator_definition',
            'unit',
            'source_url',
            'provider_label',
            'underlying_source',
            'license',
            'source_id',
        ).distinct()
    )
    if len(distinct_provenance) > 1:
        raise ValueError(
            f"Conflicting stored provenance metadata detected for indicator '{comparison.indicator_code}' "
            f"in Snapshot v{snapshot.version}."
        )

    prov = distinct_provenance[0]

    # Map country codes to country names from stored snapshot values
    country_rows = snapshot.values.filter(
        indicator_code=comparison.indicator_code,
        country_code__in=comparison.countries,
    ).order_by().values('country_code', 'country_name').distinct()
    country_names = {r['country_code']: r['country_name'] for r in country_rows}

    # Calculate latest available year across the entire snapshot scope for each country
    latest_available_years: dict[str, Optional[int]] = {}
    for c in comparison.countries:
        latest_val = snapshot.values.filter(
            indicator_code=comparison.indicator_code,
            country_code=c,
            numeric_value__isnull=False,
        ).order_by('-observation_year').first()
        latest_available_years[c] = latest_val.observation_year if latest_val else None

    # Query observations within the comparison's observation year range
    requested_years = list(range(comparison.start_year, comparison.end_year + 1))
    scope_rows = snapshot.values.filter(
        indicator_code=comparison.indicator_code,
        country_code__in=comparison.countries,
        observation_year__range=(comparison.start_year, comparison.end_year),
    )

    lookup: dict[tuple[int, str], tuple[Optional[Decimal], str]] = {}
    for r in scope_rows:
        lookup[(r.observation_year, r.country_code)] = (r.numeric_value, r.raw_value_str)

    matrix: dict[int, dict[str, Optional[Decimal]]] = {}
    raw_values: dict[int, dict[str, str]] = {}
    for y in requested_years:
        matrix[y] = {}
        raw_values[y] = {}
        for c in comparison.countries:
            val, raw = lookup.get((y, c), (None, ''))
            matrix[y][c] = val
            raw_values[y][c] = raw

    return ComparisonGrid(
        comparison=comparison,
        snapshot=snapshot,
        indicator_code=comparison.indicator_code,
        indicator_name=prov['indicator_name'],
        indicator_definition=prov['indicator_definition'],
        unit=prov['unit'],
        source_url=prov['source_url'],
        provider_label=prov['provider_label'],
        underlying_source=prov['underlying_source'],
        license=prov['license'],
        source_id=prov['source_id'],
        source_name=snapshot.source_name,
        retrieved_at=snapshot.retrieved_at,
        is_simulation=snapshot_simulation_status([snapshot.pk]).get(snapshot.pk, False),
        years=requested_years,
        countries=comparison.countries,
        country_names=country_names,
        matrix=matrix,
        raw_values=raw_values,
        latest_available_years=latest_available_years,
    )


def seed_public_example() -> SavedComparison:
    """
    Idempotent service helper to seed or retrieve the public read-only example comparison.
    If an example already exists, it is permanently preserved and never repinned.
    If no example exists, pins to the earliest published snapshot by version.
    """
    existing = SavedComparison.objects.filter(is_public_example=True).first()
    if existing:
        return existing

    earliest_snapshot = Snapshot.objects.order_by('version').first()
    if not earliest_snapshot:
        raise ValueError("Cannot seed public example: no published snapshot exists in the ledger.")

    title = (
        f"Nigeria vs Ghana & Kenya — Electricity Access "
        f"({earliest_snapshot.scope_start_year}–{earliest_snapshot.scope_end_year})"
    )

    example = SavedComparison(
        title=title,
        snapshot=earliest_snapshot,
        indicator_code='EG.ELC.ACCS.ZS',
        countries=['GHA', 'KEN', 'NGA'],
        start_year=earliest_snapshot.scope_start_year,
        end_year=earliest_snapshot.scope_end_year,
        is_public_example=True,
    )
    example.save()
    return example

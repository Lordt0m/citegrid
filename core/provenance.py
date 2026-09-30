"""
CiteGrid Authoritative Indicator Provenance Registry.

Maintains immutable definitions, units, source links, provider labels,
and underlying SDG 7.1.1 electrification dataset attribution.

Source Verification Note:
Checked on 2026-09-29 across official World Bank indicator pages:
- Total: https://data.worldbank.org/indicator/EG.ELC.ACCS.ZS
- Rural: https://data.worldbank.org/indicator/EG.ELC.ACCS.RU.ZS
- Urban: https://data.worldbank.org/indicator/EG.ELC.ACCS.UR.ZS
Current pages specify:
- Source: "SDG 7.1.1 Electrification Dataset"
- License: "CC BY-4.0" (Creative Commons Attribution 4.0 International)
Earlier World Bank releases cited "World Bank, Sustainable Energy for All (SE4ALL)
database from WHO, Energy Progress Report". Historical snapshots preserve their stored
attribution immutably.
"""
from typing import TypedDict


class IndicatorProvenance(TypedDict):
    name: str
    definition: str
    unit: str
    source_url: str
    provider: str
    underlying_source: str
    license: str


INDICATOR_PROVENANCE_REGISTRY: dict[str, IndicatorProvenance] = {
    'EG.ELC.ACCS.ZS': {
        'name': 'Access to electricity (% of population)',
        'definition': (
            'Access to electricity is the percentage of population with access to electricity. '
            'Electrification data are collected from industry, national surveys and international sources.'
        ),
        'unit': '% of population',
        'source_url': 'https://data.worldbank.org/indicator/EG.ELC.ACCS.ZS',
        'provider': 'World Bank',
        'underlying_source': 'SDG 7.1.1 Electrification Dataset',
        'license': 'CC BY-4.0',
    },
    'EG.ELC.ACCS.RU.ZS': {
        'name': 'Access to electricity, rural (% of rural population)',
        'definition': (
            'Access to electricity, rural is the percentage of rural population with access to electricity.'
        ),
        'unit': '% of rural population',
        'source_url': 'https://data.worldbank.org/indicator/EG.ELC.ACCS.RU.ZS',
        'provider': 'World Bank',
        'underlying_source': 'SDG 7.1.1 Electrification Dataset',
        'license': 'CC BY-4.0',
    },
    'EG.ELC.ACCS.UR.ZS': {
        'name': 'Access to electricity, urban (% of urban population)',
        'definition': (
            'Access to electricity, urban is the percentage of urban population with access to electricity.'
        ),
        'unit': '% of urban population',
        'source_url': 'https://data.worldbank.org/indicator/EG.ELC.ACCS.UR.ZS',
        'provider': 'World Bank',
        'underlying_source': 'SDG 7.1.1 Electrification Dataset',
        'license': 'CC BY-4.0',
    },
}


def get_indicator_provenance(indicator_code: str) -> IndicatorProvenance:
    """Retrieve the authoritative provenance metadata for a fixed WDI indicator."""
    if indicator_code not in INDICATOR_PROVENANCE_REGISTRY:
        raise KeyError(f"Indicator '{indicator_code}' is outside CiteGrid fixed provenance registry.")
    return INDICATOR_PROVENANCE_REGISTRY[indicator_code]


VALID_INDICATORS: list[str] = list(INDICATOR_PROVENANCE_REGISTRY.keys())
VALID_COUNTRIES: tuple[str, ...] = ('NGA', 'GHA', 'KEN')
COUNTRY_NAMES: dict[str, str] = {
    'NGA': 'Nigeria',
    'GHA': 'Ghana',
    'KEN': 'Kenya',
}


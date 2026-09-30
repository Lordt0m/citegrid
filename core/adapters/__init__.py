"""
CiteGrid Provider Adapters Package.
"""
from .world_bank import (
    WorldBankAdapter,
    AdapterResult,
    ObservationRecord,
    PageMetadata,
    SeriesScope,
    AdapterErrorCategory,
)

__all__ = [
    'WorldBankAdapter',
    'AdapterResult',
    'ObservationRecord',
    'PageMetadata',
    'SeriesScope',
    'AdapterErrorCategory',
]

"""
CiteGrid Snapshot Diffing Engine.

Computes observation transitions between consecutive complete snapshots (v[N-1] -> v[N]).
Preserves paired old and new provenance and foreign keys to stored SnapshotValue records.
"""
from typing import Sequence

from core.models import Snapshot, SnapshotValue, Revision, RevisionTransition


def compute_snapshot_diff(
    previous_snapshot: Snapshot,
    current_snapshot: Snapshot,
    previous_values: Sequence[SnapshotValue],
    current_values: Sequence[SnapshotValue],
) -> list[Revision]:
    """
    Diffs stored SnapshotValue records between adjacent complete snapshots.
    
    Identifies three discrete transition types:
    - NEW_VALUE: previous was absent/null, new is numeric
    - CHANGED: previous was numeric, new is different numeric
    - WITHDRAWN: previous was numeric, new is absent/null
    
    Unchanged observations and equivalent decimals (55.4 vs 55.40) produce zero revisions.
    Structural null-to-absent or absent-to-null changes produce zero numeric revisions.
    """
    old_map = {
        (v.source_id, v.country_code, v.indicator_code, v.observation_year): v
        for v in previous_values
    }
    new_map = {
        (v.source_id, v.country_code, v.indicator_code, v.observation_year): v
        for v in current_values
    }

    all_keys = sorted(
        set(old_map.keys()) | set(new_map.keys()),
        key=lambda k: (k[0], k[1], k[2], k[3]),
    )

    revisions: list[Revision] = []

    for key in all_keys:
        source_id, country_code, indicator_code, obs_year = key
        old_val = old_map.get(key)
        new_val = new_map.get(key)

        old_num = (
            old_val.numeric_value.normalize()
            if (old_val is not None and old_val.numeric_value is not None)
            else None
        )
        new_num = (
            new_val.numeric_value.normalize()
            if (new_val is not None and new_val.numeric_value is not None)
            else None
        )

        # Unchanged: both None or identical normalized Decimal
        if old_num == new_num:
            continue

        transition_type: RevisionTransition
        if old_num is None and new_num is not None:
            transition_type = RevisionTransition.NEW_VALUE
        elif old_num is not None and new_num is not None and old_num != new_num:
            transition_type = RevisionTransition.CHANGED
        elif old_num is not None and new_num is None:
            transition_type = RevisionTransition.WITHDRAWN
        else:
            continue

        # Country and indicator display names
        country_name = new_val.country_name if new_val else (old_val.country_name if old_val else country_code)
        indicator_name = new_val.indicator_name if new_val else (old_val.indicator_name if old_val else indicator_code)

        revision = Revision(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
            old_snapshot_value=old_val,
            new_snapshot_value=new_val,
            source_id=source_id,
            country_code=country_code,
            country_name=country_name,
            indicator_code=indicator_code,
            indicator_name=indicator_name,
            observation_year=obs_year,
            transition_type=transition_type,
            # Paired old values
            old_value=old_val.numeric_value if old_val else None,
            old_raw_value_str=old_val.raw_value_str if old_val else None,
            old_footnote=old_val.footnote if old_val else '',
            # Paired new values
            new_value=new_val.numeric_value if new_val else None,
            new_raw_value_str=new_val.raw_value_str if new_val else None,
            new_footnote=new_val.footnote if new_val else '',
            # Paired old provenance
            old_unit=old_val.unit if old_val else '',
            old_indicator_definition=old_val.indicator_definition if old_val else '',
            old_source_url=old_val.source_url if old_val else '',
            old_provider_label=old_val.provider_label if old_val else '',
            old_license=old_val.license if old_val else '',
            old_underlying_source=old_val.underlying_source if old_val else '',
            # Paired new provenance
            new_unit=new_val.unit if new_val else '',
            new_indicator_definition=new_val.indicator_definition if new_val else '',
            new_source_url=new_val.source_url if new_val else '',
            new_provider_label=new_val.provider_label if new_val else '',
            new_license=new_val.license if new_val else '',
            new_underlying_source=new_val.underlying_source if new_val else '',
            # Retrieval timestamps
            previous_retrieved_at=previous_snapshot.retrieved_at,
            current_retrieved_at=current_snapshot.retrieved_at,
        )
        revisions.append(revision)

    return revisions

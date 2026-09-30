"""
CiteGrid Revisions Service.
Provides querying, filtering, inspection context, neighbour comparisons,
and simulation detection for the Revisions ledger.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from core.models import ImportRun, ImportStatus, Revision, RevisionTransition, Snapshot, SnapshotValue
from core.provenance import VALID_COUNTRIES


@dataclass(frozen=True)
class NeighbourRow:
    year: int
    is_target_year: bool
    old_value: Optional[Decimal]
    new_value: Optional[Decimal]
    old_raw: Optional[str]
    new_raw: Optional[str]


@dataclass(frozen=True)
class RevisionInspectionDetail:
    revision: Revision
    is_simulation: bool
    delta_str: str
    delta_type: str  # 'positive', 'negative', 'neutral', 'categorical'
    explanation: str
    neighbour_rows: list[NeighbourRow]


@dataclass(frozen=True)
class RevisionsContext:
    state: str  # 'empty', 'initial_snapshot', 'no_revisions', 'revisions_present'
    revisions: list[Revision]
    total_revisions_count: int
    filtered_revisions_count: int
    snapshots_count: int
    latest_snapshot: Optional[Snapshot]
    is_simulation: bool
    selected_country: Optional[str]
    selected_transition: Optional[str]
    selected_snapshot_version: Optional[int]
    selected_since_version: Optional[int]
    scope_header: str
    inspection: Optional[RevisionInspectionDetail] = None
    all_snapshots: list[Snapshot] = field(default_factory=list)


def snapshot_simulation_status(snapshot_ids: list[int]) -> dict[int, bool]:
    """Use the first completed import for each snapshot, not later repeat runs."""
    status = {}
    runs = ImportRun.objects.filter(
        snapshot_id__in=snapshot_ids,
        status=ImportStatus.COMPLETED,
    ).order_by('started_at', 'pk').values_list('snapshot_id', 'is_simulation')
    for snapshot_id, is_simulation in runs:
        status.setdefault(snapshot_id, is_simulation)
    return status


def get_revisions_context(
    country: Optional[str] = None,
    transition: Optional[str] = None,
    snapshot_version: Optional[Any] = None,
    since_version: Optional[Any] = None,
    inspect_id: Optional[Any] = None,
) -> RevisionsContext:
    """
    Builds the complete context for the CiteGrid Revisions ledger view.
    Handles empty, single-snapshot, zero-revision, and active revision states.
    Strictly reads paired provenance from stored records.
    """
    all_snapshots = list(Snapshot.objects.order_by('-version'))
    snapshots_count = len(all_snapshots)

    if snapshots_count == 0:
        return RevisionsContext(
            state='empty',
            revisions=[],
            total_revisions_count=0,
            filtered_revisions_count=0,
            snapshots_count=0,
            latest_snapshot=None,
            is_simulation=False,
            selected_country=None,
            selected_transition=None,
            selected_snapshot_version=None,
            selected_since_version=None,
            scope_header="Data is not ready yet. A complete import is required.",
            all_snapshots=[],
        )

    latest_snapshot = all_snapshots[0]
    simulation_by_snapshot = snapshot_simulation_status([s.pk for s in all_snapshots])

    if snapshots_count == 1:
        # Check simulation status for snapshot 1
        is_sim = simulation_by_snapshot.get(latest_snapshot.pk, False)
        return RevisionsContext(
            state='initial_snapshot',
            revisions=[],
            total_revisions_count=0,
            filtered_revisions_count=0,
            snapshots_count=1,
            latest_snapshot=latest_snapshot,
            is_simulation=is_sim,
            selected_country=None,
            selected_transition=None,
            selected_snapshot_version=None,
            selected_since_version=None,
            scope_header=f"Snapshot v{latest_snapshot.version} is the initial complete published snapshot. There is no earlier snapshot to compare.",
            all_snapshots=all_snapshots,
        )

    # Base queryset for all revisions
    base_qs = Revision.objects.select_related(
        'previous_snapshot',
        'current_snapshot',
        'old_snapshot_value',
        'new_snapshot_value',
    ).order_by('-current_snapshot__version', 'country_code', 'indicator_code', '-observation_year')

    total_revisions_count = base_qs.count()

    # Apply filters
    filtered_qs = base_qs

    # 1. Country filter
    valid_country = country.upper() if (country and country.upper() in VALID_COUNTRIES) else None
    if valid_country:
        filtered_qs = filtered_qs.filter(country_code=valid_country)

    # 2. Transition filter
    valid_transition = None
    if transition and transition.upper() in RevisionTransition.values:
        valid_transition = transition.upper()
        filtered_qs = filtered_qs.filter(transition_type=valid_transition)

    # 3. Snapshot filter (filters revisions resulting in this current_snapshot)
    parsed_snapshot_version = None
    if snapshot_version is not None:
        try:
            parsed_snapshot_version = int(snapshot_version)
            filtered_qs = filtered_qs.filter(current_snapshot__version=parsed_snapshot_version)
        except (ValueError, TypeError):
            parsed_snapshot_version = None

    # 4. Since filter (filters revisions introduced after this snapshot version)
    parsed_since_version = None
    if since_version is not None:
        try:
            parsed_since_version = int(since_version)
            filtered_qs = filtered_qs.filter(current_snapshot__version__gt=parsed_since_version)
        except (ValueError, TypeError):
            parsed_since_version = None

    filtered_revisions = list(filtered_qs)
    filtered_revisions_count = len(filtered_revisions)
    for revision in filtered_revisions:
        revision.is_simulation_display = (
            simulation_by_snapshot.get(revision.previous_snapshot_id, False)
            or simulation_by_snapshot.get(revision.current_snapshot_id, False)
        )
    is_simulation = any(rev.is_simulation_display for rev in filtered_revisions)
    if total_revisions_count == 0:
        is_simulation = simulation_by_snapshot.get(latest_snapshot.pk, False)

    # Determine state and scope header
    if total_revisions_count == 0:
        state = 'no_revisions'
        scope_header = "No changes found between the complete snapshots stored so far."
    else:
        state = 'revisions_present'
        if parsed_snapshot_version:
            scope_header = f"Changes introduced in Snapshot v{parsed_snapshot_version}"
        elif parsed_since_version:
            scope_header = f"Changes recorded after Snapshot v{parsed_since_version} (across newer snapshots)"
        else:
            scope_header = "Changes recorded across adjacent complete published snapshots"

    # Build inspection detail if inspect_id is supplied
    inspection = None
    if inspect_id is not None:
        try:
            target_pk = int(inspect_id)
            target_rev = base_qs.filter(pk=target_pk).first()
            if target_rev:
                inspection = _build_inspection_detail(
                    target_rev,
                    simulation_by_snapshot.get(target_rev.previous_snapshot_id, False)
                    or simulation_by_snapshot.get(target_rev.current_snapshot_id, False),
                )
        except (ValueError, TypeError):
            inspection = None

    return RevisionsContext(
        state=state,
        revisions=filtered_revisions,
        total_revisions_count=total_revisions_count,
        filtered_revisions_count=filtered_revisions_count,
        snapshots_count=snapshots_count,
        latest_snapshot=latest_snapshot,
        is_simulation=is_simulation,
        selected_country=valid_country,
        selected_transition=valid_transition,
        selected_snapshot_version=parsed_snapshot_version,
        selected_since_version=parsed_since_version,
        scope_header=scope_header,
        inspection=inspection,
        all_snapshots=all_snapshots,
    )


def _build_inspection_detail(rev: Revision, is_simulation: bool) -> RevisionInspectionDetail:
    """Constructs detailed inspection data, neighbour context, and delta for a specific revision."""
    # Delta calculation
    if rev.old_value is not None and rev.new_value is not None:
        # Check unit comparability
        old_unit = (rev.old_unit or '').strip().lower()
        new_unit = (rev.new_unit or '').strip().lower()
        units_comparable = old_unit == new_unit and '%' in old_unit

        if units_comparable:
            diff = rev.new_value - rev.old_value
            if diff > 0:
                delta_str = f"+{diff:.2f} pp"
                delta_type = "positive"
            elif diff < 0:
                delta_str = f"{diff:.2f} pp"
                delta_type = "negative"
            else:
                delta_str = "0.00 pp"
                delta_type = "neutral"
        else:
            delta_str = "Values altered (units differed)"
            delta_type = "categorical"
    elif rev.old_value is None and rev.new_value is not None:
        delta_str = "New value added"
        delta_type = "positive"
    elif rev.old_value is not None and rev.new_value is None:
        delta_str = "Value withdrawn"
        delta_type = "negative"
    else:
        delta_str = "Status transition"
        delta_type = "categorical"

    # Objective neutral explanation
    obs_year = rev.observation_year
    c_name = rev.country_name

    if rev.transition_type == RevisionTransition.CHANGED:
        explanation = (
            f"Between Snapshot v{rev.previous_snapshot.version} and Snapshot v{rev.current_snapshot.version}, "
            f"CiteGrid observed a different stored value for {c_name} in observation year "
            f"{obs_year} from {rev.old_value}% to {rev.new_value}%. "
            f"CiteGrid does not infer why the values differ."
        )
    elif rev.transition_type == RevisionTransition.NEW_VALUE:
        explanation = (
            f"In Snapshot v{rev.current_snapshot.version}, CiteGrid observed a newly supplied observation for "
            f"{c_name} in observation year {obs_year} ({rev.new_value}%). "
            f"No numeric observation was present for this year in Snapshot v{rev.previous_snapshot.version}."
        )
    elif rev.transition_type == RevisionTransition.WITHDRAWN:
        explanation = (
            f"In Snapshot v{rev.current_snapshot.version}, CiteGrid observed that the observation for "
            f"{c_name} in observation year {obs_year} (previously {rev.old_value}%) is now recorded "
            f"as missing (No data). CiteGrid does not infer why the value is missing."
        )
    else:
        explanation = f"Observation transition observed by CiteGrid between Snapshot v{rev.previous_snapshot.version} and Snapshot v{rev.current_snapshot.version}."

    # Contextual neighbour rows (+/- 2 years around the observation year within snapshot bounds)
    min_year = min(rev.previous_snapshot.scope_start_year, rev.current_snapshot.scope_start_year)
    max_year = max(rev.previous_snapshot.scope_end_year, rev.current_snapshot.scope_end_year)

    start_neighbour = max(min_year, obs_year - 2)
    end_neighbour = min(max_year, obs_year + 2)

    # Fetch values from each snapshot
    old_values_by_year = {
        sv.observation_year: sv
        for sv in SnapshotValue.objects.filter(
            snapshot=rev.previous_snapshot,
            country_code=rev.country_code,
            indicator_code=rev.indicator_code,
            observation_year__gte=start_neighbour,
            observation_year__lte=end_neighbour,
        )
    }

    new_values_by_year = {
        sv.observation_year: sv
        for sv in SnapshotValue.objects.filter(
            snapshot=rev.current_snapshot,
            country_code=rev.country_code,
            indicator_code=rev.indicator_code,
            observation_year__gte=start_neighbour,
            observation_year__lte=end_neighbour,
        )
    }

    neighbour_rows = []
    for y in range(start_neighbour, end_neighbour + 1):
        v_old = old_values_by_year.get(y)
        v_new = new_values_by_year.get(y)

        neighbour_rows.append(
            NeighbourRow(
                year=y,
                is_target_year=(y == obs_year),
                old_value=v_old.numeric_value if v_old else None,
                new_value=v_new.numeric_value if v_new else None,
                old_raw=v_old.raw_value_str if v_old else None,
                new_raw=v_new.raw_value_str if v_new else None,
            )
        )

    return RevisionInspectionDetail(
        revision=rev,
        is_simulation=is_simulation,
        delta_str=delta_str,
        delta_type=delta_type,
        explanation=explanation,
        neighbour_rows=neighbour_rows,
    )

"""
CiteGrid Fixture Replay Service.

Provides isolated, in-memory evaluation of a 4-step revision sequence
and a simulated failed-page scenario using recorded test fixtures.

CRITICAL INVARIANTS:
1. No replay action modifies real published data (zero database writes).
2. The 4 steps are built by applying one change at a time to an in-memory baseline.
3. The selected observation year remains stable across steps; focused changes are
   highlighted separately.
4. The pinned briefing remains strictly tied to Step 1 baseline across all steps.
5. All dates and hashes are labelled as synthetic/simulated; no real provider
   events or cryptographic verifications are claimed.
"""
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Optional

from django.conf import settings

from core.provenance import (
    VALID_COUNTRIES,
    VALID_INDICATORS,
    INDICATOR_PROVENANCE_REGISTRY,
    COUNTRY_NAMES,
)

FIXTURE_PATH = Path(settings.BASE_DIR) / 'core/fixtures/provider' / 'simulated_nine_series_complete.json'

_CACHED_BASE_OBSERVATIONS: Optional[dict[tuple[str, str, int], Optional[Decimal]]] = None

SERIES_STYLES = {
    'NGA': {
        'color': '#0d766e',
        'stroke_dash': 'none',
        'marker': 'circle',
        'css_class': 'series-nga',
    },
    'GHA': {
        'color': '#7c3aed',
        'stroke_dash': '6,4',
        'marker': 'square',
        'css_class': 'series-gha',
    },
    'KEN': {
        'color': '#b45309',
        'stroke_dash': '2,3',
        'marker': 'diamond',
        'css_class': 'series-ken',
    },
}


def _load_base_observations() -> dict[tuple[str, str, int], Optional[Decimal]]:
    """Loads baseline observations from the complete fixture once into memory."""
    global _CACHED_BASE_OBSERVATIONS
    if _CACHED_BASE_OBSERVATIONS is not None:
        return _CACHED_BASE_OBSERVATIONS

    observations: dict[tuple[str, str, int], Optional[Decimal]] = {}
    with open(FIXTURE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for key, series_payload in data.items():
        # key format: {country}_{indicator}
        country_code, indicator_code = key.split('_', 1)
        records = series_payload[1]
        for rec in records:
            year = int(rec['date'])
            val_raw = rec.get('value')
            if val_raw is not None:
                val = Decimal(str(val_raw))
            else:
                val = None
            observations[(country_code, indicator_code, year)] = val

    _CACHED_BASE_OBSERVATIONS = observations
    return _CACHED_BASE_OBSERVATIONS


@dataclass(frozen=True)
class FocusedChange:
    country_code: str
    country_name: str
    indicator_code: str
    indicator_name: str
    year: int
    transition_type: str
    old_value_str: str
    new_value_str: str
    delta_str: Optional[str]
    explanation: str


@dataclass(frozen=True)
class ReplayStepInfo:
    number: int
    name: str
    badge_label: str
    is_active: bool


@dataclass(frozen=True)
class ReplayTableCell:
    country_code: str
    country_name: str
    formatted_value: str
    is_missing: bool
    is_focused_change: bool
    transition_badge: Optional[str] = None


@dataclass(frozen=True)
class ReplayTableRow:
    year: int
    is_selected: bool
    is_focused_year: bool
    cells: list[ReplayTableCell]


@dataclass(frozen=True)
class ReplayChartPoint:
    year: int
    value: float
    formatted_value: str
    cx: float
    cy: float
    marker: str
    color: str
    country_code: str
    country_name: str
    is_selected_year: bool
    is_focused_change: bool


@dataclass(frozen=True)
class ReplayChartSegment:
    path_d: str


@dataclass(frozen=True)
class ReplaySeriesPath:
    country_code: str
    country_name: str
    color: str
    stroke_dash: str
    marker: str
    css_class: str
    segments: list[ReplayChartSegment]
    points: list[ReplayChartPoint]
    end_label_x: float
    end_label_y: float
    latest_formatted_value: str
    latest_observation_year: Optional[int]


@dataclass(frozen=True)
class ReplayYearLensItem:
    country_code: str
    country_name: str
    color: str
    marker: str
    stroke_dash: str
    css_class: str
    formatted_value: str
    is_missing: bool
    delta_vs_nigeria: Optional[str]


@dataclass(frozen=True)
class ReplayPinnedBriefingItem:
    country_code: str
    country_name: str
    indicator_name: str
    year: int
    formatted_value: str
    is_missing: bool


@dataclass(frozen=True)
class ReplayContext:
    scenario: str
    step: int
    steps: list[ReplayStepInfo]
    prev_step: Optional[int]
    next_step: Optional[int]
    focused_change: Optional[FocusedChange]
    scenario_heading: str
    scenario_summary: str

    indicator_code: str
    indicator_name: str
    indicator_unit: str
    available_indicators: list[tuple[str, str]]

    selected_year: int
    start_year: int
    end_year: int
    all_years: list[int]

    series_paths: list[ReplaySeriesPath]
    crosshair_x: float
    chart_years: list[tuple[int, float]]
    chart_y_ticks: list[tuple[int, float]]

    lens_items: list[ReplayYearLensItem]
    table_rows: list[ReplayTableRow]

    pinned_briefing_items: list[ReplayPinnedBriefingItem]
    failed_run_log: Optional[dict[str, Any]]


def get_replay_context(
    scenario: str = 'revisions',
    step: int = 1,
    indicator_code: str = 'EG.ELC.ACCS.ZS',
    selected_year: Optional[int] = None,
    country_codes: Optional[list[str]] = None,
) -> ReplayContext:
    """
    Evaluates replay state entirely in-memory with ZERO database mutations.
    """
    # 1. Parameter Validation & Normalization
    if scenario not in ('revisions', 'failed_page'):
        scenario = 'revisions'

    try:
        step_int = int(step)
        if step_int < 1 or step_int > 4:
            step_int = 1
    except (TypeError, ValueError):
        step_int = 1

    if indicator_code not in VALID_INDICATORS:
        indicator_code = 'EG.ELC.ACCS.ZS'

    start_year = 2000
    end_year = 2024
    all_years = list(range(start_year, end_year + 1))

    # Binding Check 2: Preserve selected_year across steps; do not auto-force to change year
    if selected_year is None:
        selected_year_int = 2024
    else:
        try:
            selected_year_int = int(selected_year)
            if selected_year_int < start_year:
                selected_year_int = start_year
            elif selected_year_int > end_year:
                selected_year_int = end_year
        except (TypeError, ValueError):
            selected_year_int = 2024

    selected_countries = ['NGA', 'GHA', 'KEN']

    # 2. Build In-Memory Observations for Active Step
    # Binding Check 1: Start from complete baseline fixture and apply one change per step
    base_obs = _load_base_observations()
    active_obs = dict(base_obs)

    focused_change: Optional[FocusedChange] = None

    if scenario == 'revisions':
        if step_int == 1:
            # Baseline: exactly base_obs
            focused_change = None
            scenario_heading = "Step 1: Baseline scenario"
            scenario_summary = (
                "In this simulated baseline scenario, observation values represent initial historical "
                "records across Nigeria, Ghana, and Kenya. An analyst comparison briefing is pinned "
                "to this baseline state."
            )
        elif step_int == 2:
            # Step 2: Apply ONLY Kenya Rural 2023 (previously missing -> 75.00%)
            active_obs[('KEN', 'EG.ELC.ACCS.RU.ZS', 2023)] = Decimal('75.0')
            focused_change = FocusedChange(
                country_code='KEN',
                country_name='Kenya',
                indicator_code='EG.ELC.ACCS.RU.ZS',
                indicator_name='Access to electricity, rural (% of rural population)',
                year=2023,
                transition_type='NEW_VALUE',
                old_value_str='No data',
                new_value_str='75.00%',
                delta_str=None,
                explanation=(
                    "In this scenario, if the provider newly supplies an observation for a previously "
                    "unmeasured year (Kenya, rural access in 2023), CiteGrid detects and records a "
                    "NEW_VALUE transition. Notice that the in-memory pinned example briefing remains unchanged."
                ),
            )
            scenario_heading = "Step 2: New value scenario"
            scenario_summary = (
                "In this scenario, a previously missing observation is newly supplied as 75.00%. "
                "CiteGrid identifies a NEW_VALUE transition without altering the pinned baseline briefing."
            )
        elif step_int == 3:
            # Step 3: Keep Step 2 AND apply Nigeria Total 2018 (56.50% -> 58.20%)
            active_obs[('KEN', 'EG.ELC.ACCS.RU.ZS', 2023)] = Decimal('75.0')
            active_obs[('NGA', 'EG.ELC.ACCS.ZS', 2018)] = Decimal('58.2')
            focused_change = FocusedChange(
                country_code='NGA',
                country_name='Nigeria',
                indicator_code='EG.ELC.ACCS.ZS',
                indicator_name='Access to electricity (% of population)',
                year=2018,
                transition_type='CHANGED',
                old_value_str='56.50%',
                new_value_str='58.20%',
                delta_str='+1.70 pp',
                explanation=(
                    "In this scenario, if the provider revises a historical figure (Nigeria, total access "
                    "in 2018 from 56.50% to 58.20%), CiteGrid records a CHANGED transition with before and "
                    "after values and a +1.70 pp delta. The in-memory pinned example briefing continues "
                    "to display the original 56.50%."
                ),
            )
            scenario_heading = "Step 3: Changed past value scenario"
            scenario_summary = (
                "In this scenario, historical total access for Nigeria in 2018 is revised upward by +1.70 pp. "
                "CiteGrid flags a CHANGED transition while the pinned briefing stays locked."
            )
        else:  # step_int == 4
            # Step 4: Keep Step 3 AND apply Ghana Urban 2015 (72.50% -> None / Withdrawn)
            active_obs[('KEN', 'EG.ELC.ACCS.RU.ZS', 2023)] = Decimal('75.0')
            active_obs[('NGA', 'EG.ELC.ACCS.ZS', 2018)] = Decimal('58.2')
            active_obs[('GHA', 'EG.ELC.ACCS.UR.ZS', 2015)] = None
            focused_change = FocusedChange(
                country_code='GHA',
                country_name='Ghana',
                indicator_code='EG.ELC.ACCS.UR.ZS',
                indicator_name='Access to electricity, urban (% of urban population)',
                year=2015,
                transition_type='WITHDRAWN',
                old_value_str='72.50%',
                new_value_str='No data',
                delta_str=None,
                explanation=(
                    "In this scenario, if an observation previously published by the provider is withdrawn "
                    "to missing (Ghana, urban access in 2015), CiteGrid logs a WITHDRAWN transition. "
                    "The in-memory pinned example briefing still preserves the 72.50% figure citable at baseline."
                ),
            )
            scenario_heading = "Step 4: Withdrawn value scenario"
            scenario_summary = (
                "In this scenario, Ghana's 2015 urban observation is withdrawn by the provider to unmeasured. "
                "CiteGrid logs a WITHDRAWN transition without deleting historical audit records."
            )
        failed_run_log = None
    else:
        # Binding Check 3: Failed-page scenario is an isolated illustration identical to baseline
        step_int = 1
        focused_change = None
        scenario_heading = "Simulated Failed-Page Scenario"
        scenario_summary = (
            "In this simulated scenario, Page 2 could not be retrieved from the provider. "
            "Because CiteGrid requires complete coverage across all 9 series before publication, "
            "no new snapshot was published and the previous complete scenario snapshot remains available. "
            "Notice that the chart and table below remain identical to the baseline; "
            "no partial data or spurious withdrawals were introduced."
        )
        failed_run_log = {
            'status': 'FAILED',
            'attempt_time': 'Synthetic illustration timestamp',
            'error_category': 'ADAPTER_ERROR / NETWORK_ERROR',
            'pages_fetched': '1 of 2',
            'records_committed': '0 (Publication aborted)',
            'safe_message': 'Page 2 could not be retrieved from provider.',
            'explanation': (
                "In this simulated scenario, Page 2 could not be retrieved from the provider. "
                "Because CiteGrid requires complete coverage across all 9 series before publication, "
                "no new snapshot was published and the previous complete scenario snapshot remains available. "
                "Notice that the chart and table below remain identical to the baseline; "
                "no partial data or spurious withdrawals were introduced."
            ),
        }

    # 3. Steps Navigation Info
    step_defs = [
        (1, "1. Baseline", "Baseline snapshot"),
        (2, "2. New value", "Newly supplied observation"),
        (3, "3. Changed", "Revised historical figure"),
        (4, "4. Withdrawn", "Withdrawn observation"),
    ]
    steps_info = [
        ReplayStepInfo(
            number=num,
            name=name,
            badge_label=badge,
            is_active=(scenario == 'revisions' and step_int == num),
        )
        for num, name, badge in step_defs
    ]
    prev_step = (step_int - 1) if step_int > 1 else None
    next_step = (step_int + 1) if step_int < 4 else None

    # 4. In-Memory Pinned Briefing (Binding Check 1 & 4)
    # Stays strictly pinned to Step 1 baseline observations across all 4 steps
    pinned_items = [
        ReplayPinnedBriefingItem(
            country_code='NGA',
            country_name='Nigeria',
            indicator_name='Access to electricity (% of population)',
            year=2018,
            formatted_value='56.50%',
            is_missing=False,
        ),
        ReplayPinnedBriefingItem(
            country_code='GHA',
            country_name='Ghana',
            indicator_name='Access to electricity, urban (% of urban population)',
            year=2015,
            formatted_value='72.50%',
            is_missing=False,
        ),
        ReplayPinnedBriefingItem(
            country_code='KEN',
            country_name='Kenya',
            indicator_name='Access to electricity, rural (% of rural population)',
            year=2023,
            formatted_value='No data',
            is_missing=True,
        ),
    ]

    # 5. SVG Chart Coordinate Math
    plot_x0 = 50.0
    plot_y0 = 20.0
    plot_w = 570.0
    plot_h = 260.0

    def _x_for_year(y: int) -> float:
        return plot_x0 + ((y - start_year) / (end_year - start_year)) * plot_w

    def _y_for_val(v: float) -> float:
        return plot_y0 + ((100.0 - v) / 100.0) * plot_h

    series_paths: list[ReplaySeriesPath] = []
    for country in selected_countries:
        style = SERIES_STYLES[country]
        points: list[ReplayChartPoint] = []
        segments: list[ReplayChartSegment] = []
        current_segment_pts: list[tuple[float, float]] = []

        latest_year: Optional[int] = None
        latest_val: Optional[Decimal] = None

        for y in range(start_year, end_year + 1):
            val_dec = active_obs.get((country, indicator_code, y))
            if val_dec is not None:
                latest_year = y
                latest_val = val_dec
                val_flt = float(val_dec)
                px = round(_x_for_year(y), 1)
                py = round(_y_for_val(val_flt), 1)
                current_segment_pts.append((px, py))

                is_focused = bool(
                    focused_change
                    and focused_change.country_code == country
                    and focused_change.indicator_code == indicator_code
                    and focused_change.year == y
                )

                points.append(
                    ReplayChartPoint(
                        year=y,
                        value=val_flt,
                        formatted_value=f"{val_dec.normalize():f}%",
                        cx=px,
                        cy=py,
                        marker=style['marker'],
                        color=style['color'],
                        country_code=country,
                        country_name=COUNTRY_NAMES[country],
                        is_selected_year=(y == selected_year_int),
                        is_focused_change=is_focused,
                    )
                )
            else:
                if current_segment_pts:
                    d_cmd = f"M {current_segment_pts[0][0]} {current_segment_pts[0][1]}"
                    for p in current_segment_pts[1:]:
                        d_cmd += f" L {p[0]} {p[1]}"
                    segments.append(ReplayChartSegment(path_d=d_cmd))
                    current_segment_pts = []

        if current_segment_pts:
            d_cmd = f"M {current_segment_pts[0][0]} {current_segment_pts[0][1]}"
            for p in current_segment_pts[1:]:
                d_cmd += f" L {p[0]} {p[1]}"
            segments.append(ReplayChartSegment(path_d=d_cmd))

        end_lbl_x = round(_x_for_year(end_year) + 8, 1)
        if points:
            end_lbl_y = points[-1].cy + 4.0
        else:
            end_lbl_y = plot_y0 + plot_h / 2

        f_latest = f"{latest_val.normalize():f}%" if latest_val is not None else "No data"
        series_paths.append(
            ReplaySeriesPath(
                country_code=country,
                country_name=COUNTRY_NAMES[country],
                color=style['color'],
                stroke_dash=style['stroke_dash'],
                marker=style['marker'],
                css_class=style['css_class'],
                segments=segments,
                points=points,
                end_label_x=end_lbl_x,
                end_label_y=end_lbl_y,
                latest_formatted_value=f_latest,
                latest_observation_year=latest_year,
            )
        )

    crosshair_x = round(_x_for_year(selected_year_int), 1)
    chart_years = [(yr, round(_x_for_year(yr), 1)) for yr in range(start_year, end_year + 1) if yr == 2000 or yr == 2024 or yr % 5 == 0]
    chart_y_ticks = [(pct, round(_y_for_val(float(pct)), 1)) for pct in [0, 25, 50, 75, 100]]

    # 6. Year Lens Items (for selected_year)
    nga_val = active_obs.get(('NGA', indicator_code, selected_year_int))
    lens_items: list[ReplayYearLensItem] = []
    for country in selected_countries:
        style = SERIES_STYLES[country]
        val_dec = active_obs.get((country, indicator_code, selected_year_int))
        if val_dec is not None:
            f_val = f"{val_dec.normalize():f}%"
            is_miss = False
            if country != 'NGA' and nga_val is not None:
                delta = val_dec - nga_val
                delta_str = f"{'+' if delta >= 0 else ''}{delta.normalize():f} pp"
            else:
                delta_str = None
        else:
            f_val = "No data in this snapshot"
            is_miss = True
            delta_str = None

        lens_items.append(
            ReplayYearLensItem(
                country_code=country,
                country_name=COUNTRY_NAMES[country],
                color=style['color'],
                marker=style['marker'],
                stroke_dash=style['stroke_dash'],
                css_class=style['css_class'],
                formatted_value=f_val,
                is_missing=is_miss,
                delta_vs_nigeria=delta_str,
            )
        )

    # 7. Semantic Table Rows (2024 down to 2000)
    table_rows: list[ReplayTableRow] = []
    for y in range(end_year, start_year - 1, -1):
        cells: list[ReplayTableCell] = []
        is_focused_yr = bool(focused_change and focused_change.year == y)

        for country in selected_countries:
            val_dec = active_obs.get((country, indicator_code, y))
            is_cell_focused = bool(
                focused_change
                and focused_change.country_code == country
                and focused_change.indicator_code == indicator_code
                and focused_change.year == y
            )

            if val_dec is not None:
                f_val = f"{val_dec.normalize():f}%"
                is_miss = False
            else:
                f_val = "No data"
                is_miss = True

            trans_badge = focused_change.transition_type if is_cell_focused else None

            cells.append(
                ReplayTableCell(
                    country_code=country,
                    country_name=COUNTRY_NAMES[country],
                    formatted_value=f_val,
                    is_missing=is_miss,
                    is_focused_change=is_cell_focused,
                    transition_badge=trans_badge,
                )
            )

        table_rows.append(
            ReplayTableRow(
                year=y,
                is_selected=(y == selected_year_int),
                is_focused_year=is_focused_yr,
                cells=cells,
            )
        )

    # 8. Available Indicators
    available_indicators = [
        (code, INDICATOR_PROVENANCE_REGISTRY[code]['name'])
        for code in VALID_INDICATORS
    ]
    ind_prov = INDICATOR_PROVENANCE_REGISTRY[indicator_code]

    return ReplayContext(
        scenario=scenario,
        step=step_int,
        steps=steps_info,
        prev_step=prev_step,
        next_step=next_step,
        focused_change=focused_change,
        scenario_heading=scenario_heading,
        scenario_summary=scenario_summary,
        indicator_code=indicator_code,
        indicator_name=ind_prov['name'],
        indicator_unit=ind_prov['unit'],
        available_indicators=available_indicators,
        selected_year=selected_year_int,
        start_year=start_year,
        end_year=end_year,
        all_years=all_years,
        series_paths=series_paths,
        crosshair_x=crosshair_x,
        chart_years=chart_years,
        chart_y_ticks=chart_y_ticks,
        lens_items=lens_items,
        table_rows=table_rows,
        pinned_briefing_items=pinned_items,
        failed_run_log=failed_run_log,
    )

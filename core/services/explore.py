from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import models

from core.models import Snapshot, SnapshotValue, Revision, VALID_COUNTRIES
from core.provenance import VALID_INDICATORS, get_indicator_provenance

COUNTRY_NAMES = {
    'NGA': 'Nigeria',
    'GHA': 'Ghana',
    'KEN': 'Kenya',
}

SERIES_STYLES = {
    'NGA': {
        'color': 'var(--series-nigeria)',
        'stroke_dash': 'none',
        'marker': 'circle',
        'css_class': 'series-nigeria',
    },
    'GHA': {
        'color': 'var(--series-ghana)',
        'stroke_dash': '6,4',
        'marker': 'diamond',
        'css_class': 'series-ghana',
    },
    'KEN': {
        'color': 'var(--series-kenya)',
        'stroke_dash': '2,3',
        'marker': 'square',
        'css_class': 'series-kenya',
    },
}


@dataclass(frozen=True)
class ChartPoint:
    year: int
    value: Decimal | None
    is_missing: bool
    formatted_value: str
    x: float
    y: float
    marker_type: str
    country_code: str
    country_name: str


@dataclass(frozen=True)
class ChartSegment:
    country_code: str
    path_d: str
    stroke_dash: str
    color: str
    css_class: str


@dataclass(frozen=True)
class ChartSeries:
    country_code: str
    country_name: str
    color: str
    stroke_dash: str
    marker: str
    css_class: str
    segments: list[ChartSegment]
    points: list[ChartPoint]
    end_label_x: float | None
    end_label_y: float | None
    latest_observation_year: int | None
    latest_formatted_value: str | None


@dataclass(frozen=True)
class YearLensCountryItem:
    country_code: str
    country_name: str
    color: str
    marker: str
    stroke_dash: str
    css_class: str
    formatted_value: str
    is_missing: bool
    is_latest: bool
    snapshot_latest_year: int | None


@dataclass(frozen=True)
class YearLensData:
    selected_year: int
    prev_year: int | None
    next_year: int | None
    country_items: list[YearLensCountryItem]
    unit: str
    all_range_years: list[int]


@dataclass(frozen=True)
class TableCell:
    country_code: str
    country_name: str
    formatted_value: str
    is_missing: bool


@dataclass(frozen=True)
class TableRow:
    year: int
    is_selected: bool
    cells: list[TableCell]


@dataclass(frozen=True)
class ExploreContext:
    snapshot: Snapshot
    indicator_code: str
    indicator_name: str
    indicator_short_label: str
    indicator_definition: str
    unit: str
    source_url: str
    provider_label: str
    underlying_source: str
    license: str
    retrieved_at: Any
    content_hash: str
    version: int

    selected_countries: list[str]
    available_countries: list[tuple[str, str]]
    available_indicators: list[tuple[str, str]]

    start_year: int
    end_year: int
    selected_year: int
    view_mode: str

    scope_start_year: int
    scope_end_year: int

    chart_series_list: list[ChartSeries]
    crosshair_x: float | None
    chart_years: list[tuple[int, float]]
    chart_y_ticks: list[tuple[int, float]]

    year_lens: YearLensData
    table_rows: list[TableRow]
    table_columns: list[tuple[str, str]]

    notifications: list[str]
    warning_notification: str | None

    has_revisions: bool
    revision_count: int

    snapshot_latest_years: dict[str, int | None]
    provenance: dict[str, Any]


def get_explore_context(
    snapshot: Snapshot,
    indicator_code: str | None = None,
    country_codes: list[str] | None = None,
    start_year: int | str | None = None,
    end_year: int | str | None = None,
    selected_year: int | str | None = None,
    view_mode: str = 'chart',
    filter_submitted: bool = False,
) -> ExploreContext:
    """
    Builds the complete presentation and data context for CiteGrid's Explore interface.
    Validates parameters, verifies snapshot-wide stored provenance, calculates
    gap-splitting SVG chart lines, builds the year lens and semantic table data.
    """
    notifications: list[str] = []
    warning_notification: str | None = None

    # 1. Indicator validation
    if not indicator_code or indicator_code not in VALID_INDICATORS:
        indicator_code = VALID_INDICATORS[0]  # Total electricity access

    # 2. Country validation & submission distinction
    if filter_submitted:
        if not country_codes:
            warning_notification = "At least one country must remain selected. Preserved other filters and reselected all countries."
            selected_countries = list(VALID_COUNTRIES)
        else:
            valid_selected = [c for c in VALID_COUNTRIES if c in country_codes]
            if not valid_selected:
                warning_notification = "At least one valid country must remain selected. Reset to all countries."
                selected_countries = list(VALID_COUNTRIES)
            else:
                selected_countries = valid_selected
    else:
        if country_codes is None:
            selected_countries = list(VALID_COUNTRIES)
        else:
            valid_selected = [c for c in VALID_COUNTRIES if c in country_codes]
            if not valid_selected:
                warning_notification = "Unrecognized country selection. Reset to all countries."
                selected_countries = list(VALID_COUNTRIES)
            else:
                selected_countries = valid_selected

    # 3. Year range parsing & bounding
    scope_start = snapshot.scope_start_year
    scope_end = snapshot.scope_end_year

    def _parse_year(val: Any, default: int) -> int:
        if val is None or val == '':
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    parsed_start = _parse_year(start_year, scope_start)
    parsed_end = _parse_year(end_year, scope_end)

    if parsed_start > parsed_end:
        warning_notification = (
            f"Invalid year range: start year ({parsed_start}) cannot be greater than end year ({parsed_end}). "
            f"Reset to full available range."
        )
        start_year_int = scope_start
        end_year_int = scope_end
    else:
        if parsed_start < scope_start:
            notifications.append(f"Start year {parsed_start} is before data scope ({scope_start}); adjusted to {scope_start}.")
            start_year_int = scope_start
        else:
            start_year_int = parsed_start

        if parsed_end > scope_end:
            notifications.append(f"End year {parsed_end} is after data scope ({scope_end}); adjusted to {scope_end}.")
            end_year_int = scope_end
        else:
            end_year_int = parsed_end

    # 4. Snapshot-wide Provenance Extraction & Conflict Check
    # Must check across the WHOLE snapshot for this indicator, not just displayed range
    snapshot_indicator_values = list(
        SnapshotValue.objects.filter(snapshot=snapshot, indicator_code=indicator_code)
    )
    if not snapshot_indicator_values:
        raise ValueError(f"No stored records found for indicator {indicator_code} in Snapshot #{snapshot.id}")

    sample_val = snapshot_indicator_values[0]
    checked_fields = [
        'indicator_name',
        'indicator_definition',
        'unit',
        'source_url',
        'provider_label',
        'underlying_source',
        'license',
    ]
    for row in snapshot_indicator_values:
        for fld in checked_fields:
            if getattr(row, fld) != getattr(sample_val, fld):
                raise ValueError(
                    f"Conflicting stored provenance for indicator {indicator_code} on field '{fld}': "
                    f"'{getattr(sample_val, fld)}' vs '{getattr(row, fld)}'"
                )

    provenance = {
        'indicator_code': sample_val.indicator_code,
        'indicator_name': sample_val.indicator_name,
        'indicator_definition': sample_val.indicator_definition,
        'unit': sample_val.unit,
        'source_url': sample_val.source_url,
        'provider_label': sample_val.provider_label,
        'underlying_source': sample_val.underlying_source,
        'license': sample_val.license,
        'source_name': snapshot.source_name,
        'version': snapshot.version,
        'content_hash': snapshot.content_hash,
        'retrieved_at': snapshot.retrieved_at,
    }

    # 5. Snapshot-wide latest available year per country
    snapshot_latest_years: dict[str, int | None] = {}
    for country in VALID_COUNTRIES:
        max_y = (
            SnapshotValue.objects.filter(
                snapshot=snapshot,
                indicator_code=indicator_code,
                country_code=country,
                numeric_value__isnull=False,
            ).aggregate(models.Max('observation_year'))['observation_year__max']
        )
        snapshot_latest_years[country] = max_y

    # 6. Selected year resolution (strictly bounded within [start_year_int, end_year_int])
    if selected_year is None or selected_year == '':
        in_range_max = (
            SnapshotValue.objects.filter(
                snapshot=snapshot,
                indicator_code=indicator_code,
                country_code__in=selected_countries,
                observation_year__gte=start_year_int,
                observation_year__lte=end_year_int,
                numeric_value__isnull=False,
            ).aggregate(models.Max('observation_year'))['observation_year__max']
        )
        if in_range_max is not None:
            selected_year_int = in_range_max
        else:
            selected_year_int = end_year_int
    else:
        try:
            req_year = int(selected_year)
            if req_year < start_year_int:
                notifications.append(
                    f"Selected year {req_year} is before the active range ({start_year_int}–{end_year_int}); "
                    f"moved to {start_year_int}."
                )
                selected_year_int = start_year_int
            elif req_year > end_year_int:
                notifications.append(
                    f"Selected year {req_year} is after the active range ({start_year_int}–{end_year_int}); "
                    f"moved to {end_year_int}."
                )
                selected_year_int = end_year_int
            else:
                selected_year_int = req_year
        except (ValueError, TypeError):
            selected_year_int = end_year_int

    # 7. Query values for active display range
    display_rows = SnapshotValue.objects.filter(
        snapshot=snapshot,
        indicator_code=indicator_code,
        country_code__in=selected_countries,
        observation_year__gte=start_year_int,
        observation_year__lte=end_year_int,
    )
    values_map: dict[tuple[str, int], SnapshotValue] = {
        (row.country_code, row.observation_year): row for row in display_rows
    }

    # 8. SVG Chart geometry and gap-splitting path calculation
    # ViewBox: 0 0 800 400
    # Left margin 60, Right margin 90, Top 40, Bottom 50
    svg_width = 800.0
    svg_height = 400.0
    margin_left = 60.0
    margin_right = 90.0
    margin_top = 40.0
    margin_bottom = 50.0

    plot_width = svg_width - margin_left - margin_right
    plot_height = svg_height - margin_top - margin_bottom

    def _x_for_year(y: int) -> float:
        if end_year_int == start_year_int:
            return margin_left + plot_width / 2.0
        return margin_left + ((y - start_year_int) / (end_year_int - start_year_int)) * plot_width

    def _y_for_val(v: Decimal | float) -> float:
        # Scale 0 to 100%
        val_float = float(v)
        val_clamped = max(0.0, min(100.0, val_float))
        return margin_top + ((100.0 - val_clamped) / 100.0) * plot_height

    chart_series_list: list[ChartSeries] = []

    for country in selected_countries:
        style = SERIES_STYLES[country]
        country_name = COUNTRY_NAMES[country]

        points: list[ChartPoint] = []
        segments: list[ChartSegment] = []

        # Build segments of continuous observations to guarantee NO lines across missing years
        current_segment_points: list[tuple[float, float]] = []

        latest_obs_year: int | None = None
        latest_formatted: str | None = None
        last_x: float | None = None
        last_y: float | None = None

        for y in range(start_year_int, end_year_int + 1):
            val_rec = values_map.get((country, y))
            x_pos = _x_for_year(y)

            if val_rec and val_rec.numeric_value is not None:
                val = val_rec.numeric_value
                y_pos = _y_for_val(val)
                fmt_val = f"{val.normalize():f}%"
                pt = ChartPoint(
                    year=y,
                    value=val,
                    is_missing=False,
                    formatted_value=fmt_val,
                    x=round(x_pos, 1),
                    y=round(y_pos, 1),
                    marker_type=style['marker'],
                    country_code=country,
                    country_name=country_name,
                )
                points.append(pt)
                current_segment_points.append((round(x_pos, 1), round(y_pos, 1)))

                latest_obs_year = y
                latest_formatted = fmt_val
                last_x = round(x_pos, 1)
                last_y = round(y_pos, 1)
            else:
                # Gap / missing observation: end current segment
                if current_segment_points:
                    path_d = _build_svg_path(current_segment_points)
                    segments.append(
                        ChartSegment(
                            country_code=country,
                            path_d=path_d,
                            stroke_dash=style['stroke_dash'],
                            color=style['color'],
                            css_class=style['css_class'],
                        )
                    )
                    current_segment_points = []

                # Still add point entry for missing observation (without SVG mark, but for data parity)
                points.append(
                    ChartPoint(
                        year=y,
                        value=None,
                        is_missing=True,
                        formatted_value="No data",
                        x=round(x_pos, 1),
                        y=0.0,
                        marker_type=style['marker'],
                        country_code=country,
                        country_name=country_name,
                    )
                )

        if current_segment_points:
            path_d = _build_svg_path(current_segment_points)
            segments.append(
                ChartSegment(
                    country_code=country,
                    path_d=path_d,
                    stroke_dash=style['stroke_dash'],
                    color=style['color'],
                    css_class=style['css_class'],
                )
            )

        end_lbl_x = (last_x + 10.0) if last_x is not None else None
        end_lbl_y = (last_y + 4.0) if last_y is not None else None

        chart_series_list.append(
            ChartSeries(
                country_code=country,
                country_name=country_name,
                color=style['color'],
                stroke_dash=style['stroke_dash'],
                marker=style['marker'],
                css_class=style['css_class'],
                segments=segments,
                points=points,
                end_label_x=end_lbl_x,
                end_label_y=end_lbl_y,
                latest_observation_year=latest_obs_year,
                latest_formatted_value=latest_formatted,
            )
        )

    # Crosshair position at selected_year
    crosshair_x: float | None = round(_x_for_year(selected_year_int), 1)

    # Chart Year axis ticks (e.g. every 5 years or start/end/mid)
    chart_years: list[tuple[int, float]] = []
    step = 5 if (end_year_int - start_year_int) >= 10 else (2 if (end_year_int - start_year_int) >= 4 else 1)
    for yr in range(start_year_int, end_year_int + 1):
        if yr == start_year_int or yr == end_year_int or yr % step == 0:
            chart_years.append((yr, round(_x_for_year(yr), 1)))

    # Chart Y axis ticks: 0%, 25%, 50%, 75%, 100%
    chart_y_ticks: list[tuple[int, float]] = [
        (pct, round(_y_for_val(pct), 1)) for pct in [0, 25, 50, 75, 100]
    ]

    # 9. Year Lens Data
    prev_y = (selected_year_int - 1) if selected_year_int > start_year_int else None
    next_y = (selected_year_int + 1) if selected_year_int < end_year_int else None

    lens_items: list[YearLensCountryItem] = []
    for country in selected_countries:
        style = SERIES_STYLES[country]
        val_rec = values_map.get((country, selected_year_int))
        if val_rec and val_rec.numeric_value is not None:
            f_val = f"{val_rec.numeric_value.normalize():f}%"
            is_miss = False
        else:
            f_val = "No data in this snapshot"
            is_miss = True

        is_latest = (snapshot_latest_years.get(country) == selected_year_int)
        lens_items.append(
            YearLensCountryItem(
                country_code=country,
                country_name=COUNTRY_NAMES[country],
                color=style['color'],
                marker=style['marker'],
                stroke_dash=style['stroke_dash'],
                css_class=style['css_class'],
                formatted_value=f_val,
                is_missing=is_miss,
                is_latest=is_latest,
                snapshot_latest_year=snapshot_latest_years.get(country),
            )
        )

    all_range_years = list(range(start_year_int, end_year_int + 1))
    year_lens = YearLensData(
        selected_year=selected_year_int,
        prev_year=prev_y,
        next_year=next_y,
        country_items=lens_items,
        unit=sample_val.unit,
        all_range_years=all_range_years,
    )

    # 10. Semantic Table Model
    table_rows: list[TableRow] = []
    for y in range(start_year_int, end_year_int + 1):
        cells: list[TableCell] = []
        for country in selected_countries:
            val_rec = values_map.get((country, y))
            if val_rec and val_rec.numeric_value is not None:
                c_val = f"{val_rec.numeric_value.normalize():f}%"
                c_miss = False
            else:
                c_val = "No data"
                c_miss = True
            cells.append(
                TableCell(
                    country_code=country,
                    country_name=COUNTRY_NAMES[country],
                    formatted_value=c_val,
                    is_missing=c_miss,
                )
            )
        table_rows.append(
            TableRow(
                year=y,
                is_selected=(y == selected_year_int),
                cells=cells,
            )
        )

    table_columns = [(c, COUNTRY_NAMES[c]) for c in selected_countries]

    # 11. Revisions Check against previous snapshot
    revision_count = Revision.objects.filter(current_snapshot=snapshot).count()
    has_revisions = (revision_count > 0)

    # 12. View Mode
    valid_view = view_mode if view_mode in ('chart', 'table') else 'chart'

    available_countries = [(c, COUNTRY_NAMES[c]) for c in VALID_COUNTRIES]
    available_indicators = [
        (VALID_INDICATORS[0], "Total"),
        (VALID_INDICATORS[1], "Rural"),
        (VALID_INDICATORS[2], "Urban"),
    ]

    return ExploreContext(
        snapshot=snapshot,
        indicator_code=indicator_code,
        indicator_name=(
            get_indicator_provenance(indicator_code)['name']
            if sample_val.indicator_name == indicator_code else sample_val.indicator_name
        ),
        indicator_short_label=dict(available_indicators)[indicator_code],
        indicator_definition=sample_val.indicator_definition,
        unit=sample_val.unit,
        source_url=sample_val.source_url,
        provider_label=sample_val.provider_label,
        underlying_source=sample_val.underlying_source,
        license=sample_val.license,
        retrieved_at=snapshot.retrieved_at,
        content_hash=snapshot.content_hash,
        version=snapshot.version,
        selected_countries=selected_countries,
        available_countries=available_countries,
        available_indicators=available_indicators,
        start_year=start_year_int,
        end_year=end_year_int,
        selected_year=selected_year_int,
        view_mode=valid_view,
        scope_start_year=scope_start,
        scope_end_year=scope_end,
        chart_series_list=chart_series_list,
        crosshair_x=crosshair_x,
        chart_years=chart_years,
        chart_y_ticks=chart_y_ticks,
        year_lens=year_lens,
        table_rows=table_rows,
        table_columns=table_columns,
        notifications=notifications,
        warning_notification=warning_notification,
        has_revisions=has_revisions,
        revision_count=revision_count,
        snapshot_latest_years=snapshot_latest_years,
        provenance=provenance,
    )


def _build_svg_path(points: list[tuple[float, float]]) -> str:
    """Constructs SVG path command string from a list of (x, y) coordinates."""
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        # Emit a short horizontal line so single-observation points render a visible mark
        return f"M {x - 2.0:.1f},{y:.1f} L {x + 2.0:.1f},{y:.1f}"

    cmds = [f"M {points[0][0]:.1f},{points[0][1]:.1f}"]
    for x, y in points[1:]:
        cmds.append(f"L {x:.1f},{y:.1f}")
    return " ".join(cmds)

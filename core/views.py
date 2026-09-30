from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.models import ImportRun, ImportStatus, Revision, SavedComparison, Snapshot
from core.provenance import get_indicator_provenance
from core.services.comparisons import get_comparison_grid
from core.services.exporter import package_comparison_export
from core.services.explore import get_explore_context
from core.services.revisions import _build_inspection_detail, get_revisions_context, snapshot_simulation_status
from core.services.replay import get_replay_context


@require_GET
def index(request):
    """
    CiteGrid Explore View — the signature evidence studio.
    Renders interactive comparison, accessible SVG chart, year lens, semantic data table,
    and source trail. Fully functional with and without JavaScript.
    """
    latest_snapshot = Snapshot.objects.filter(is_latest=True).first()
    if not latest_snapshot:
        return render(
            request,
            'core/explore_empty.html',
            {
                'active_nav': 'explore',
                'headline': "Know which figure you’re citing.",
                'data_status': "No data imported yet.",
            },
        )

    filter_submitted = (request.GET.get('filter_submitted') == '1')
    indicator_code = request.GET.get('indicator')
    country_codes = request.GET.getlist('countries') if 'countries' in request.GET else None
    start_year = request.GET.get('start_year')
    end_year = request.GET.get('end_year')
    selected_year = request.GET.get('year')

    if 'view' in request.GET:
        view_mode = request.GET.get('view')
        request.session['explore_view_mode'] = view_mode
    else:
        view_mode = request.session.get('explore_view_mode', 'chart')

    explore_ctx = get_explore_context(
        snapshot=latest_snapshot,
        indicator_code=indicator_code,
        country_codes=country_codes,
        start_year=start_year,
        end_year=end_year,
        selected_year=selected_year,
        view_mode=view_mode,
        filter_submitted=filter_submitted,
    )

    context = {
        'ctx': explore_ctx,
        'snapshot_is_simulation': snapshot_simulation_status([latest_snapshot.pk]).get(latest_snapshot.pk, False),
        'provenance': explore_ctx.provenance,
        'active_nav': 'explore',
        'query_params': request.GET,
    }
    return render(request, 'core/explore.html', context)


@require_GET
def revisions_view(request):
    """
    CiteGrid Revisions Ledger View.
    Displays changes across adjacent published snapshots with truthful scope,
    country/transition filters, and inline inspection drawer.
    """
    country = request.GET.get('country')
    transition = request.GET.get('transition')
    snapshot = request.GET.get('snapshot')
    since = request.GET.get('since')
    inspect = request.GET.get('inspect')

    rev_ctx = get_revisions_context(
        country=country,
        transition=transition,
        snapshot_version=snapshot,
        since_version=since,
        inspect_id=inspect,
    )

    provenance = None
    if rev_ctx.latest_snapshot:
        val = rev_ctx.latest_snapshot.values.first()
        if val:
            provenance = {
                'provider_label': val.provider_label,
                'underlying_source': val.underlying_source,
                'license': val.license,
                'source_name': rev_ctx.latest_snapshot.source_name,
                'version': rev_ctx.latest_snapshot.version,
                'content_hash': rev_ctx.latest_snapshot.content_hash,
                'retrieved_at': rev_ctx.latest_snapshot.retrieved_at,
            }

    context = {
        'ctx': rev_ctx,
        'provenance': provenance,
        'active_nav': 'revisions',
        'query_params': request.GET,
    }
    return render(request, 'core/revisions.html', context)


@require_GET
def revision_detail_view(request, pk: int):
    """
    Standalone revision inspection view for deep links and accessible fallbacks.
    Reads paired evidence directly from stored records.
    """
    rev = get_object_or_404(
        Revision.objects.select_related(
            'previous_snapshot',
            'current_snapshot',
            'old_snapshot_value',
            'new_snapshot_value',
        ),
        pk=pk,
    )

    simulation_by_snapshot = snapshot_simulation_status(
        [rev.previous_snapshot_id, rev.current_snapshot_id]
    )
    is_simulation = (
        simulation_by_snapshot.get(rev.previous_snapshot_id, False)
        or simulation_by_snapshot.get(rev.current_snapshot_id, False)
    )

    inspection = _build_inspection_detail(rev, is_simulation)

    provenance = {
        'provider_label': rev.new_provider_label or rev.old_provider_label,
        'underlying_source': rev.new_underlying_source or rev.old_underlying_source,
        'license': rev.new_license or rev.old_license,
        'source_name': rev.current_snapshot.source_name,
        'version': rev.current_snapshot.version,
        'content_hash': rev.current_snapshot.content_hash,
        'retrieved_at': rev.current_retrieved_at,
    }

    context = {
        'inspection': inspection,
        'provenance': provenance,
        'active_nav': 'revisions',
    }
    return render(request, 'core/revision_detail.html', context)


@require_GET
def about_view(request):
    """
    About the data and import history view.
    Explains the fixed scope, data provider, CC BY 4.0 license, immutable snapshots,
    and displays the durable audit log of ImportRun records.
    """
    latest_snapshot = Snapshot.objects.filter(is_latest=True).first()
    provenance = None
    if latest_snapshot:
        val = latest_snapshot.values.first()
        if val:
            provenance = {
                'provider_label': val.provider_label,
                'underlying_source': val.underlying_source,
                'license': val.license,
                'source_name': latest_snapshot.source_name,
                'version': latest_snapshot.version,
                'content_hash': latest_snapshot.content_hash,
                'retrieved_at': latest_snapshot.retrieved_at,
            }

    raw_runs = ImportRun.objects.select_related('snapshot').order_by('-started_at')[:30]
    import_runs = []
    has_simulated_runs = False

    for r in raw_runs:
        if r.is_simulation:
            has_simulated_runs = True

        duration_sec = None
        if r.ended_at and r.started_at:
            duration_sec = round((r.ended_at - r.started_at).total_seconds(), 2)

        # Generate safe human-readable summary without raw traces or unreviewed URLs
        if r.status == ImportStatus.COMPLETED:
            if r.snapshot:
                if r.snapshot.published_at and r.snapshot.published_at >= r.started_at:
                    summary = f"Published Snapshot v{r.snapshot.version} ({r.records_count} records)"
                else:
                    summary = f"Repeat content confirmed (matches Snapshot v{r.snapshot.version})"
            else:
                summary = "Completed successfully"
        elif r.status == ImportStatus.FAILED:
            cat = r.error_category or "UNKNOWN_ERROR"
            summary = f"Import halted: {cat}. Database publication transaction rolled back; earlier snapshots untouched."
        elif r.status == ImportStatus.STARTED:
            summary = "Import in progress"
        else:
            summary = f"Status: {r.status}"

        import_runs.append({
            'run': r,
            'duration_sec': duration_sec,
            'summary': summary,
            'is_simulation': r.is_simulation,
        })

    context = {
        'latest_snapshot': latest_snapshot,
        'snapshot_is_simulation': (
            snapshot_simulation_status([latest_snapshot.pk]).get(latest_snapshot.pk, False)
            if latest_snapshot else False
        ),
        'provenance': provenance,
        'import_runs': import_runs,
        'has_simulated_runs': has_simulated_runs,
        'active_nav': 'about',
    }
    return render(request, 'core/about.html', context)


def _render_comparison(request, comparison: SavedComparison):
    """Internal helper to construct template context and render a comparison briefing."""
    grid = get_comparison_grid(comparison)
    columns = [f"{grid.country_names.get(c, c)} ({c})" for c in grid.countries]

    table_rows = []
    for y in grid.years:
        cells = [
            {'label': grid.country_names.get(c, c), 'value': grid.matrix[y].get(c)}
            for c in grid.countries
        ]
        table_rows.append({'year': y, 'cells': cells})

    latest_years = [
        {
            'country_name': grid.country_names.get(c, c),
            'country_code': c,
            'latest_year': grid.latest_available_years.get(c),
        }
        for c in grid.countries
    ]

    provenance = {
        'provider_label': grid.provider_label,
        'underlying_source': grid.underlying_source,
        'license': grid.license,
        'source_name': grid.source_name,
        'version': grid.snapshot.version,
        'content_hash': grid.snapshot.content_hash,
        'retrieved_at': grid.retrieved_at,
    }

    latest_snapshot = Snapshot.objects.filter(is_latest=True).first()
    has_newer_snapshot = bool(latest_snapshot and (latest_snapshot.version > comparison.snapshot.version))

    context = {
        'grid': grid,
        'snapshot_is_simulation': grid.is_simulation,
        'display_indicator_name': (
            get_indicator_provenance(grid.indicator_code)['name']
            if grid.indicator_name == grid.indicator_code else grid.indicator_name
        ),
        'columns': columns,
        'table_rows': table_rows,
        'latest_years': latest_years,
        'provenance': provenance,
        'active_nav': 'briefing',
        'latest_snapshot': latest_snapshot,
        'has_newer_snapshot': has_newer_snapshot,
    }
    return render(request, 'core/comparison_detail.html', context)


@require_GET
def public_example_view(request):
    """
    Renders the seeded public read-only example comparison.
    If no snapshot or seeded example exists yet, renders the empty state.
    """
    example = SavedComparison.objects.filter(is_public_example=True).first()
    if not example:
        return render(request, 'core/comparison_empty.html')

    return _render_comparison(request, example)


@require_GET
def public_example_export_view(request):
    """
    Downloads the deterministic ZIP export package for the public read-only example.
    Returns 404 if no public example exists yet.
    """
    example = SavedComparison.objects.filter(is_public_example=True).first()
    if not example:
        raise Http404("Public example data is not ready yet.")

    zip_bytes = package_comparison_export(example)
    filename = f"citegrid_comparison_example_v{example.snapshot.version}.zip"

    response = HttpResponse(zip_bytes, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_GET
def comparison_detail_view(request, pk: int):
    """
    Renders the pinned comparison briefing page for a given ID.
    Restricted: public anonymous users can only access rows where is_public_example=True.
    """
    comparison = get_object_or_404(SavedComparison, pk=pk)

    if not comparison.is_public_example:
        if not (request.user.is_authenticated and comparison.owner_id == request.user.id):
            return HttpResponseForbidden("Access denied. This comparison is not public.")

    return _render_comparison(request, comparison)


@require_GET
def comparison_export_view(request, pk: int):
    """
    Downloads the deterministic ZIP export package for a pinned comparison.
    Restricted: public anonymous users can only access rows where is_public_example=True.
    """
    comparison = get_object_or_404(SavedComparison, pk=pk)

    if not comparison.is_public_example:
        if not (request.user.is_authenticated and comparison.owner_id == request.user.id):
            return HttpResponseForbidden("Access denied. This comparison is not public.")

    zip_bytes = package_comparison_export(comparison)
    filename = f"citegrid_comparison_{comparison.id}_v{comparison.snapshot.version}.zip"

    response = HttpResponse(zip_bytes, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@require_GET
def replay_view(request):
    """
    CiteGrid Fixture Replay View.
    Demonstrates in-memory 4-step revision sequence and failed-page scenario.
    CRITICAL: Causes ZERO database mutations.
    """
    scenario = request.GET.get('scenario', 'revisions')
    step = request.GET.get('step', '1')
    indicator = request.GET.get('indicator', 'EG.ELC.ACCS.ZS')
    year = request.GET.get('year', None)

    replay_ctx = get_replay_context(
        scenario=scenario,
        step=step,
        indicator_code=indicator,
        selected_year=year,
    )

    context = {
        'ctx': replay_ctx,
        'active_nav': 'replay',
        'query_params': request.GET,
    }
    return render(request, 'core/replay.html', context)


@require_GET
def health_check(request):
    """Simple JSON health check for monitoring and offline test verification."""
    return JsonResponse({'status': 'ok', 'app': 'citegrid', 'version': '0.1.0'})

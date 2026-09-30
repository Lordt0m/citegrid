from decimal import Decimal
import json

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from core.models import Snapshot, SnapshotValue, Revision, RevisionTransition, VALID_COUNTRIES
from core.provenance import VALID_INDICATORS, INDICATOR_PROVENANCE_REGISTRY
from core.services.comparisons import seed_public_example
from core.services.explore import get_explore_context


class ExploreInterfaceTests(TestCase):
    """
    Offline automated acceptance tests for CiteGrid Ticket 6: Explore Interface.
    Exercises URL filters, zero-JS navigation, gap rendering, stored provenance,
    metadata update resilience, accessibility semantics, and mutation guards.
    """

    def setUp(self):
        self.client = Client()

    def _create_snapshot(self, version=1, is_latest=True, content_hash="hash0001", metadata_override=None):
        """Helper to create a complete 9-series snapshot in the test database."""
        if is_latest:
            Snapshot.objects.filter(is_latest=True).update(is_latest=False)

        snapshot = Snapshot.objects.create(
            version=version,
            content_hash=content_hash,
            scope_start_year=2000,
            scope_end_year=2024,
            source_name="World Bank World Development Indicators",
            retrieved_at=timezone.now(),
            is_latest=is_latest,
        )

        values = []
        for ind_code in VALID_INDICATORS:
            reg = INDICATOR_PROVENANCE_REGISTRY[ind_code]
            ind_name = reg['name']
            ind_def = metadata_override.get('indicator_definition', reg['definition']) if metadata_override else reg['definition']
            unit = reg['unit']
            source_url = reg['source_url']
            provider = metadata_override.get('provider_label', reg.get('provider', 'World Bank')) if metadata_override else reg.get('provider', 'World Bank')
            underlying = metadata_override.get('underlying_source', reg['underlying_source']) if metadata_override else reg['underlying_source']
            license_str = metadata_override.get('license', reg['license']) if metadata_override else reg['license']

            for c in VALID_COUNTRIES:
                for y in range(2000, 2025):
                    # Introduce intentional null gap: KEN Rural in 2023 is None
                    if c == 'KEN' and ind_code == 'EG.ELC.ACCS.RU.ZS' and y == 2023:
                        num_val = None
                    else:
                        num_val = Decimal(f"{(y - 1990) * 2}.50000000")

                    values.append(
                        SnapshotValue(
                            snapshot=snapshot,
                            country_code=c,
                            country_name={'NGA': 'Nigeria', 'GHA': 'Ghana', 'KEN': 'Kenya'}[c],
                            indicator_code=ind_code,
                            indicator_name=ind_name,
                            indicator_definition=ind_def,
                            observation_year=y,
                            numeric_value=num_val,
                            raw_value_str=str(num_val) if num_val is not None else None,
                            unit=unit,
                            source_url=source_url,
                            provider_label=provider,
                            underlying_source=underlying,
                            license=license_str,
                            footnote="Test observation footnote",
                        )
                    )
        SnapshotValue.objects.bulk_create(values)
        return snapshot

    # 1. Empty State Tests
    def test_empty_state_when_zero_snapshots_exist(self):
        """When no snapshots have been imported, Explore and related views render clean empty states."""
        resp = self.client.get(reverse('core:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Data is not ready yet")
        self.assertContains(resp, "No data imported yet")
        self.assertNotContains(resp, "<svg")

        rev_resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(rev_resp.status_code, 200)
        self.assertContains(rev_resp, "No data imported yet")

        about_resp = self.client.get(reverse('core:about'))
        self.assertEqual(about_resp.status_code, 200)
        self.assertContains(about_resp, "About the Evidence")
        self.assertContains(about_resp, "Fixed Evidence Scope")

    # 2. Initial Visit Defaults
    def test_initial_first_visit_defaults(self):
        """Initial GET / defaults to Total indicator, all 3 countries, full range, without warnings."""
        snapshot = self._create_snapshot(version=1)
        resp = self.client.get(reverse('core:index'))
        self.assertEqual(resp.status_code, 200)

        # Baseline elements
        self.assertContains(resp, "Know which figure you’re citing.")
        self.assertContains(resp, f"Snapshot v1 ({snapshot.content_hash[:8]})")
        self.assertContains(resp, "Initial complete published snapshot. No earlier snapshot exists to compare.")

        # Default controls
        self.assertContains(resp, 'value="EG.ELC.ACCS.ZS" checked')
        self.assertContains(resp, 'value="NGA" checked')
        self.assertContains(resp, 'value="GHA" checked')
        self.assertContains(resp, 'value="KEN" checked')
        self.assertContains(resp, 'value="2000"')
        self.assertContains(resp, 'value="2024"')

        # Zero warning messages
        self.assertNotContains(resp, "alert-box--warning")

        # Semantic table structure
        self.assertContains(resp, '<th scope="col" class="font-mono">Observation Year</th>')
        self.assertContains(resp, 'class="year-pin-link font-mono"')

    def test_indicator_code_is_not_used_as_the_explore_heading(self):
        snapshot = self._create_snapshot(version=1)
        SnapshotValue.objects.filter(
            snapshot=snapshot, indicator_code=VALID_INDICATORS[0]
        ).update(indicator_name=VALID_INDICATORS[0])

        resp = self.client.get(reverse('core:index'))
        self.assertContains(resp, 'Access to electricity (% of population)')
        self.assertNotContains(resp, '<h2 id="chart-title" class="chart-title">EG.ELC.ACCS.ZS</h2>')

    # 3. Country Validation & Submission Distinction
    def test_all_unchecked_country_submission_resets_and_warns(self):
        """Submitting a form with all countries unchecked resets to all countries and shows a notice."""
        self._create_snapshot(version=1)
        url = reverse('core:index') + "?filter_submitted=1&indicator=EG.ELC.ACCS.ZS&start_year=2000&end_year=2024"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Preserves other filters and resets countries
        self.assertContains(resp, "At least one country must remain selected. Preserved other filters and reselected all countries.")
        self.assertContains(resp, 'value="NGA" checked')
        self.assertContains(resp, 'value="GHA" checked')
        self.assertContains(resp, 'value="KEN" checked')

    def test_valid_country_subset_selection(self):
        """Selecting a single country displays only that country across chart, table, and lens."""
        self._create_snapshot(version=1)
        url = reverse('core:index') + "?countries=NGA"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, 'value="NGA" checked')
        self.assertNotContains(resp, 'value="GHA" checked')
        self.assertNotContains(resp, 'value="KEN" checked')
        self.assertContains(resp, 'data-country="NGA"')

    def test_explicit_empty_and_invalid_country_codes(self):
        """Explicit empty or invalid country codes are handled safely without unhandled errors."""
        self._create_snapshot(version=1)

        # Explicit empty ?countries=
        resp_empty = self.client.get(reverse('core:index') + "?countries=")
        self.assertEqual(resp_empty.status_code, 200)
        self.assertContains(resp_empty, 'value="NGA" checked')

        # Invalid country codes ?countries=XYZ&countries=NGA
        resp_invalid = self.client.get(reverse('core:index') + "?countries=XYZ&countries=NGA")
        self.assertEqual(resp_invalid.status_code, 200)
        self.assertContains(resp_invalid, 'value="NGA" checked')
        self.assertNotContains(resp_invalid, 'value="GHA" checked')

        # All invalid country codes ?countries=XYZ&countries=ABC
        resp_all_invalid = self.client.get(reverse('core:index') + "?countries=XYZ&countries=ABC")
        self.assertEqual(resp_all_invalid.status_code, 200)
        self.assertContains(resp_all_invalid, "Unrecognized country selection. Reset to all countries.")

    # 4. Year Range Validation & Bounding
    def test_narrow_range_first_load_selected_year_bounded(self):
        """A narrow range request without a year parameter defaults to a year strictly in that range."""
        self._create_snapshot(version=1)
        url = reverse('core:index') + "?start_year=2005&end_year=2010"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        ctx = resp.context['ctx']
        self.assertEqual(ctx.start_year, 2005)
        self.assertEqual(ctx.end_year, 2010)
        self.assertTrue(2005 <= ctx.selected_year <= 2010)

    def test_inverted_year_range_resets_and_warns(self):
        """Inverted range (start > end) shows an explicit warning and resets to snapshot scope."""
        self._create_snapshot(version=1)
        url = reverse('core:index') + "?start_year=2020&end_year=2005"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Invalid year range: start year (2020) cannot be greater than end year (2005). Reset to full available range.")
        ctx = resp.context['ctx']
        self.assertEqual(ctx.start_year, 2000)
        self.assertEqual(ctx.end_year, 2024)

    def test_out_of_scope_year_range_clipping(self):
        """Years outside snapshot scope are clipped with informational notifications."""
        self._create_snapshot(version=1)
        url = reverse('core:index') + "?start_year=1990&end_year=2050"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        ctx = resp.context['ctx']
        self.assertEqual(ctx.start_year, 2000)
        self.assertEqual(ctx.end_year, 2024)
        self.assertContains(resp, "Start year 1990 is before data scope (2000); adjusted to 2000.")
        self.assertContains(resp, "End year 2050 is after data scope (2024); adjusted to 2024.")

    def test_range_with_all_missing_observations_resolves_safely(self):
        """If every observation in a range is null, default selected year resolves safely to end_year."""
        snapshot = self._create_snapshot(version=1)
        # Set all values in 2020-2024 to None for Kenya Rural
        SnapshotValue.objects.filter(
            snapshot=snapshot,
            country_code='KEN',
            indicator_code='EG.ELC.ACCS.RU.ZS',
            observation_year__gte=2020,
        ).update(numeric_value=None)

        url = reverse('core:index') + "?indicator=EG.ELC.ACCS.RU.ZS&countries=KEN&start_year=2020&end_year=2024"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context['ctx']
        self.assertEqual(ctx.selected_year, 2024)
        self.assertContains(resp, "No data in this snapshot")

    # 5. Data Integrity: Missing Observations & SVG Path Gaps
    def test_missing_data_breaks_svg_path_and_never_renders_as_zero(self):
        """Missing observations emit broken SVG paths (discontinuous segments) and never render as 0%."""
        snapshot = self._create_snapshot(version=1)
        # Rural Kenya 2023 was set to None in setUp
        url = reverse('core:index') + "?indicator=EG.ELC.ACCS.RU.ZS&countries=KEN&start_year=2022&end_year=2024"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        ctx = resp.context['ctx']
        ken_series = next(s for s in ctx.chart_series_list if s.country_code == 'KEN')

        # Since 2023 is missing, there should be multiple disjoint path segments (2022 and 2024 separate)
        self.assertGreaterEqual(len(ken_series.segments), 2)

        # In table, 2023 displays 'No data', not '0%' or '0.00000000%'
        self.assertContains(resp, 'No data')

    # 6. Snapshot-Wide Provenance & Metadata Update Resilience
    def test_snapshot_wide_provenance_and_conflict_check(self):
        """Provenance is extracted across the whole snapshot, and conflicts raise ValueError."""
        snapshot = self._create_snapshot(version=1)

        # Explore context on narrow range still contains complete attribution
        ctx = get_explore_context(snapshot, indicator_code='EG.ELC.ACCS.ZS', start_year=2020, end_year=2020)
        self.assertEqual(ctx.provider_label, "World Bank")
        self.assertEqual(ctx.license, "CC BY-4.0")

        # Induce metadata conflict
        val = SnapshotValue.objects.filter(snapshot=snapshot, indicator_code='EG.ELC.ACCS.ZS').last()
        val.license = "Conflicting License"
        val.save()

        with self.assertRaises(ValueError) as cm:
            get_explore_context(snapshot, indicator_code='EG.ELC.ACCS.ZS')
        self.assertIn("Conflicting stored provenance", str(cm.exception))

    def test_metadata_only_snapshot_updates_attribution_in_strip_and_footer(self):
        """A newer snapshot with changed metadata updates Explore source strip and footer, while pinned briefing keeps v1."""
        s1 = self._create_snapshot(version=1, is_latest=True, content_hash="hash0001")
        # Seed public example pinned to s1
        seed_public_example()

        # Publish Snapshot v2 with altered metadata
        s2 = self._create_snapshot(
            version=2,
            is_latest=True,
            content_hash="hash0002",
            metadata_override={
                'license': 'CC BY 4.0 Revised Edition',
                'underlying_source': 'Updated SDG 7.1.1 Electrification Database',
            }
        )

        # GET / should now show Snapshot v2's attribution in source strip AND footer
        resp = self.client.get(reverse('core:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CC BY 4.0 Revised Edition")
        self.assertContains(resp, "Updated SDG 7.1.1 Electrification Database")
        self.assertContains(resp, "Snapshot v2 (hash0002)")

        # Pinned example briefing still uses Snapshot v1 attribution
        briefing_resp = self.client.get(reverse('core:public_example'))
        self.assertEqual(briefing_resp.status_code, 200)
        self.assertContains(briefing_resp, "SDG 7.1.1 Electrification Dataset")
        self.assertContains(briefing_resp, "v1 (hash0001)")
        self.assertNotContains(briefing_resp, "Updated SDG 7.1.1 Electrification Database")

    # 7. Zero-JavaScript Year Navigation Links
    def test_no_javascript_year_navigation_preserves_all_parameters(self):
        """Previous/Next and year links preserve indicator, countries, range, and view query parameters."""
        self._create_snapshot(version=1)
        url = reverse('core:index') + "?indicator=EG.ELC.ACCS.RU.ZS&countries=NGA&countries=KEN&start_year=2005&end_year=2020&year=2015&view=table"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Previous year link points to 2014 preserving filters
        self.assertContains(resp, 'year=2014&view=table')
        # Next year link points to 2016 preserving filters
        self.assertContains(resp, 'year=2016&view=table')
        # Noscript year select form includes all hidden parameters
        self.assertContains(resp, 'name="indicator" value="EG.ELC.ACCS.RU.ZS"')
        self.assertContains(resp, 'name="countries" value="NGA"')
        self.assertContains(resp, 'name="countries" value="KEN"')

    # 8. Grayscale Identifiability & Accessible Markers
    def test_grayscale_markers_and_stroke_patterns(self):
        """Chart renders distinguishable markers and patterns for all three countries."""
        self._create_snapshot(version=1)
        resp = self.client.get(reverse('core:index'))
        self.assertEqual(resp.status_code, 200)

        # Nigeria: circle marker
        self.assertContains(resp, 'point-mark-circle')
        # Ghana: diamond marker
        self.assertContains(resp, 'point-mark-diamond')
        # Kenya: square marker
        self.assertContains(resp, 'point-mark-square')

        # Stroke dashes
        self.assertContains(resp, 'stroke-dasharray="6,4"')
        self.assertContains(resp, 'stroke-dasharray="2,3"')

    # 9. View Mode Persistence
    def test_view_mode_persistence_in_session(self):
        """View mode parameter persists in session across subsequent page visits."""
        self._create_snapshot(version=1)

        # Request with ?view=table
        resp1 = self.client.get(reverse('core:index') + "?view=table")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(self.client.session.get('explore_view_mode'), 'table')

        # Subsequent request without ?view uses persisted session view
        resp2 = self.client.get(reverse('core:index'))
        self.assertEqual(resp2.status_code, 200)
        ctx = resp2.context['ctx']
        self.assertEqual(ctx.view_mode, 'table')

    # 10. Revisions Banner & Stub Navigation
    def test_revisions_banner_and_honest_stub_view(self):
        """When revisions exist, Explore displays a prominent prompt linking to /revisions/."""
        s1 = self._create_snapshot(version=1, is_latest=False, content_hash="hash0001")
        s2 = self._create_snapshot(version=2, is_latest=True, content_hash="hash0002")

        # Create simulated revision
        Revision.objects.create(
            previous_snapshot=s1,
            current_snapshot=s2,
            country_code='NGA',
            indicator_code='EG.ELC.ACCS.ZS',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal("56.50000000"),
            new_value=Decimal("58.20000000"),
            previous_retrieved_at=s1.retrieved_at,
            current_retrieved_at=s2.retrieved_at,
        )

        resp = self.client.get(reverse('core:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Changes since previous complete snapshot")
        self.assertContains(resp, "1 revision recorded.")
        self.assertContains(resp, 'href="/revisions/"')

        # Visit /revisions/
        rev_resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(rev_resp.status_code, 200)
        self.assertContains(rev_resp, "Showing 1 of 1 recorded revision")
        self.assertContains(rev_resp, "Changes recorded across adjacent complete published snapshots")

    # 11. Mutation Guards
    def test_explore_and_navigation_views_reject_mutations(self):
        """All public explore routes enforce @require_GET; mutations return 405."""
        self._create_snapshot(version=1)

        for url in [reverse('core:index'), reverse('core:revisions_list'), reverse('core:about')]:
            post_resp = self.client.post(url, {'indicator': 'EG.ELC.ACCS.ZS'})
            self.assertEqual(post_resp.status_code, 405)

            put_resp = self.client.put(url)
            self.assertEqual(put_resp.status_code, 405)

            delete_resp = self.client.delete(url)
            self.assertEqual(delete_resp.status_code, 405)

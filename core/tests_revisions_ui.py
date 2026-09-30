"""
Comprehensive offline test suite for Ticket 7:
Revisions ledger, revision inspection, neighbour context, simulated evidence separation,
pinned briefing newer-snapshot notice, and About the data import run history.
"""
from decimal import Decimal
import json
import os
from typing import Optional

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ImportRun,
    ImportStatus,
    Revision,
    RevisionTransition,
    SavedComparison,
    Snapshot,
    SnapshotValue,
)
from core.provenance import get_indicator_provenance


class RevisionsInterfaceTests(TestCase):
    """Offline test suite for Revisions ledger, inspection, and briefing updates."""

    def setUp(self):
        self.client = Client()
        self.now = timezone.now()

    def _create_snapshot(self, version: int, content_hash: str, is_latest: bool = True, retrieved_at=None) -> Snapshot:
        if is_latest:
            Snapshot.objects.filter(is_latest=True).update(is_latest=False)
        return Snapshot.objects.create(
            version=version,
            content_hash=content_hash,
            source_name='World Bank World Development Indicators',
            source_id='2',
            scope_start_year=2000,
            scope_end_year=2024,
            published_at=self.now,
            retrieved_at=retrieved_at or self.now,
            is_latest=is_latest,
        )

    def _create_snapshot_value(self, snapshot: Snapshot, country: str, year: int, val: Optional[Decimal], **kwargs) -> SnapshotValue:
        prov = get_indicator_provenance(kwargs.get('indicator_code', 'EG.ELC.ACCS.ZS'))
        defaults = {
            'snapshot': snapshot,
            'source_id': '2',
            'country_code': country,
            'country_name': 'Nigeria' if country == 'NGA' else ('Ghana' if country == 'GHA' else 'Kenya'),
            'indicator_code': kwargs.get('indicator_code', 'EG.ELC.ACCS.ZS'),
            'indicator_name': kwargs.get('indicator_name', 'Access to electricity (% of population)'),
            'observation_year': year,
            'numeric_value': val,
            'raw_value_str': str(val) if val is not None else None,
            'unit': kwargs.get('unit', '% of population'),
            'indicator_definition': kwargs.get('indicator_definition', prov['definition']),
            'provider_label': kwargs.get('provider_label', prov['provider']),
            'source_url': kwargs.get('source_url', prov['source_url']),
            'license': kwargs.get('license', prov['license']),
            'underlying_source': kwargs.get('underlying_source', prov['underlying_source']),
        }
        defaults.update(kwargs)
        return SnapshotValue.objects.create(**defaults)

    def test_empty_state_when_zero_snapshots_exist(self):
        """When no snapshots have been imported, Revisions view renders a clean empty state."""
        resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Data is not ready yet")
        self.assertContains(resp, "A complete, validated data import is required")

    def test_initial_snapshot_state_when_one_snapshot_exists(self):
        """When only Snapshot v1 exists, Revisions view explains there is no earlier snapshot to compare."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000')
        resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Initial Complete Published Snapshot")
        self.assertContains(resp, "There is no earlier snapshot to compare")
        self.assertContains(resp, "Snapshot v1")

    def test_simulated_initial_snapshot_does_not_claim_a_difference(self):
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000')
        ImportRun.objects.create(
            status=ImportStatus.COMPLETED,
            snapshot=snap1,
            is_simulation=True,
            records_count=225,
            pages_fetched=9,
        )
        resp = self.client.get(reverse('core:revisions_list'))
        self.assertContains(resp, "simulated test fixture data")
        self.assertNotContains(resp, "observed a difference between these retrieved snapshots")

    def test_no_revisions_state_when_multiple_snapshots_with_zero_revisions(self):
        """When multiple snapshots exist but zero revisions were recorded, renders calm no-revisions notice."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No Revisions Found")
        self.assertContains(resp, "No changes found between the complete snapshots stored so far")

    def test_three_snapshots_truthful_scope_and_revisions_list(self):
        """
        Tests sequence across three snapshots:
        - v1 -> v2 has 1 revision (Nigeria 2018 changed from 56.50% to 58.20%).
        - v2 -> v3 has 0 revisions.
        - Verifies ledger shows transitions, ?snapshot=2 filters v2, ?snapshot=3 shows 0 revisions, and scope headers are truthful.
        """
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap3 = self._create_snapshot(3, 'hash_v3_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        v1_nga = self._create_snapshot_value(snap1, 'NGA', 2018, Decimal('56.50000000'))
        v2_nga = self._create_snapshot_value(snap2, 'NGA', 2018, Decimal('58.20000000'))
        v3_nga = self._create_snapshot_value(snap3, 'NGA', 2018, Decimal('58.20000000'))

        rev1_2 = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            old_snapshot_value=v1_nga,
            new_snapshot_value=v2_nga,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity (% of population)',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.50000000'),
            new_value=Decimal('58.20000000'),
            old_raw_value_str='56.5',
            new_raw_value_str='58.2',
            old_unit='% of population',
            new_unit='% of population',
            old_provider_label='World Bank',
            new_provider_label='World Bank',
            old_license='CC BY 4.0',
            new_license='CC BY 4.0',
            old_underlying_source='SDG 7.1.1 electrification dataset',
            new_underlying_source='SDG 7.1.1 electrification dataset',
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        # 1. Default view shows all revisions across snapshots
        resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Changes recorded across adjacent complete published snapshots")
        self.assertContains(resp, "Nigeria")
        self.assertContains(resp, "2018")
        self.assertContains(resp, "56.50%")
        self.assertContains(resp, "58.20%")

        # 2. Filter ?snapshot=2 lists revisions resulting in v2
        resp_v2 = self.client.get(f"{reverse('core:revisions_list')}?snapshot=2")
        self.assertEqual(resp_v2.status_code, 200)
        self.assertContains(resp_v2, "Changes introduced in Snapshot v2")
        self.assertContains(resp_v2, "58.20%")

        # 3. Filter ?snapshot=3 shows zero revisions for v2->v3
        resp_v3 = self.client.get(f"{reverse('core:revisions_list')}?snapshot=3")
        self.assertEqual(resp_v3.status_code, 200)
        self.assertContains(resp_v3, "Changes introduced in Snapshot v3")
        self.assertContains(resp_v3, "No revisions match the selected filters")

        # 4. Filter ?since=1 shows all revisions after Snapshot v1 (including v1->v2)
        resp_since = self.client.get(f"{reverse('core:revisions_list')}?since=1")
        self.assertEqual(resp_since.status_code, 200)
        self.assertContains(resp_since, "Changes recorded after Snapshot v1")
        self.assertContains(resp_since, "58.20%")

    def test_all_transition_types_rendered_with_badges(self):
        """Verifies CHANGED, NEW_VALUE, and WITHDRAWN transitions render with distinct semantic badges and values."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        # 1. CHANGED (Nigeria 2018)
        Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity (% of population)',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.50000000'),
            new_value=Decimal('58.20000000'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        # 2. NEW_VALUE (Ghana 2021)
        Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='GHA',
            country_name='Ghana',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity (% of population)',
            observation_year=2021,
            transition_type=RevisionTransition.NEW_VALUE,
            old_value=None,
            new_value=Decimal('85.90000000'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        # 3. WITHDRAWN (Kenya 2020)
        Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='KEN',
            country_name='Kenya',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity (% of population)',
            observation_year=2020,
            transition_type=RevisionTransition.WITHDRAWN,
            old_value=Decimal('71.40000000'),
            new_value=None,
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        resp = self.client.get(reverse('core:revisions_list'))
        self.assertEqual(resp.status_code, 200)

        # Badges present
        self.assertContains(resp, 'badge-changed')
        self.assertContains(resp, 'badge-new')
        self.assertContains(resp, 'badge-withdrawn')

        # Values formatted or No data
        self.assertContains(resp, '56.50%')
        self.assertContains(resp, '58.20%')
        self.assertContains(resp, '85.90%')
        self.assertContains(resp, '71.40%')
        self.assertContains(resp, 'No data')

    def test_country_and_transition_filtering(self):
        """Verifies filtering by country and transition type narrows the result set safely."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.5'),
            new_value=Decimal('58.2'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )
        Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='GHA',
            country_name='Ghana',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2021,
            transition_type=RevisionTransition.NEW_VALUE,
            old_value=None,
            new_value=Decimal('85.9'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        # Country filter: NGA
        resp_nga = self.client.get(f"{reverse('core:revisions_list')}?country=NGA")
        self.assertEqual(resp_nga.status_code, 200)
        self.assertEqual(resp_nga.context['ctx'].filtered_revisions_count, 1)
        self.assertEqual(resp_nga.context['ctx'].revisions[0].country_code, 'NGA')
        self.assertContains(resp_nga, "Nigeria")

        # Transition filter: NEW_VALUE
        resp_new = self.client.get(f"{reverse('core:revisions_list')}?transition=NEW_VALUE")
        self.assertEqual(resp_new.status_code, 200)
        self.assertEqual(resp_new.context['ctx'].filtered_revisions_count, 1)
        self.assertEqual(resp_new.context['ctx'].revisions[0].transition_type, RevisionTransition.NEW_VALUE)
        self.assertEqual(resp_new.context['ctx'].revisions[0].country_code, 'GHA')
        self.assertContains(resp_new, "Ghana")

        # Invalid country filter falls back safely
        resp_invalid = self.client.get(f"{reverse('core:revisions_list')}?country=XYZ")
        self.assertEqual(resp_invalid.status_code, 200)
        self.assertContains(resp_invalid, "Nigeria")
        self.assertContains(resp_invalid, "Ghana")

    def test_paired_evidence_read_from_stored_fields_not_hardcoded(self):
        """Revision detail reads paired attribution, definition, unit, and source URLs from stored fields without hard-coding."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity (% of population)',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.50000000'),
            new_value=Decimal('58.20000000'),
            old_unit='custom_percentage_unit',
            new_unit='custom_percentage_unit',
            old_provider_label='Custom National Statistical Agency',
            new_provider_label='Custom National Statistical Agency',
            old_license='Open Government License 3.0',
            new_license='Open Government License 3.0',
            old_underlying_source='Custom National Electrification Audit',
            new_underlying_source='Custom National Electrification Audit',
            old_indicator_definition='Percentage of domestic households with metered electricity.',
            new_indicator_definition='Percentage of domestic households with metered electricity.',
            old_source_url='https://custom-data.org/nga/series/1',
            new_source_url='https://custom-data.org/nga/series/2',
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        resp = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev.pk}))
        self.assertEqual(resp.status_code, 200)

        # Asserts stored fields are rendered exactly
        self.assertContains(resp, 'Custom National Statistical Agency')
        self.assertContains(resp, 'Open Government License 3.0')
        self.assertContains(resp, 'Custom National Electrification Audit')
        self.assertContains(resp, 'Percentage of domestic households with metered electricity')
        self.assertContains(resp, 'https://custom-data.org/nga/series/2')

    def test_delta_calculation_only_for_comparable_units(self):
        """Delta in percentage points (+1.70 pp) is computed only when units are comparable numeric values."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        rev_comparable = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.50000000'),
            new_value=Decimal('58.20000000'),
            old_unit='% of population',
            new_unit='% of population',
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        resp = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev_comparable.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "+1.70 pp")

    def test_different_percentage_denominators_do_not_get_point_delta(self):
        snap1 = self._create_snapshot(1, 'old' * 21 + 'x', is_latest=False)
        snap2 = self._create_snapshot(2, 'new' * 21 + 'x', is_latest=True)
        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.5'),
            new_value=Decimal('58.2'),
            old_unit='% of rural population',
            new_unit='% of population',
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        resp = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev.pk}))
        self.assertContains(resp, 'Values altered (units differed)')
        self.assertNotContains(resp, '+1.70 pp')

    def test_neighbour_years_read_from_respective_snapshots(self):
        """Inspection view displays neighbouring years from respective snapshots, rendering nulls as 'No data'."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        # Neighbour years: 2016 (unchanged), 2017 (null in v1, value in v2), 2018 (revised)
        self._create_snapshot_value(snap1, 'NGA', 2016, Decimal('54.00000000'))
        self._create_snapshot_value(snap2, 'NGA', 2016, Decimal('54.00000000'))

        self._create_snapshot_value(snap1, 'NGA', 2017, None)
        self._create_snapshot_value(snap2, 'NGA', 2017, Decimal('55.50000000'))

        self._create_snapshot_value(snap1, 'NGA', 2018, Decimal('56.50000000'))
        self._create_snapshot_value(snap2, 'NGA', 2018, Decimal('58.20000000'))

        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.50000000'),
            new_value=Decimal('58.20000000'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        resp = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev.pk}))
        self.assertEqual(resp.status_code, 200)

        # Check neighbour years rendered
        self.assertContains(resp, "2016")
        self.assertContains(resp, "54.00%")
        self.assertContains(resp, "2017")
        self.assertContains(resp, "55.50%")
        self.assertContains(resp, "No data")

    def test_simulated_runs_and_persistent_badge(self):
        """When snapshot/revision is from a simulation run, a persistent 'Simulated example' badge is displayed."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        # Mark an import run as simulation
        ImportRun.objects.create(
            status=ImportStatus.COMPLETED,
            snapshot=snap2,
            is_simulation=True,
            records_count=225,
            pages_fetched=9,
        )

        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.5'),
            new_value=Decimal('58.2'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        # List view displays simulated badge
        resp_list = self.client.get(reverse('core:revisions_list'))
        self.assertContains(resp_list, "Simulated example")

        # Detail view displays simulated badge
        resp_detail = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev.pk}))
        self.assertContains(resp_detail, "Simulated example")

    def test_unrelated_and_repeat_simulated_runs_do_not_relabel_live_revision(self):
        snap1 = self._create_snapshot(1, 'old' * 21 + 'x', is_latest=False)
        snap2 = self._create_snapshot(2, 'new' * 21 + 'x', is_latest=True)
        ImportRun.objects.create(status=ImportStatus.COMPLETED, snapshot=snap1, is_simulation=False)
        ImportRun.objects.create(status=ImportStatus.COMPLETED, snapshot=snap2, is_simulation=False)
        ImportRun.objects.create(status=ImportStatus.COMPLETED, snapshot=snap2, is_simulation=True)
        ImportRun.objects.create(status=ImportStatus.FAILED, is_simulation=True)
        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.5'),
            new_value=Decimal('58.2'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        list_resp = self.client.get(reverse('core:revisions_list'))
        detail_resp = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev.pk}))
        self.assertNotContains(list_resp, 'Simulated example')
        self.assertNotContains(detail_resp, 'Simulated example')

    def test_newer_snapshot_banner_on_pinned_briefing(self):
        """
        When a comparison is pinned to Snapshot v1 and a newer Snapshot v2 is published:
        - Briefing displays 'A newer snapshot is available' banner linking to /revisions/?since=1.
        - Pinned observation values and byte-identical export remain strictly tied to Snapshot v1.
        """
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=True)

        # Insert values for Snapshot v1
        for country in ['NGA', 'GHA', 'KEN']:
            for y in range(2000, 2025):
                val = Decimal('56.50000000') if (country == 'NGA' and y == 2018) else Decimal('50.00000000')
                self._create_snapshot_value(snap1, country, y, val)

        # Create public example pinned to Snapshot v1
        comp = SavedComparison.objects.create(
            snapshot=snap1,
            title="Electricity Access in West & East Africa (2000–2024)",
            indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA', 'GHA', 'KEN'],
            start_year=2000,
            end_year=2024,
            is_public_example=True,
        )

        # Before v2 exists: no newer-snapshot banner
        resp_initial = self.client.get(reverse('core:public_example'))
        self.assertEqual(resp_initial.status_code, 200)
        self.assertNotContains(resp_initial, "A newer snapshot is available")

        # Publish Snapshot v2 (is_latest=True)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)
        for country in ['NGA', 'GHA', 'KEN']:
            for y in range(2000, 2025):
                val = Decimal('58.20000000') if (country == 'NGA' and y == 2018) else Decimal('50.00000000')
                self._create_snapshot_value(snap2, country, y, val)

        # After v2 exists: newer-snapshot banner appears
        resp_after_v2 = self.client.get(reverse('core:public_example'))
        self.assertEqual(resp_after_v2.status_code, 200)
        self.assertContains(resp_after_v2, "A newer snapshot is available")
        self.assertContains(resp_after_v2, f"Snapshot v{snap1.version}")
        self.assertContains(resp_after_v2, f"Snapshot v{snap2.version}")
        self.assertContains(resp_after_v2, f"/revisions/?since={snap1.version}")

        # Invariant check: pinned briefing still displays Snapshot v1 value (56.5)
        self.assertContains(resp_after_v2, "56.5")
        self.assertNotContains(resp_after_v2, "58.2")

    def test_about_view_renders_import_run_history_and_safe_failure(self):
        """About view displays live ImportRun records with safe failure summaries without leaking raw stack traces."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000')

        # Completed run
        ImportRun.objects.create(
            status=ImportStatus.COMPLETED,
            snapshot=snap1,
            records_count=225,
            pages_fetched=9,
            started_at=self.now,
            ended_at=self.now,
        )

        # Failed run with error_detail
        ImportRun.objects.create(
            status=ImportStatus.FAILED,
            error_category='HTTP_ERROR',
            error_detail='Traceback: Connection refused on https://internal.secret.api.host/call',
            pages_fetched=2,
            started_at=self.now,
            ended_at=self.now,
        )

        resp = self.client.get(reverse('core:about'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Durable Import Run History")
        self.assertContains(resp, "Completed")
        self.assertContains(resp, "Failed")
        self.assertContains(resp, "HTTP_ERROR")
        self.assertContains(resp, "Database publication transaction rolled back; earlier snapshots untouched")

        # Invariant: raw traceback or internal URL must not leak
        self.assertNotContains(resp, "internal.secret.api.host")
        self.assertNotContains(resp, "Traceback")

    def test_revisions_and_about_views_reject_mutations(self):
        """All public revision and about routes enforce @require_GET; mutations return 405."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)
        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Total',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.5'),
            new_value=Decimal('58.2'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        routes = [
            reverse('core:revisions_list'),
            reverse('core:revision_detail', kwargs={'pk': rev.pk}),
            reverse('core:about'),
        ]

        for url in routes:
            for method in ['post', 'put', 'delete', 'patch']:
                client_fn = getattr(self.client, method)
                resp = client_fn(url, {})
                self.assertEqual(resp.status_code, 405, f"Method {method.upper()} on {url} should return 405")

    def test_standalone_detail_view_and_zero_js_deep_link(self):
        """Standalone revision detail view returns 200 with full inspection; 404 for missing pk."""
        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=False)
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)
        rev = Revision.objects.create(
            previous_snapshot=snap1,
            current_snapshot=snap2,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity (% of population)',
            observation_year=2018,
            transition_type=RevisionTransition.CHANGED,
            old_value=Decimal('56.50000000'),
            new_value=Decimal('58.20000000'),
            previous_retrieved_at=snap1.retrieved_at,
            current_retrieved_at=snap2.retrieved_at,
        )

        # 1. Standalone detail route
        resp = self.client.get(reverse('core:revision_detail', kwargs={'pk': rev.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Back to Revisions Ledger")
        self.assertContains(resp, "What Changed?")
        self.assertContains(resp, "Snapshot v1")
        self.assertContains(resp, "Snapshot v2")

        # 2. Non-existent pk returns 404
        resp_404 = self.client.get(reverse('core:revision_detail', kwargs={'pk': 99999}))
        self.assertEqual(resp_404.status_code, 404)

        # 3. List with ?inspect=pk renders the inline inspection panel
        resp_list_inspect = self.client.get(f"{reverse('core:revisions_list')}?inspect={rev.pk}")
        self.assertEqual(resp_list_inspect.status_code, 200)
        self.assertContains(resp_list_inspect, 'id="inspection-drawer"')
        self.assertContains(resp_list_inspect, "Close inspection")

    def test_export_zip_byte_identical_with_newer_snapshot_active(self):
        """Export ZIP package for pinned comparison remains byte-identical even when a newer snapshot exists."""
        import hashlib
        from core.services.exporter import package_comparison_export

        snap1 = self._create_snapshot(1, 'hash_v1_000000000000000000000000000000000000000000000000000000000000', is_latest=True)
        for country in ['NGA', 'GHA', 'KEN']:
            for y in range(2000, 2025):
                self._create_snapshot_value(snap1, country, y, Decimal('50.00000000'))

        comp = SavedComparison.objects.create(
            snapshot=snap1,
            title="Electricity Access in West & East Africa (2000–2024)",
            indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA', 'GHA', 'KEN'],
            start_year=2000,
            end_year=2024,
            is_public_example=True,
        )

        zip_v1 = package_comparison_export(comp)
        hash_v1 = hashlib.sha256(zip_v1).hexdigest()

        # Publish Snapshot v2
        snap2 = self._create_snapshot(2, 'hash_v2_000000000000000000000000000000000000000000000000000000000000', is_latest=True)
        for country in ['NGA', 'GHA', 'KEN']:
            for y in range(2000, 2025):
                self._create_snapshot_value(snap2, country, y, Decimal('55.00000000'))

        # Re-export pinned comparison
        zip_after_v2 = package_comparison_export(comp)
        hash_after_v2 = hashlib.sha256(zip_after_v2).hexdigest()

        self.assertEqual(hash_v1, hash_after_v2)

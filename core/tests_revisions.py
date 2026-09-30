"""
Automated test suite for Ticket 4: Revision Ledger.

Tests all acceptance criteria and invariants:
- Consecutive snapshot diffing (v[N-1] -> v[N])
- All three transition types (NEW_VALUE, CHANGED, WITHDRAWN)
- Unchanged observations and equivalent decimals produce zero revisions
- Paired old/new provenance and foreign keys to stored SnapshotValues
- Numeric-to-absent withdrawal, absent-to-numeric new value, and null-to-absent (no revision)
- Distinguishing absent observation from failed/missing page
- Atomic rollback on Revision.objects.bulk_create failure
- Zero revisions when only metadata changes between snapshots
- Reversion sequence A -> B -> A generating exact inverse revisions
- CLI inspection helper displaying snapshot IDs, retrieval dates, and attribution
"""
from decimal import Decimal
import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.adapters import WorldBankAdapter, ObservationRecord, AdapterErrorCategory
from core.models import (
    Snapshot,
    SnapshotValue,
    ImportRun,
    ImportStatus,
    PublicationPointer,
    Revision,
    RevisionTransition,
)
from core.services import ImportService, compute_snapshot_diff
from core.provenance import get_indicator_provenance, INDICATOR_PROVENANCE_REGISTRY


COMPLETE_FIXTURE_PATH = 'core/fixtures/provider/simulated_nine_series_complete.json'
REVISED_B_FIXTURE_PATH = 'core/fixtures/provider/simulated_nine_series_revised_b.json'
REVERTED_A_FIXTURE_PATH = 'core/fixtures/provider/simulated_nine_series_reverted_a.json'


class RevisionLedgerTests(TestCase):
    """Integration and invariant tests for the CiteGrid revision ledger."""

    def setUp(self):
        PublicationPointer.objects.get_or_create(name='wdi_publication')

    def test_first_snapshot_produces_zero_revisions(self):
        """The initial publication (Snapshot v1) produces zero Revision records."""
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 1)
        self.assertEqual(Revision.objects.count(), 0)

    def test_identical_refetch_produces_zero_revisions(self):
        """Re-fetching identical data publishes no new snapshot and creates no Revision rows."""
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 1)
        self.assertEqual(Revision.objects.count(), 0)

    def test_equivalent_decimals_produce_zero_revisions(self):
        """Equivalent decimal representations (55.4 vs 55.40) normalize to equality and produce 0 revisions."""
        now = timezone.now()
        s1 = Snapshot.objects.create(version=1, content_hash='h1' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=False)
        s2 = Snapshot.objects.create(version=2, content_hash='h2' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=True)
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')

        sv1 = SnapshotValue.objects.create(
            snapshot=s1, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            observation_year=2020, numeric_value=Decimal('55.4'), raw_value_str='55.4',
            indicator_definition=prov['definition'], source_url=prov['source_url'],
        )
        sv2 = SnapshotValue.objects.create(
            snapshot=s2, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            observation_year=2020, numeric_value=Decimal('55.40'), raw_value_str='55.40',
            indicator_definition=prov['definition'], source_url=prov['source_url'],
        )

        diff = compute_snapshot_diff(s1, s2, [sv1], [sv2])
        self.assertEqual(len(diff), 0)

    def test_all_transition_types_detected(self):
        """
        Publishing Dataset B after Dataset A generates exact CHANGED, NEW_VALUE, and WITHDRAWN transitions.
        - CHANGED: Nigeria Total 2018 (56.5% -> 58.2%)
        - NEW_VALUE: Kenya Rural 2023 (null -> 75.0%)
        - WITHDRAWN: Ghana Urban 2015 (72.5% -> null)
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())

        self.assertEqual(Snapshot.objects.count(), 2)
        s1 = Snapshot.objects.get(version=1)
        s2 = Snapshot.objects.get(version=2)

        revisions = list(Revision.objects.filter(current_snapshot=s2))
        self.assertEqual(len(revisions), 3)

        # 1. CHANGED transition
        rev_changed = Revision.objects.get(
            current_snapshot=s2,
            country_code='NGA',
            indicator_code='EG.ELC.ACCS.ZS',
            observation_year=2018,
        )
        self.assertEqual(rev_changed.transition_type, RevisionTransition.CHANGED)
        self.assertEqual(rev_changed.old_value, Decimal('56.5'))
        self.assertEqual(rev_changed.new_value, Decimal('58.2'))
        self.assertIsNotNone(rev_changed.old_snapshot_value)
        self.assertIsNotNone(rev_changed.new_snapshot_value)
        self.assertEqual(rev_changed.previous_snapshot, s1)
        self.assertEqual(rev_changed.current_snapshot, s2)
        self.assertEqual(rev_changed.previous_retrieved_at, s1.retrieved_at)
        self.assertEqual(rev_changed.current_retrieved_at, s2.retrieved_at)

        # 2. NEW_VALUE transition
        rev_new = Revision.objects.get(
            current_snapshot=s2,
            country_code='KEN',
            indicator_code='EG.ELC.ACCS.RU.ZS',
            observation_year=2023,
        )
        self.assertEqual(rev_new.transition_type, RevisionTransition.NEW_VALUE)
        self.assertIsNone(rev_new.old_value)
        self.assertEqual(rev_new.new_value, Decimal('75.0'))
        self.assertEqual(rev_new.old_snapshot_value.numeric_value, None)
        self.assertEqual(rev_new.new_snapshot_value.numeric_value, Decimal('75.0'))

        # 3. WITHDRAWN transition
        rev_withdrawn = Revision.objects.get(
            current_snapshot=s2,
            country_code='GHA',
            indicator_code='EG.ELC.ACCS.UR.ZS',
            observation_year=2015,
        )
        self.assertEqual(rev_withdrawn.transition_type, RevisionTransition.WITHDRAWN)
        self.assertEqual(rev_withdrawn.old_value, Decimal('72.5'))
        self.assertIsNone(rev_withdrawn.new_value)
        self.assertEqual(rev_withdrawn.old_snapshot_value.numeric_value, Decimal('72.5'))
        self.assertIsNone(rev_withdrawn.new_snapshot_value.numeric_value)

    def test_unchanged_values_produce_no_revisions(self):
        """The 222 unchanged observations in Dataset B produce zero Revision records."""
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())

        # Total revisions across 225 keys is exactly 3
        self.assertEqual(Revision.objects.count(), 3)

        # Confirm an unchanged key (e.g. Kenya Total 2020) has no revision
        self.assertFalse(
            Revision.objects.filter(
                country_code='KEN',
                indicator_code='EG.ELC.ACCS.ZS',
                observation_year=2020,
            ).exists()
        )

    def test_absent_to_numeric_new_value(self):
        """An observation completely absent from previous snapshot that appears with a numeric value in new snapshot produces NEW_VALUE."""
        now = timezone.now()
        s1 = Snapshot.objects.create(version=1, content_hash='h1' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=False)
        s2 = Snapshot.objects.create(version=2, content_hash='h2' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=True)
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')

        # s1 has no record for 2024 (absent from table)
        sv2 = SnapshotValue.objects.create(
            snapshot=s2, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            observation_year=2024, numeric_value=Decimal('62.5'), raw_value_str='62.5',
            indicator_definition=prov['definition'], source_url=prov['source_url'],
        )

        diff = compute_snapshot_diff(s1, s2, [], [sv2])
        self.assertEqual(len(diff), 1)
        rev = diff[0]
        self.assertEqual(rev.transition_type, RevisionTransition.NEW_VALUE)
        self.assertIsNone(rev.old_snapshot_value)
        self.assertIsNone(rev.old_value)
        self.assertEqual(rev.new_value, Decimal('62.5'))

    def test_numeric_to_absent_withdrawal(self):
        """An observation numeric in previous snapshot that is absent from new snapshot's stored values produces WITHDRAWN."""
        now = timezone.now()
        s1 = Snapshot.objects.create(version=1, content_hash='h1' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=False)
        s2 = Snapshot.objects.create(version=2, content_hash='h2' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=True)
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')

        # s1 has numeric observation, s2 has no record (absent from table)
        sv1 = SnapshotValue.objects.create(
            snapshot=s1, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            observation_year=2024, numeric_value=Decimal('62.5'), raw_value_str='62.5',
            indicator_definition=prov['definition'], source_url=prov['source_url'],
        )

        diff = compute_snapshot_diff(s1, s2, [sv1], [])
        self.assertEqual(len(diff), 1)
        rev = diff[0]
        self.assertEqual(rev.transition_type, RevisionTransition.WITHDRAWN)
        self.assertEqual(rev.old_value, Decimal('62.5'))
        self.assertIsNone(rev.new_snapshot_value)
        self.assertIsNone(rev.new_value)

    def test_null_to_absent_produces_no_numeric_revision(self):
        """A structural transition between explicit null and absent observation produces ZERO numeric revisions."""
        now = timezone.now()
        s1 = Snapshot.objects.create(version=1, content_hash='h1' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=False)
        s2 = Snapshot.objects.create(version=2, content_hash='h2' + '0'*62, retrieved_at=now, scope_end_year=2024, is_latest=True)
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')

        # s1 has explicit null value, s2 has absent value
        sv1 = SnapshotValue.objects.create(
            snapshot=s1, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            observation_year=2000, numeric_value=None, raw_value_str=None,
            indicator_definition=prov['definition'], source_url=prov['source_url'],
        )

        diff = compute_snapshot_diff(s1, s2, [sv1], [])
        self.assertEqual(len(diff), 0)

    def test_distinguish_absent_observation_from_failed_page(self):
        """
        An absent observation in a validly paginated complete series response is processed as absent,
        whereas a failed or missing page aborts the entire import with zero revisions.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        # Transport simulating HTTP 500 error on one page of one series
        def broken_transport(url: str, timeout: int):
            if 'GHA' in url:
                return 500, "Internal Server Error", {}
            return 200, json.dumps([{'page': 1, 'pages': 1, 'per_page': 50, 'total': 25, 'sourceid': '2'}, []]), {}

        adapter = WorldBankAdapter(transport=broken_transport)
        res = ImportService.run_import(adapter=adapter, is_simulation=True)

        self.assertFalse(res.success)
        # Snapshot v1 remains untouched, zero revisions generated
        s1.refresh_from_db()
        self.assertTrue(s1.is_latest)
        self.assertEqual(Snapshot.objects.count(), 1)
        self.assertEqual(Revision.objects.count(), 0)

    def test_failed_import_cannot_produce_withdrawals(self):
        """A failed import attempt never leaves spurious withdrawal revisions in the database."""
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())

        def timeout_transport(url: str, timeout: int):
            raise TimeoutError("Network timed out")

        adapter = WorldBankAdapter(transport=timeout_transport)
        res = ImportService.run_import(adapter=adapter, is_simulation=True)

        self.assertFalse(res.success)
        self.assertEqual(Revision.objects.count(), 0)

    def test_revision_bulk_create_failure_rolls_back_snapshot_and_pointer(self):
        """
        If Revision.objects.bulk_create fails, the publication transaction rolls back atomically:
        the new snapshot, its values, revisions, and pointer advance are reverted,
        the previous snapshot stays latest, and ImportRun(status=FAILED) is persisted.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)
        self.assertTrue(s1.is_latest)

        with open(REVISED_B_FIXTURE_PATH, 'r', encoding='utf-8') as f:
            b_data = json.load(f)

        def b_transport(url: str, timeout: int):
            for c in WorldBankAdapter.COUNTRIES:
                if f'/country/{c}/' in url:
                    for ind in WorldBankAdapter.INDICATORS:
                        if f'/indicator/{ind}' in url:
                            return 200, json.dumps(b_data[f"{c}_{ind}"]), {}
            return 404, "Not Found", {}

        adapter_b = WorldBankAdapter(transport=b_transport)

        # Inject failure during Revision bulk_create
        with patch('core.models.Revision.objects.bulk_create', side_effect=RuntimeError("Simulated revision storage failure")):
            res = ImportService.run_import(adapter=adapter_b, is_simulation=True)

        self.assertFalse(res.success)
        self.assertEqual(res.error_category, 'PUBLICATION_ERROR')
        self.assertIn("Simulated revision storage failure", res.error_detail)

        # Assert full rollback
        self.assertEqual(Snapshot.objects.count(), 1)
        s1.refresh_from_db()
        self.assertTrue(s1.is_latest)
        self.assertEqual(SnapshotValue.objects.filter(snapshot__version=2).count(), 0)
        self.assertEqual(Revision.objects.count(), 0)

        # ImportRun is durably persisted
        self.assertEqual(ImportRun.objects.count(), 2)
        failed_run = ImportRun.objects.order_by('-started_at').first()
        self.assertEqual(failed_run.status, ImportStatus.FAILED)
        self.assertIn("Simulated revision storage failure", failed_run.error_detail)

    def test_zero_revisions_when_only_metadata_changes_between_snapshots(self):
        """
        Two complete snapshots whose content hashes differ only because metadata changed
        publish as distinct snapshots but produce exactly ZERO numeric Revision rows.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        with open(COMPLETE_FIXTURE_PATH, 'r', encoding='utf-8') as f:
            a_data = json.load(f)

        def a_transport(url: str, timeout: int):
            for c in WorldBankAdapter.COUNTRIES:
                if f'/country/{c}/' in url:
                    for ind in WorldBankAdapter.INDICATORS:
                        if f'/indicator/{ind}' in url:
                            return 200, json.dumps(a_data[f"{c}_{ind}"]), {}
            return 404, "Not Found", {}

        # Alter metadata definition in provenance registry for one indicator
        alt_registry = dict(INDICATOR_PROVENANCE_REGISTRY)
        alt_registry['EG.ELC.ACCS.ZS'] = dict(alt_registry['EG.ELC.ACCS.ZS'])
        alt_registry['EG.ELC.ACCS.ZS']['definition'] = "Updated official SDG 7.1.1 definition."

        with patch('core.provenance.INDICATOR_PROVENANCE_REGISTRY', alt_registry):
            adapter_metadata_change = WorldBankAdapter(transport=a_transport)
            res = ImportService.run_import(adapter=adapter_metadata_change, is_simulation=True)

        self.assertTrue(res.success)
        self.assertTrue(res.is_new_snapshot)
        self.assertEqual(Snapshot.objects.count(), 2)

        s2 = Snapshot.objects.get(version=2)
        # Content hash changed because metadata changed
        self.assertNotEqual(s1.content_hash, s2.content_hash)

        # Exactly 0 numeric revisions between s1 and s2 because all numeric values were equal
        revisions = Revision.objects.filter(current_snapshot=s2)
        self.assertEqual(revisions.count(), 0)

    def test_reversion_sequence_b_to_a_generates_reverse_revisions(self):
        """
        Sequence A -> B -> A:
        - A -> v1: 0 revisions
        - B -> v2: 3 revisions (1 changed, 1 new, 1 withdrawn)
        - Reverted A -> v3: 3 reverse revisions (changed reverts back, new is withdrawn, withdrawn becomes new)
        """
        # Step 1: Initial publication A
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Revision.objects.count(), 0)

        # Step 2: Revision publication B
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 2)
        self.assertEqual(Revision.objects.filter(current_snapshot__version=2).count(), 3)

        # Step 3: Reversion publication A (Snapshot v3)
        call_command('import_wdi', fixture=REVERTED_A_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 3)
        s3 = Snapshot.objects.get(version=3)

        revisions_v3 = list(Revision.objects.filter(current_snapshot=s3))
        self.assertEqual(len(revisions_v3), 3)

        # NGA Total 2018: changed from 58.2 back to 56.5
        rev_rev_changed = Revision.objects.get(current_snapshot=s3, country_code='NGA', indicator_code='EG.ELC.ACCS.ZS', observation_year=2018)
        self.assertEqual(rev_rev_changed.transition_type, RevisionTransition.CHANGED)
        self.assertEqual(rev_rev_changed.old_value, Decimal('58.2'))
        self.assertEqual(rev_rev_changed.new_value, Decimal('56.5'))

        # KEN Rural 2023: 75.0 withdrawn back to null
        rev_rev_withdrawn = Revision.objects.get(current_snapshot=s3, country_code='KEN', indicator_code='EG.ELC.ACCS.RU.ZS', observation_year=2023)
        self.assertEqual(rev_rev_withdrawn.transition_type, RevisionTransition.WITHDRAWN)
        self.assertEqual(rev_rev_withdrawn.old_value, Decimal('75.0'))
        self.assertIsNone(rev_rev_withdrawn.new_value)

        # GHA Urban 2015: null restored back to 72.5 as a new value
        rev_rev_new = Revision.objects.get(current_snapshot=s3, country_code='GHA', indicator_code='EG.ELC.ACCS.UR.ZS', observation_year=2015)
        self.assertEqual(rev_rev_new.transition_type, RevisionTransition.NEW_VALUE)
        self.assertIsNone(rev_rev_new.old_value)
        self.assertEqual(rev_rev_new.new_value, Decimal('72.5'))

    def test_revision_paired_provenance_and_dates_stored(self):
        """Revision records store complete paired old and new provenance metadata and retrieval timestamps."""
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())

        rev = Revision.objects.get(
            current_snapshot__version=2,
            country_code='NGA',
            indicator_code='EG.ELC.ACCS.ZS',
            observation_year=2018,
        )

        # Provenance attribution
        self.assertEqual(rev.old_provider_label, 'World Bank')
        self.assertEqual(rev.new_provider_label, 'World Bank')
        self.assertEqual(rev.old_license, 'CC BY-4.0')
        self.assertEqual(rev.new_license, 'CC BY-4.0')
        self.assertTrue(rev.old_source_url.startswith('https://data.worldbank.org/indicator/'))
        self.assertTrue(rev.new_source_url.startswith('https://data.worldbank.org/indicator/'))
        self.assertTrue(len(rev.old_indicator_definition) > 20)
        self.assertTrue(len(rev.new_indicator_definition) > 20)
        self.assertEqual(rev.old_unit, '% of population')
        self.assertEqual(rev.new_unit, '% of population')
        self.assertIsNotNone(rev.previous_retrieved_at)
        self.assertIsNotNone(rev.current_retrieved_at)

    def test_inspect_revisions_command(self):
        """The inspect_revisions command outputs formatted representative records with snapshot IDs and retrieval dates."""
        # 1. No data imported yet
        out = io.StringIO()
        call_command('inspect_revisions', stdout=out)
        self.assertIn("No data imported yet", out.getvalue())

        # 2. Initial publication: no earlier snapshot to compare
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        out = io.StringIO()
        call_command('inspect_revisions', stdout=out)
        self.assertIn("Snapshot v1 is the initial publication. No earlier snapshot exists to compare.", out.getvalue())

        # 3. Publish revisions and inspect
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        out = io.StringIO()
        call_command('inspect_revisions', stdout=out)
        output = out.getvalue()

        self.assertIn("=== CiteGrid Revision Ledger (3 revisions) ===", output)
        self.assertIn("[CHANGED] NGA (Nigeria) | EG.ELC.ACCS.ZS | Year: 2018", output)
        self.assertIn("56.50000000% -> 58.20000000%", output)
        self.assertIn("Previous Snapshot:    v1", output)
        self.assertIn("Current Snapshot:     v2", output)
        self.assertIn("observed by CiteGrid on", output)
        self.assertIn("World Bank | CC BY-4.0", output)

        self.assertIn("[NEW_VALUE] KEN (Kenya) | EG.ELC.ACCS.RU.ZS | Year: 2023", output)
        self.assertIn("No data -> 75.00000000%", output)

        self.assertIn("[WITHDRAWN] GHA (Ghana) | EG.ELC.ACCS.UR.ZS | Year: 2015", output)
        self.assertIn("72.50000000% -> No data", output)

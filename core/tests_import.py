"""
Automated test suite for Ticket 3: Complete Import & Immutable Publication.

Tests all acceptance criteria, invariants, and edge cases:
- Strict 9-series completeness
- Deterministic canonical content hashing
- Immutable publication & single-latest constraint
- Sequence & reversion handling (A -> B -> A)
- Exact decimal precision policy (8 decimal places, >8 quantized ROUND_HALF_UP, raw string preserved)
- Provenance integrity stored with values and included in hash
- Durable ImportRun failure logging across transaction rollback
- Designated test/demo database enforcement for fixtures
- Dry-run zero-write guarantee
- Concurrency serialization via PublicationPointer
- Dynamic placeholder view reflecting active snapshot
"""
from decimal import Decimal
import io
import json
import threading
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.adapters import WorldBankAdapter, ObservationRecord, AdapterErrorCategory
from core.models import Snapshot, SnapshotValue, ImportRun, ImportStatus, PublicationPointer
from core.services import ImportService, compute_canonical_content_hash
from core.provenance import get_indicator_provenance, INDICATOR_PROVENANCE_REGISTRY


COMPLETE_FIXTURE_PATH = 'core/fixtures/provider/simulated_nine_series_complete.json'
REVISED_B_FIXTURE_PATH = 'core/fixtures/provider/simulated_nine_series_revised_b.json'
REVERTED_A_FIXTURE_PATH = 'core/fixtures/provider/simulated_nine_series_reverted_a.json'
SINGLE_SERIES_FIXTURE_PATH = 'core/fixtures/provider/captured_wdi_nga_total_2026.json'


class ImportPublicationTests(TestCase):
    """Integration and invariant tests for the CiteGrid import pipeline."""

    def setUp(self):
        # Ensure publication pointer lock exists
        PublicationPointer.objects.get_or_create(name='wdi_publication')

    def test_unexpected_adapter_error_finishes_durable_run(self):
        class FailingAdapter:
            def fetch_scope(self, **kwargs):
                raise RuntimeError('Unexpected provider response')

        result = ImportService.run_import(adapter=FailingAdapter())

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, 'ADAPTER_ERROR')
        self.assertEqual(ImportRun.objects.count(), 1)
        run = ImportRun.objects.get()
        self.assertEqual(run.status, ImportStatus.FAILED)
        self.assertIsNotNone(run.ended_at)
        self.assertEqual(Snapshot.objects.count(), 0)

    def test_unexpected_adapter_error_in_dry_run_makes_no_writes(self):
        class FailingAdapter:
            def fetch_scope(self, **kwargs):
                raise RuntimeError('Unexpected provider response')

        result = ImportService.run_import(adapter=FailingAdapter(), dry_run=True)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, 'ADAPTER_ERROR')
        self.assertEqual(ImportRun.objects.count(), 0)

    def test_complete_import_publishes_once(self):
        """A complete 9-series import publishes Snapshot v1 with 225 observation values."""
        out = io.StringIO()
        call_command(
            'import_wdi',
            fixture=COMPLETE_FIXTURE_PATH,
            allow_simulation=True,
            stdout=out,
        )

        # Assert Snapshot v1 published
        self.assertEqual(Snapshot.objects.count(), 1)
        snapshot = Snapshot.objects.first()
        self.assertEqual(snapshot.version, 1)
        self.assertTrue(snapshot.is_latest)
        self.assertEqual(len(snapshot.content_hash), 64)
        self.assertEqual(snapshot.scope_countries, ['NGA', 'GHA', 'KEN'])
        self.assertEqual(len(snapshot.scope_indicators), 3)

        # 9 series x 25 years (2000-2024) = 225 values
        self.assertEqual(snapshot.values.count(), 225)

        # Verify values carry provenance attributes
        first_val = snapshot.values.filter(country_code='NGA', indicator_code='EG.ELC.ACCS.ZS').first()
        self.assertIsNotNone(first_val)
        self.assertEqual(first_val.unit, '% of population')
        self.assertEqual(first_val.provider_label, 'World Bank')
        self.assertEqual(first_val.license, 'CC BY-4.0')
        self.assertTrue(first_val.source_url.startswith('https://data.worldbank.org/indicator/'))
        self.assertTrue(len(first_val.indicator_definition) > 20)

        # Assert ImportRun completed
        self.assertEqual(ImportRun.objects.count(), 1)
        run = ImportRun.objects.first()
        self.assertEqual(run.status, ImportStatus.COMPLETED)
        self.assertEqual(run.snapshot, snapshot)
        self.assertTrue(run.is_simulation)
        self.assertEqual(run.records_count, 225)
        self.assertIn("Successfully published Snapshot v1", out.getvalue())

    def test_identical_refetch_creates_no_new_snapshot(self):
        """Re-fetching identical data records a new ImportRun pointing to existing Snapshot without incrementing version."""
        # Initial import
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 1)
        initial_snapshot = Snapshot.objects.first()

        # Second import with identical content
        out = io.StringIO()
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=out)

        # Still exactly 1 Snapshot, still version 1 and is_latest=True
        self.assertEqual(Snapshot.objects.count(), 1)
        current_snapshot = Snapshot.objects.first()
        self.assertEqual(current_snapshot.pk, initial_snapshot.pk)
        self.assertEqual(current_snapshot.version, 1)
        self.assertTrue(current_snapshot.is_latest)

        # Total values unchanged at 225
        self.assertEqual(SnapshotValue.objects.count(), 225)

        # 2 ImportRun records, both completed and pointing to Snapshot 1
        self.assertEqual(ImportRun.objects.count(), 2)
        runs = list(ImportRun.objects.order_by('started_at'))
        self.assertEqual(runs[0].snapshot, initial_snapshot)
        self.assertEqual(runs[1].snapshot, initial_snapshot)
        self.assertEqual(runs[1].status, ImportStatus.COMPLETED)
        self.assertIn("Repeat content detected", out.getvalue())

    def test_reversion_sequence_a_b_a_publishes_new_version(self):
        """
        Sequence: A -> repeat A -> B -> A.
        - First A publishes v1 (hash A)
        - Repeat A creates no new version (points to v1)
        - B publishes v2 (hash B)
        - Reverted A publishes v3 (hash A, is_latest=True), preserving diffability B -> A.
        """
        # Step 1: Initial dataset A
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        snap1 = Snapshot.objects.get(version=1)
        self.assertTrue(snap1.is_latest)
        hash_a = snap1.content_hash

        # Step 2: Repeat dataset A
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 1)

        # Step 3: Revised dataset B (revised Nigeria 2018 value)
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 2)
        snap1.refresh_from_db()
        self.assertFalse(snap1.is_latest)
        snap2 = Snapshot.objects.get(version=2)
        self.assertTrue(snap2.is_latest)
        self.assertNotEqual(snap2.content_hash, hash_a)

        # Step 4: Reverted dataset A (reversion to original figures)
        call_command('import_wdi', fixture=REVERTED_A_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 3)
        snap2.refresh_from_db()
        self.assertFalse(snap2.is_latest)

        snap3 = Snapshot.objects.get(version=3)
        self.assertTrue(snap3.is_latest)
        # Snapshot 3 content hash is identical to Snapshot 1
        self.assertEqual(snap3.content_hash, hash_a)
        self.assertNotEqual(snap3.content_hash, snap2.content_hash)

        # Exactly 1 active latest snapshot across the entire database
        self.assertEqual(Snapshot.objects.filter(is_latest=True).count(), 1)
        self.assertEqual(Snapshot.objects.filter(is_latest=True).first(), snap3)

        # 4 durable ImportRuns
        self.assertEqual(ImportRun.objects.count(), 4)

    def test_partial_scope_rejected(self):
        """Importing a partial scope (e.g. 1 series instead of all 9) is strictly rejected with zero snapshots."""
        with self.assertRaises(CommandError) as ctx:
            call_command('import_wdi', fixture=SINGLE_SERIES_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())

        self.assertIn("Import failed", str(ctx.exception))
        # Zero snapshots published
        self.assertEqual(Snapshot.objects.count(), 0)
        self.assertEqual(SnapshotValue.objects.count(), 0)

        # Failed ImportRun is durably recorded
        self.assertEqual(ImportRun.objects.count(), 1)
        run = ImportRun.objects.first()
        self.assertEqual(run.status, ImportStatus.FAILED)

    def test_failed_import_leaves_published_snapshot_untouched(self):
        """A failed import attempt does not alter or corrupt the currently published latest snapshot."""
        # Publish initial Snapshot v1
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        snap1 = Snapshot.objects.get(version=1)
        self.assertTrue(snap1.is_latest)

        # Attempt an import that fails during network traversal
        with open(COMPLETE_FIXTURE_PATH, 'r', encoding='utf-8') as fixture_file:
            complete_pages = json.load(fixture_file)

        def failing_transport(url: str, timeout: int):
            if 'GHA' in url:
                return 500, "Internal Server Error", {}
            country = url.split('/country/')[1].split('/')[0]
            indicator = url.split('/indicator/')[1].split('?')[0]
            return 200, json.dumps(complete_pages[f'{country}_{indicator}']), {}

        failing_adapter = WorldBankAdapter(transport=failing_transport)
        res = ImportService.run_import(adapter=failing_adapter, is_simulation=True)

        self.assertFalse(res.success)
        self.assertEqual(res.error_category, AdapterErrorCategory.HTTP_ERROR.value)

        # Snapshot v1 remains latest and intact
        snap1.refresh_from_db()
        self.assertTrue(snap1.is_latest)
        self.assertEqual(Snapshot.objects.count(), 1)
        self.assertEqual(SnapshotValue.objects.count(), 225)

        # Failed ImportRun recorded durably
        self.assertEqual(ImportRun.objects.count(), 2)
        failed_run = ImportRun.objects.order_by('-started_at').first()
        self.assertEqual(failed_run.status, ImportStatus.FAILED)
        self.assertEqual(failed_run.error_category, 'HTTP_ERROR')

    def test_publication_rollback_preserves_failed_import_run(self):
        """If an exception occurs during snapshot publication, the transaction rolls back but ImportRun(status=FAILED) is persisted."""
        with open(COMPLETE_FIXTURE_PATH, 'r', encoding='utf-8') as f:
            fixture_data = json.load(f)

        def mock_transport(url: str, timeout: int):
            for country in WorldBankAdapter.COUNTRIES:
                if f'/country/{country}/' in url:
                    for ind in WorldBankAdapter.INDICATORS:
                        if f'/indicator/{ind}' in url:
                            return 200, json.dumps(fixture_data[f"{country}_{ind}"]), {}
            return 404, "Not Found", {}

        adapter = WorldBankAdapter(transport=mock_transport)

        # Patch bulk_create to simulate database error during publication
        with patch('core.models.SnapshotValue.objects.bulk_create', side_effect=RuntimeError("Simulated disk full")):
            res = ImportService.run_import(adapter=adapter, is_simulation=True)

        self.assertFalse(res.success)
        self.assertEqual(res.error_category, 'PUBLICATION_ERROR')
        self.assertIn("Simulated disk full", res.error_detail)

        # Zero snapshots and zero values created
        self.assertEqual(Snapshot.objects.count(), 0)
        self.assertEqual(SnapshotValue.objects.count(), 0)

        # ImportRun is durably saved in the database
        self.assertEqual(ImportRun.objects.count(), 1)
        run = ImportRun.objects.first()
        self.assertEqual(run.status, ImportStatus.FAILED)
        self.assertEqual(run.error_category, 'PUBLICATION_ERROR')
        self.assertIn("Simulated disk full", run.error_detail)

    def test_database_single_latest_constraint(self):
        """Database constraint prevents multiple snapshots from having is_latest=True simultaneously."""
        now = timezone.now()
        Snapshot.objects.create(
            version=1,
            content_hash='hash1' + '0' * 59,
            retrieved_at=now,
            scope_end_year=2024,
            is_latest=True,
        )

        with self.assertRaises(IntegrityError):
            Snapshot.objects.create(
                version=2,
                content_hash='hash2' + '0' * 59,
                retrieved_at=now,
                scope_end_year=2024,
                is_latest=True,
            )

    def test_null_preservation_in_storage(self):
        """Explicit missing observation values remain numeric_value=None and are never coerced to zero."""
        now = timezone.now()
        snap = Snapshot.objects.create(
            version=1,
            content_hash='test' + '0' * 60,
            retrieved_at=now,
            scope_end_year=2024,
            is_latest=True,
        )
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')

        # Insert one null observation and one explicit 0.0 observation
        null_val = SnapshotValue.objects.create(
            snapshot=snap,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity',
            observation_year=2000,
            numeric_value=None,
            raw_value_str=None,
            indicator_definition=prov['definition'],
            source_url=prov['source_url'],
        )
        zero_val = SnapshotValue.objects.create(
            snapshot=snap,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity',
            observation_year=2001,
            numeric_value=Decimal('0.00000000'),
            raw_value_str='0.0',
            indicator_definition=prov['definition'],
            source_url=prov['source_url'],
        )

        null_val.refresh_from_db()
        zero_val.refresh_from_db()

        self.assertIsNone(null_val.numeric_value)
        self.assertEqual(zero_val.numeric_value, Decimal('0'))
        self.assertNotEqual(null_val.numeric_value, zero_val.numeric_value)
        self.assertEqual(SnapshotValue.objects.filter(numeric_value__isnull=True).count(), 1)

    def test_numeric_precision_policy(self):
        """
        Precision policy:
        - Equivalent decimal representations canonicalize to identical hash.
        - Precision up to 8 decimal places is stored without loss.
        - Values with >8 decimal places are quantized to 8 decimal places using ROUND_HALF_UP, raw string preserved.
        - Values exceeding 16 total digits are rejected with INVALID_PAYLOAD.
        """
        # Equivalent decimals produce identical normalized hash
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')
        rec1 = ObservationRecord(
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity',
            observation_year=2020,
            normalized_value=Decimal('55.4').normalize(),
            raw_value_str='55.4',
            indicator_definition=prov['definition'],
            source_url=prov['source_url'],
        )
        rec2 = ObservationRecord(
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity',
            observation_year=2020,
            normalized_value=Decimal('55.40').normalize(),
            raw_value_str='55.40',
            indicator_definition=prov['definition'],
            source_url=prov['source_url'],
        )
        hash1 = compute_canonical_content_hash([rec1])
        hash2 = compute_canonical_content_hash([rec2])
        self.assertEqual(hash1, hash2)

        # Value with 8 decimal places
        exact_8 = Decimal('55.12345678')
        snap = Snapshot.objects.create(
            version=1,
            content_hash='precision_test' + '0' * 50,
            retrieved_at=timezone.now(),
            scope_end_year=2024,
            is_latest=True,
        )
        sv = SnapshotValue.objects.create(
            snapshot=snap,
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity',
            observation_year=2020,
            numeric_value=exact_8,
            raw_value_str='55.12345678',
            indicator_definition=prov['definition'],
            source_url=prov['source_url'],
        )
        sv.refresh_from_db()
        self.assertEqual(sv.numeric_value, exact_8)

        # >8 decimal places quantized by adapter
        raw_payload = json.dumps([
            {'page': 1, 'pages': 1, 'per_page': 50, 'total': 1, 'sourceid': '2'},
            [{
                'countryiso3code': 'NGA',
                'country': {'id': 'NGA', 'value': 'Nigeria'},
                'indicator': {'id': 'EG.ELC.ACCS.ZS', 'value': 'Access to electricity'},
                'date': '2020',
                'value': 55.123456789,  # 9 decimal places
            }]
        ])
        adapter = WorldBankAdapter(transport=lambda url, to: (200, raw_payload, {}))
        res = adapter.fetch_series('NGA', 'EG.ELC.ACCS.ZS', 2020, 2020)
        self.assertTrue(res.success)
        rec = res.records[0]
        # Quantized 55.123456789 with ROUND_HALF_UP -> 55.12345679
        self.assertEqual(rec.normalized_value, Decimal('55.12345679'))
        self.assertEqual(rec.raw_value_str, '55.123456789')

        # Total digits > 16 rejected
        raw_overflow = json.dumps([
            {'page': 1, 'pages': 1, 'per_page': 50, 'total': 1, 'sourceid': '2'},
            [{
                'countryiso3code': 'NGA',
                'country': {'id': 'NGA', 'value': 'Nigeria'},
                'indicator': {'id': 'EG.ELC.ACCS.ZS', 'value': 'Access to electricity'},
                'date': '2020',
                'value': 12345678901234567.8,  # >16 digits
            }]
        ])
        adapter_overflow = WorldBankAdapter(transport=lambda url, to: (200, raw_overflow, {}))
        res_overflow = adapter_overflow.fetch_series('NGA', 'EG.ELC.ACCS.ZS', 2020, 2020)
        self.assertFalse(res_overflow.success)
        self.assertEqual(res_overflow.error_category, AdapterErrorCategory.INVALID_PAYLOAD)

    def test_provenance_integrity_and_hashing(self):
        """Authoritative provenance metadata alters canonical content hash if modified."""
        prov = get_indicator_provenance('EG.ELC.ACCS.ZS')
        rec = ObservationRecord(
            country_code='NGA',
            country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS',
            indicator_name='Access to electricity',
            observation_year=2020,
            normalized_value=Decimal('55.4'),
            raw_value_str='55.4',
            indicator_definition=prov['definition'],
            source_url=prov['source_url'],
            provider_label=prov['provider'],
            license=prov['license'],
            underlying_source=prov['underlying_source'],
        )
        hash_orig = compute_canonical_content_hash([rec])

        # Alter definition
        rec_alt_def = ObservationRecord(**{**rec.__dict__, 'indicator_definition': 'Altered definition'})
        hash_alt_def = compute_canonical_content_hash([rec_alt_def])
        self.assertNotEqual(hash_orig, hash_alt_def)

        # Alter source URL
        rec_alt_url = ObservationRecord(**{**rec.__dict__, 'source_url': 'https://different.url'})
        hash_alt_url = compute_canonical_content_hash([rec_alt_url])
        self.assertNotEqual(hash_orig, hash_alt_url)

    def test_fixture_prohibited_in_production_even_with_flag(self):
        """Using --fixture against a production/normal database is rejected even if --allow-simulation and CITEGRID_IS_DEMO_DB=True are passed."""
        prod_db_config = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': 'production.db',
            }
        }
        with override_settings(DATABASES=prod_db_config, IS_DEMO_DB=True):
            with patch('sys.argv', ['manage.py', 'import_wdi']):
                with patch.dict('os.environ', {'CITEGRID_IS_DEMO_DB': 'True'}):
                    with self.assertRaises(CommandError) as ctx:
                        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
                    self.assertIn("not a designated demo/test database", str(ctx.exception))

    def test_fixture_permitted_in_designated_demo_database(self):
        """Importing --fixture is permitted when database is explicitly designated for demo use (contains 'demo' in name)."""
        demo_db_config = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': 'demo_test.db',
            }
        }
        with override_settings(DATABASES=demo_db_config, IS_DEMO_DB=True):
            with patch('sys.argv', ['manage.py', 'import_wdi']):
                with patch.dict('os.environ', {'CITEGRID_IS_DEMO_DB': 'True'}):
                    from core.services.importer import is_designated_test_or_demo_db
                    is_valid, reason = is_designated_test_or_demo_db()
                    self.assertTrue(is_valid)
                    self.assertIn("verified", reason)

    def test_dry_run_creates_zero_database_rows(self):
        """A dry-run validates scope and computes hash with strictly ZERO database writes."""
        out = io.StringIO()
        call_command(
            'import_wdi',
            fixture=COMPLETE_FIXTURE_PATH,
            allow_simulation=True,
            dry_run=True,
            stdout=out,
        )

        # Zero rows added
        self.assertEqual(ImportRun.objects.count(), 0)
        self.assertEqual(Snapshot.objects.count(), 0)
        self.assertEqual(SnapshotValue.objects.count(), 0)

        output = out.getvalue()
        self.assertIn("[DRY RUN] Scope validated successfully across 9 series", output)
        self.assertIn("[DRY RUN] Observations Count: 225", output)
        self.assertIn("[DRY RUN] Canonical Content Hash:", output)
        self.assertIn("[DRY RUN] Database status: 0 records committed", output)

    def test_views_index_reflects_published_snapshot(self):
        """Index view placeholder updates from 'No data imported yet' to active snapshot metadata after publication."""
        # Initial check: no data imported yet
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No data imported yet.")

        # Run complete import
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        snap = Snapshot.objects.first()

        # Check view after publication
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"Snapshot v{snap.version}")
        self.assertContains(resp, snap.content_hash[:8])
        self.assertContains(resp, "225 observation values")


import unittest
from django.db import connection


class ConcurrencyPublicationTests(TransactionTestCase):
    """
    Concurrency and locking tests using TransactionTestCase.
    Verifies that simultaneous import transactions serialize on PublicationPointer.
    """

    def test_publication_pointer_lock_row_seeded_and_lockable(self):
        """The PublicationPointer singleton row exists and is lockable via select_for_update."""
        self.assertTrue(PublicationPointer.objects.filter(name='wdi_publication').exists())
        with transaction.atomic():
            lock = PublicationPointer.objects.select_for_update().get(name='wdi_publication')
            self.assertEqual(lock.name, 'wdi_publication')
            self.assertIsNotNone(lock.locked_at)

    @unittest.skipUnless(
        connection.vendor == 'postgresql',
        "Two-writer row-level lock concurrency requires PostgreSQL. SQLite ignores SELECT FOR UPDATE "
        "and uses database-level locks where concurrent writer threads encounter table lock conflicts."
    )
    def test_two_writer_concurrency_postgresql(self):
        """Two concurrent imports of the same data serialize cleanly on PostgreSQL without race conditions."""
        with open(COMPLETE_FIXTURE_PATH, 'r', encoding='utf-8') as f:
            fixture_data = json.load(f)

        def mock_transport(url: str, timeout: int):
            for country in WorldBankAdapter.COUNTRIES:
                if f'/country/{country}/' in url:
                    for ind in WorldBankAdapter.INDICATORS:
                        if f'/indicator/{ind}' in url:
                            return 200, json.dumps(fixture_data[f"{country}_{ind}"]), {}
            return 404, "Not Found", {}

        results = []
        errors = []

        def worker():
            try:
                # Ensure each thread has its own DB connection
                connections.close_all()
                adapter = WorldBankAdapter(transport=mock_transport)
                res = ImportService.run_import(adapter=adapter, is_simulation=True)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        t2.start()

        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Thread errors encountered: {errors}")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results))

        # Exactly 1 snapshot version published
        self.assertEqual(Snapshot.objects.count(), 1)
        snap = Snapshot.objects.first()
        self.assertEqual(snap.version, 1)
        self.assertTrue(snap.is_latest)

        # Both ImportRuns completed and pointed to the same snapshot
        self.assertEqual(ImportRun.objects.count(), 2)
        for run in ImportRun.objects.all():
            self.assertEqual(run.status, ImportStatus.COMPLETED)
            self.assertEqual(run.snapshot, snap)

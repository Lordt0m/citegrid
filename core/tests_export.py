"""
CiteGrid Offline Automated Tests for Ticket 5: Pinned Comparison and Attributed Export.

Verifies:
1. Pinned comparison stability across subsequent snapshot publications (including revised and metadata-only snapshots).
2. Byte-identical deterministic export generation (RFC 4180 CSV + normalized ZIP attributes).
3. Accurate missing-value representation ("No data") distinct from numeric zero.
4. Stored provenance extraction directly from pinned snapshot records with conflict detection.
5. Snapshot-wide latest available year calculation.
6. Public route access restriction (only is_public_example=True accessible anonymously).
7. Strict mutation rejection (HTTP 405 Method Not Allowed) preserving database state.
8. SavedComparison model validation against pinned snapshot and single public example constraints.
9. Seed public example lifecycle and CLI management command idempotency.
"""
import hashlib
import html
import io
import os
import zipfile
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.utils import timezone

from core.models import SavedComparison, Snapshot, SnapshotValue
from core.services.comparisons import get_comparison_grid, seed_public_example
from core.services.exporter import (
    generate_comparison_csv,
    generate_source_txt,
    package_comparison_export,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures', 'provider')
COMPLETE_FIXTURE_PATH = os.path.join(FIXTURES_DIR, 'simulated_nine_series_complete.json')
REVISED_B_FIXTURE_PATH = os.path.join(FIXTURES_DIR, 'simulated_nine_series_revised_b.json')
REVERTED_A_FIXTURE_PATH = os.path.join(FIXTURES_DIR, 'simulated_nine_series_reverted_a.json')


class PinnedComparisonAndExportTests(TestCase):
    """Offline test suite for Ticket 5 acceptance checks."""

    def setUp(self):
        self.client = Client()

    def test_fixture_snapshot_is_labelled_on_pages_and_in_export(self):
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        example = seed_public_example()

        for path in ('/', '/about/', '/comparisons/example/'):
            with self.subTest(path=path):
                self.assertContains(self.client.get(path), 'Simulated example')

        with zipfile.ZipFile(io.BytesIO(package_comparison_export(example))) as archive:
            source_note = archive.read('source.txt').decode('utf-8')
        self.assertIn('SIMULATED EXAMPLE: Values were loaded from test fixtures', source_note)

    def test_pinned_comparison_stability_after_new_snapshot(self):
        """
        A comparison pinned to Snapshot v1 displays and exports identical data
        even after Snapshot v2 (with revised values) and Snapshot v3 publish.
        """
        # Publish Snapshot v1 (Dataset A)
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        # Create a comparison pinned to Snapshot v1
        comparison = SavedComparison(
            title="Nigeria vs Ghana & Kenya (Total)",
            snapshot=s1,
            indicator_code='EG.ELC.ACCS.ZS',
            countries=['GHA', 'KEN', 'NGA'],
            start_year=2015,
            end_year=2020,
            is_public_example=True,
        )
        comparison.save()

        # Generate initial grid and export package for Snapshot v1
        grid_v1 = get_comparison_grid(comparison)
        # In Dataset A, Nigeria 2018 Total is 56.5
        self.assertEqual(grid_v1.matrix[2018]['NGA'], Decimal('56.5'))
        export_v1_bytes = package_comparison_export(comparison)
        v1_hash = hashlib.sha256(export_v1_bytes).hexdigest()

        # Publish Snapshot v2 (Dataset B) where Nigeria 2018 is changed to 58.2
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 2)

        # Re-query comparison grid for the v1 pinned comparison
        grid_after_v2 = get_comparison_grid(comparison)
        # Must STILL be 56.5, NOT the revised 58.2 from v2
        self.assertEqual(grid_after_v2.matrix[2018]['NGA'], Decimal('56.5'))

        # Export package bytes must be 100% byte-identical
        export_after_v2_bytes = package_comparison_export(comparison)
        self.assertEqual(hashlib.sha256(export_after_v2_bytes).hexdigest(), v1_hash)

        # Publish Snapshot v3 (reverted A)
        call_command('import_wdi', fixture=REVERTED_A_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 3)

        # Re-query after v3
        grid_after_v3 = get_comparison_grid(comparison)
        self.assertEqual(grid_after_v3.matrix[2018]['NGA'], Decimal('56.5'))
        export_after_v3_bytes = package_comparison_export(comparison)
        self.assertEqual(hashlib.sha256(export_after_v3_bytes).hexdigest(), v1_hash)

    def test_metadata_only_snapshot_does_not_affect_pinned_comparison(self):
        """
        A metadata change in a later Snapshot v2 does not alter the briefing
        or export bytes of a comparison pinned to Snapshot v1.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        example = seed_public_example()
        grid_v1 = get_comparison_grid(example)
        v1_def = grid_v1.indicator_definition
        v1_lic = grid_v1.license
        bytes_v1 = package_comparison_export(example)
        v1_hash = hashlib.sha256(bytes_v1).hexdigest()

        # Simulate creation of Snapshot v2 where metadata is updated in the database
        s1.is_latest = False
        s1.save()

        now = timezone.now()
        s2 = Snapshot.objects.create(
            version=2,
            content_hash='h2' + '0' * 62,
            retrieved_at=now,
            scope_start_year=2000,
            scope_end_year=2024,
            is_latest=True,
            source_name='Updated Provider Name',
        )

        # Copy values into s2 with an updated definition and licence
        for sv in s1.values.all():
            SnapshotValue.objects.create(
                snapshot=s2,
                source_id=sv.source_id,
                country_code=sv.country_code,
                country_name=sv.country_name,
                indicator_code=sv.indicator_code,
                indicator_name=sv.indicator_name,
                indicator_definition="Updated Definition in v2",
                unit=sv.unit,
                source_url="https://example.com/v2",
                provider_label="Updated Provider",
                underlying_source="Updated Underlying Source",
                license="CC BY 3.0",
                footnote=sv.footnote,
                observation_year=sv.observation_year,
                numeric_value=sv.numeric_value,
                raw_value_str=sv.raw_value_str,
            )

        # Pinned example on v1 must still read v1's stored metadata
        grid_still_v1 = get_comparison_grid(example)
        self.assertEqual(grid_still_v1.indicator_definition, v1_def)
        self.assertEqual(grid_still_v1.license, v1_lic)
        self.assertEqual(grid_still_v1.source_name, 'World Development Indicators')

        # Export bytes for v1 must remain byte-identical
        bytes_still_v1 = package_comparison_export(example)
        self.assertEqual(hashlib.sha256(bytes_still_v1).hexdigest(), v1_hash)

    def test_conflicting_or_missing_stored_provenance_rejected(self):
        """
        get_comparison_grid detects and rejects missing records or conflicting
        stored provenance for an indicator in the pinned snapshot.
        """
        now = timezone.now()
        s = Snapshot.objects.create(
            version=1, content_hash='h'*64, retrieved_at=now,
            scope_start_year=2000, scope_end_year=2024, is_latest=True
        )

        # 1. Missing indicator records in snapshot
        comp = SavedComparison(
            title="Test", snapshot=s, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'], start_year=2015, end_year=2020,
        )
        # Bypass clean() to test service guard
        super(SavedComparison, comp).save()

        with self.assertRaises(ValueError) as ctx:
            get_comparison_grid(comp)
        self.assertIn("No observation records found for indicator", str(ctx.exception))

        # 2. Conflicting metadata in snapshot for the same indicator
        SnapshotValue.objects.create(
            snapshot=s, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            indicator_definition='Definition A', unit='%', source_url='http://a',
            provider_label='WB', underlying_source='SE4ALL', license='CC BY 4.0',
            observation_year=2018, numeric_value=Decimal('56.5'), raw_value_str='56.5',
        )
        SnapshotValue.objects.create(
            snapshot=s, source_id='2', country_code='GHA', country_name='Ghana',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            indicator_definition='CONFLICTING Definition B', unit='%', source_url='http://a',
            provider_label='WB', underlying_source='SE4ALL', license='CC BY 4.0',
            observation_year=2018, numeric_value=Decimal('72.5'), raw_value_str='72.5',
        )

        with self.assertRaises(ValueError) as ctx:
            get_comparison_grid(comp)
        self.assertIn("Conflicting stored provenance metadata detected", str(ctx.exception))

    def test_export_bytes_identical_across_repeated_runs(self):
        """
        Repeatedly generating the ZIP export for a pinned comparison produces
        identical bytes with matching SHA-256 hash.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        example = seed_public_example()

        pkg1 = package_comparison_export(example)
        pkg2 = package_comparison_export(example)

        self.assertEqual(hashlib.sha256(pkg1).hexdigest(), hashlib.sha256(pkg2).hexdigest())

        # Verify ZIP archive contents
        with zipfile.ZipFile(io.BytesIO(pkg1), 'r') as zf:
            namelist = zf.namelist()
            self.assertEqual(namelist, ['comparison.csv', 'source.txt'])

            csv_info = zf.getinfo('comparison.csv')
            self.assertEqual(csv_info.date_time, (2026, 1, 1, 0, 0, 0))
            self.assertEqual(csv_info.compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual(csv_info.create_system, 0)

            txt_info = zf.getinfo('source.txt')
            self.assertEqual(txt_info.date_time, (2026, 1, 1, 0, 0, 0))
            self.assertEqual(txt_info.compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual(txt_info.create_system, 0)

    def test_missing_value_accurate_text_and_no_data_label(self):
        """
        Rural indicator comparison handles missing observation (Kenya Rural 2023 is null in Dataset A).
        Verifies:
        - Matrix has None
        - CSV has 'No data'
        - source.txt contains the accurate missing-value notice
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        rural_comp = SavedComparison(
            title="Rural Electricity Access Comparison",
            snapshot=s1,
            indicator_code='EG.ELC.ACCS.RU.ZS',
            countries=['GHA', 'KEN', 'NGA'],
            start_year=2020,
            end_year=2024,
            is_public_example=False,
        )
        rural_comp.save()

        grid = get_comparison_grid(rural_comp)
        # Kenya 2023 Rural is null in Dataset A
        self.assertIsNone(grid.matrix[2023]['KEN'])

        # Check CSV rendering
        csv_text = generate_comparison_csv(grid)
        self.assertIn("\r\n", csv_text)
        # In 2023 row, Kenya column should be 'No data'
        self.assertIn("No data", csv_text)

        # Check source.txt
        src_text = generate_source_txt(grid)
        expected_missing_notice = (
            "Missing values are recorded as 'No data'. This indicates that no numeric value\r\n"
            "is available in this pinned snapshot (whether the provider returned an explicit\r\n"
            "null observation or the observation was absent from the dataset). It does not\r\n"
            "represent zero electricity access."
        )
        self.assertIn(expected_missing_notice, src_text)
        self.assertIn("Rural Electricity Access Comparison", src_text)
        self.assertIn(f"v{s1.version}", src_text)
        self.assertIn(s1.content_hash, src_text)
        self.assertIn("World Development Indicators", src_text)
        self.assertIn("World Bank", src_text)
        self.assertIn("SDG 7.1.1 Electrification Dataset", src_text)
        self.assertIn("CC BY-4.0", src_text)

    def test_numeric_zero_distinguished_from_missing(self):
        """An observation with numeric value 0.0 exports as '0', distinct from 'No data'."""
        now = timezone.now()
        s = Snapshot.objects.create(
            version=1, content_hash='h'*64, retrieved_at=now,
            scope_start_year=2000, scope_end_year=2024, is_latest=True
        )

        SnapshotValue.objects.create(
            snapshot=s, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            indicator_definition='Definition', unit='%', source_url='http://url',
            provider_label='WB', underlying_source='SE4ALL', license='CC BY 4.0',
            observation_year=2000, numeric_value=Decimal('0.0'), raw_value_str='0.0',
        )
        SnapshotValue.objects.create(
            snapshot=s, source_id='2', country_code='NGA', country_name='Nigeria',
            indicator_code='EG.ELC.ACCS.ZS', indicator_name='Access to electricity',
            indicator_definition='Definition', unit='%', source_url='http://url',
            provider_label='WB', underlying_source='SE4ALL', license='CC BY 4.0',
            observation_year=2001, numeric_value=None, raw_value_str='',
        )

        comp = SavedComparison(
            title="Zero vs Null Test", snapshot=s, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'], start_year=2000, end_year=2001,
        )
        comp.save()

        grid = get_comparison_grid(comp)
        self.assertEqual(grid.matrix[2000]['NGA'], Decimal('0.0'))
        self.assertIsNone(grid.matrix[2001]['NGA'])

        csv_text = generate_comparison_csv(grid)
        self.assertIn("2000,0\r\n", csv_text)
        self.assertIn("2001,No data\r\n", csv_text)

    def test_latest_available_year_evaluated_across_whole_snapshot(self):
        """
        When a comparison has a narrow year range (e.g. 2005-2010), latest available year
        is computed across the entire pinned snapshot scope (e.g. 2024), not just 2010.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        comp = SavedComparison(
            title="Narrow Range Test",
            snapshot=s1,
            indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA', 'GHA'],
            start_year=2005,
            end_year=2010,
        )
        comp.save()

        grid = get_comparison_grid(comp)
        # In Dataset A, Nigeria total data is present up to 2024
        self.assertEqual(grid.latest_available_years['NGA'], 2024)
        self.assertEqual(grid.latest_available_years['GHA'], 2024)

    def test_public_routes_allow_only_public_example(self):
        """
        Public anonymous users can access /comparisons/example/ and /comparisons/example/export/,
        as well as /comparisons/<pk>/ where is_public_example=True.
        Any request to a non-public comparison (is_public_example=False) returns HTTP 403 Forbidden.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        example = seed_public_example()

        # 1. Anonymous access to example routes -> HTTP 200
        res_ex = self.client.get('/comparisons/example/')
        self.assertEqual(res_ex.status_code, 200)
        self.assertContains(res_ex, html.escape(example.title))

        res_ex_export = self.client.get('/comparisons/example/export/')
        self.assertEqual(res_ex_export.status_code, 200)
        self.assertEqual(res_ex_export['Content-Type'], 'application/zip')

        # 2. Anonymous access to public example via integer ID -> HTTP 200
        res_id = self.client.get(f'/comparisons/{example.id}/')
        self.assertEqual(res_id.status_code, 200)

        res_id_export = self.client.get(f'/comparisons/{example.id}/export/')
        self.assertEqual(res_id_export.status_code, 200)

        # 3. Create a non-public comparison
        private_comp = SavedComparison(
            title="Private Analyst Briefing",
            snapshot=example.snapshot,
            indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'],
            start_year=2010,
            end_year=2020,
            is_public_example=False,
        )
        private_comp.save()

        # Anonymous access to non-public comparison -> HTTP 403 Forbidden
        res_priv = self.client.get(f'/comparisons/{private_comp.id}/')
        self.assertEqual(res_priv.status_code, 403)

        res_priv_export = self.client.get(f'/comparisons/{private_comp.id}/export/')
        self.assertEqual(res_priv_export.status_code, 403)

    def test_public_example_empty_state_when_no_data(self):
        """
        When no snapshot or public example exists yet:
        - GET /comparisons/example/ renders HTTP 200 empty state notice
        - GET /comparisons/example/export/ returns HTTP 404
        """
        self.assertEqual(Snapshot.objects.count(), 0)

        res = self.client.get('/comparisons/example/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Data is not ready yet")

        res_exp = self.client.get('/comparisons/example/export/')
        self.assertEqual(res_exp.status_code, 404)

    def test_mutation_attempts_return_405_method_not_allowed(self):
        """
        Non-GET HTTP methods (POST, PUT, PATCH, DELETE) to comparison and export routes
        return HTTP 405 Method Not Allowed and leave the database completely unaltered.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        example = seed_public_example()
        original_title = example.title
        original_export_hash = hashlib.sha256(package_comparison_export(example)).hexdigest()

        urls = [
            '/comparisons/example/',
            '/comparisons/example/export/',
            f'/comparisons/{example.id}/',
            f'/comparisons/{example.id}/export/',
        ]

        for url in urls:
            for method in ['post', 'put', 'patch', 'delete']:
                client_fn = getattr(self.client, method)
                response = client_fn(url, {'title': 'Mutated Title'})
                self.assertEqual(
                    response.status_code, 405,
                    f"Expected 405 for {method.upper()} {url}, got {response.status_code}"
                )

        # Verify database row remains completely unmodified
        example.refresh_from_db()
        self.assertEqual(example.title, original_title)

        # Verify export package remains byte-identical
        new_export_hash = hashlib.sha256(package_comparison_export(example)).hexdigest()
        self.assertEqual(new_export_hash, original_export_hash)

    def test_saved_comparison_model_validation_on_save(self):
        """
        SavedComparison validates fields against the pinned snapshot on save().
        Also tests that duplicate countries are automatically deduplicated and canonically sorted.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        # 1. Empty title
        comp = SavedComparison(
            title="", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'], start_year=2010, end_year=2020,
        )
        with self.assertRaises(ValidationError) as ctx:
            comp.save()
        self.assertIn('title', ctx.exception.message_dict)

        # 2. Invalid indicator
        comp = SavedComparison(
            title="Valid Title", snapshot=s1, indicator_code='INVALID.INDICATOR',
            countries=['NGA'], start_year=2010, end_year=2020,
        )
        with self.assertRaises(ValidationError) as ctx:
            comp.save()
        self.assertIn('indicator_code', ctx.exception.message_dict)

        # 3. Invalid country
        comp = SavedComparison(
            title="Valid Title", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['USA'], start_year=2010, end_year=2020,
        )
        with self.assertRaises(ValidationError) as ctx:
            comp.save()
        self.assertIn('countries', ctx.exception.message_dict)

        # 4. Inverted year range
        comp = SavedComparison(
            title="Valid Title", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'], start_year=2020, end_year=2010,
        )
        with self.assertRaises(ValidationError) as ctx:
            comp.save()
        self.assertIn('start_year', ctx.exception.message_dict)

        # 5. Year outside snapshot scope
        comp = SavedComparison(
            title="Valid Title", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'], start_year=1990, end_year=2020,
        )
        with self.assertRaises(ValidationError) as ctx:
            comp.save()
        self.assertIn('start_year', ctx.exception.message_dict)

        # 6. Deduplication and canonical sorting
        comp = SavedComparison(
            title="Deduplication Test", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA', 'GHA', 'NGA'], start_year=2010, end_year=2020,
        )
        comp.save()
        self.assertEqual(comp.countries, ['GHA', 'NGA'])

    def test_single_public_example_constraint(self):
        """
        Enforces that at most one comparison can have is_public_example=True.
        - Model layer: save() raises ValidationError.
        - Database layer: bulk_create bypassing clean() raises IntegrityError.
        """
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        s1 = Snapshot.objects.get(version=1)

        comp1 = SavedComparison(
            title="Public Example 1", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['NGA'], start_year=2010, end_year=2020, is_public_example=True,
        )
        comp1.save()

        # Model layer attempt
        comp2 = SavedComparison(
            title="Public Example 2", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['GHA'], start_year=2010, end_year=2020, is_public_example=True,
        )
        with self.assertRaises(ValidationError) as ctx:
            comp2.save()
        self.assertIn('is_public_example', ctx.exception.message_dict)

        # Direct database layer attempt bypassing full_clean()
        comp3 = SavedComparison(
            title="Public Example 3", snapshot=s1, indicator_code='EG.ELC.ACCS.ZS',
            countries=['KEN'], start_year=2010, end_year=2020, is_public_example=True,
        )
        with self.assertRaises(IntegrityError):
            SavedComparison.objects.bulk_create([comp3])

    def test_seed_public_example_lifecycle(self):
        """
        Tests seed_public_example():
        1. Fails when no snapshots exist.
        2. When v1 and v2 exist, selects the earliest published snapshot (v1).
        3. Subsequent calls leave the example permanently pinned to Snapshot v1 (no repinning).
        """
        # 1. No snapshots
        with self.assertRaises(ValueError) as ctx:
            seed_public_example()
        self.assertIn("no published snapshot exists", str(ctx.exception))

        # 2. Publish Snapshot v1 and v2
        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        call_command('import_wdi', fixture=REVISED_B_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 2)

        # First invocation must choose earliest snapshot by version (v1)
        example = seed_public_example()
        self.assertEqual(example.snapshot.version, 1)
        self.assertTrue(example.is_public_example)
        self.assertEqual(
            example.title,
            f"Nigeria vs Ghana & Kenya — Electricity Access ({example.snapshot.scope_start_year}–{example.snapshot.scope_end_year})"
        )

        # 3. Subsequent invocation after Snapshot v3 publishes
        call_command('import_wdi', fixture=REVERTED_A_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())
        self.assertEqual(Snapshot.objects.count(), 3)

        example_again = seed_public_example()
        # Must STILL be pinned to Snapshot v1
        self.assertEqual(example_again.id, example.id)
        self.assertEqual(example_again.snapshot.version, 1)

    def test_seed_public_example_management_command(self):
        """Tests the seed_public_example CLI management command."""
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command('seed_public_example', stdout=out)

        call_command('import_wdi', fixture=COMPLETE_FIXTURE_PATH, allow_simulation=True, stdout=io.StringIO())

        out = io.StringIO()
        call_command('seed_public_example', stdout=out)
        self.assertIn("Successfully seeded public example", out.getvalue())

        out2 = io.StringIO()
        call_command('seed_public_example', stdout=out2)
        self.assertIn("Public example already exists", out2.getvalue())

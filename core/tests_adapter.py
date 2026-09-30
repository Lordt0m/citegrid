"""
Offline unit and integration tests for WorldBankAdapter and fixture corpus.
Runs 100% offline without opening network sockets.
"""
from decimal import Decimal
import json
from pathlib import Path
from django.test import TestCase

from core.adapters import (
    WorldBankAdapter,
    AdapterErrorCategory,
    ObservationRecord,
)

FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures' / 'provider'


class WorldBankAdapterUnitTests(TestCase):
    """Unit tests for URL construction, JSON decimal parsing, and validation."""

    def setUp(self):
        self.adapter = WorldBankAdapter()

    def test_build_request_url_query_shape(self):
        """Constructs HTTPS query with exact required parameters across single series."""
        url = self.adapter.build_request_url(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
            page=1,
            per_page=50,
        )
        self.assertTrue(url.startswith('https://api.worldbank.org/v2/country/NGA/indicator/EG.ELC.ACCS.ZS'))
        self.assertIn('source=2', url)
        self.assertIn('date=2000:2024', url)
        self.assertIn('format=json', url)
        self.assertIn('page=1', url)
        self.assertIn('per_page=50', url)
        self.assertIn('footnote=y', url)

    def test_unquoted_json_numbers_parse_directly_to_decimal(self):
        """Unquoted JSON numbers (e.g. 55.4) parse directly to Decimal without float conversion."""
        raw_json = '{"value": 55.4}'
        parsed = WorldBankAdapter.parse_json_payload(raw_json)
        self.assertIsInstance(parsed['value'], Decimal)
        self.assertEqual(parsed['value'], Decimal('55.4'))

    def test_zero_is_distinguished_from_null(self):
        """Numeric zero (0 or 0.0) is parsed as Decimal('0') and strictly distinct from null (None)."""
        raw_zero_int = '{"value": 0}'
        raw_zero_float = '{"value": 0.0}'
        raw_null = '{"value": null}'

        parsed_zero_int = WorldBankAdapter.parse_json_payload(raw_zero_int)
        parsed_zero_float = WorldBankAdapter.parse_json_payload(raw_zero_float)
        parsed_null = WorldBankAdapter.parse_json_payload(raw_null)

        # Zero integer is parsed as int 0, and adapter will normalize to Decimal('0')
        self.assertEqual(parsed_zero_int['value'], 0)
        self.assertNotEqual(parsed_zero_int['value'], None)

        # Zero float is parsed as Decimal('0.0')
        self.assertIsInstance(parsed_zero_float['value'], Decimal)
        self.assertEqual(parsed_zero_float['value'].normalize(), Decimal('0'))

        # Null is strictly None
        self.assertIsNone(parsed_null['value'])


class WorldBankAdapterFixtureTests(TestCase):
    """Tests executing adapter validation against the offline fixture corpus."""

    @staticmethod
    def _read_fixture(filename: str) -> str:
        return (FIXTURES_DIR / filename).read_text(encoding='utf-8')

    def test_captured_wdi_fixture_parsing(self):
        """Parses authentic World Bank API response captured verbatim from live endpoint."""
        fixture_text = self._read_fixture('captured_wdi_nga_total_2026.json')

        def mock_transport(url: str, timeout: int):
            return 200, fixture_text, {'Content-Type': 'application/json'}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
            per_page=50,
        )

        self.assertTrue(result.success, msg=result.error_detail)
        self.assertEqual(len(result.pages), 1)
        self.assertEqual(result.pages[0].page, 1)
        self.assertEqual(result.pages[0].total, 25)
        self.assertEqual(result.pages[0].source_id, '2')
        self.assertEqual(result.pages[0].last_updated, '2026-07-13')

        # 25 observations from 2000 to 2024
        self.assertEqual(len(result.records), 25)
        first_rec = result.records[0]
        self.assertEqual(first_rec.country_code, 'NGA')
        self.assertEqual(first_rec.country_name, 'Nigeria')
        self.assertEqual(first_rec.indicator_code, 'EG.ELC.ACCS.ZS')
        self.assertEqual(first_rec.observation_year, 2024)
        self.assertEqual(first_rec.normalized_value, Decimal('62.5'))
        self.assertIsInstance(first_rec.normalized_value, Decimal)

    def test_series_pagination_traversal(self):
        """Traverses multi-page series completely (page 1 of 2, page 2 of 2)."""
        p1_text = self._read_fixture('simulated_series_multipage_p1.json')
        p2_text = self._read_fixture('simulated_series_multipage_p2.json')

        def mock_transport(url: str, timeout: int):
            if '&page=1&' in url:
                return 200, p1_text, {}
            elif '&page=2&' in url:
                return 200, p2_text, {}
            return 404, '', {}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
            per_page=13,
        )

        self.assertTrue(result.success, msg=result.error_detail)
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.pages[0].page, 1)
        self.assertEqual(result.pages[1].page, 2)
        self.assertEqual(len(result.records), 25)

    def test_null_preservation(self):
        """Verifies that explicit null values are preserved as None, while zero is preserved as Decimal(0)."""
        fixture_text = self._read_fixture('simulated_series_with_nulls.json')

        def mock_transport(url: str, timeout: int):
            return 200, fixture_text, {}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
        )

        self.assertTrue(result.success)
        rec_2000 = next(r for r in result.records if r.observation_year == 2000)
        rec_2001 = next(r for r in result.records if r.observation_year == 2001)
        rec_2002 = next(r for r in result.records if r.observation_year == 2002)

        # 2000 and 2001 are explicit nulls
        self.assertIsNone(rec_2000.normalized_value)
        self.assertIsNone(rec_2001.normalized_value)

        # 2002 is numeric zero, which must NOT be None
        self.assertIsNotNone(rec_2002.normalized_value)
        self.assertEqual(rec_2002.normalized_value, Decimal('0'))

    def test_year_outside_requested_range_is_rejected(self):
        """Rejects observations with observation year outside requested start_year:end_year."""
        fixture_text = self._read_fixture('captured_wdi_nga_total_2026.json')

        def mock_transport(url: str, timeout: int):
            return 200, fixture_text, {}

        adapter = WorldBankAdapter(transport=mock_transport)
        # Request only 2010 to 2020, but fixture contains 2000-2024
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2010,
            end_year=2020,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.SCOPE_MISMATCH)
        self.assertIn("outside requested range", result.error_detail)
        self.assertEqual(len(result.records), 0)

    def test_inconsistent_page_numbers_rejected(self):
        """Rejects responses with inconsistent pagination numbers."""
        fixture_text = self._read_fixture('simulated_series_inconsistent_pagination.json')

        def mock_transport(url: str, timeout: int):
            return 200, fixture_text, {}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.INCONSISTENT_PAGINATION)
        self.assertEqual(len(result.records), 0)

    def test_duplicate_conflict_rejected(self):
        """Rejects responses with conflicting duplicate keys for the same series and year."""
        fixture_text = self._read_fixture('simulated_series_conflict.json')

        def mock_transport(url: str, timeout: int):
            return 200, fixture_text, {}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.DUPLICATE_CONFLICT)
        self.assertIn("Duplicate conflict", result.error_detail)
        self.assertEqual(len(result.records), 0)

    def test_identical_duplicate_is_rejected_before_publication(self):
        payload = json.loads(self._read_fixture('simulated_series_with_nulls.json'))
        payload[1].append(dict(payload[1][0]))
        payload[0]['total'] += 1
        adapter = WorldBankAdapter(transport=lambda url, timeout: (200, json.dumps(payload), {}))

        result = adapter.fetch_series('NGA', 'EG.ELC.ACCS.ZS', 2000, 2024)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.DUPLICATE_CONFLICT)
        self.assertEqual(result.records, [])

    def test_wrong_source_id_is_rejected(self):
        payload = json.loads(self._read_fixture('simulated_series_with_nulls.json'))
        payload[0]['sourceid'] = '3'
        adapter = WorldBankAdapter(transport=lambda url, timeout: (200, json.dumps(payload), {}))

        result = adapter.fetch_series('NGA', 'EG.ELC.ACCS.ZS', 2000, 2024)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.SCOPE_MISMATCH)
        self.assertEqual(result.records, [])

    def test_reported_total_must_match_received_records(self):
        payload = json.loads(self._read_fixture('simulated_series_with_nulls.json'))
        payload[0]['total'] += 1
        adapter = WorldBankAdapter(transport=lambda url, timeout: (200, json.dumps(payload), {}))

        result = adapter.fetch_series('NGA', 'EG.ELC.ACCS.ZS', 2000, 2024)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.INCONSISTENT_PAGINATION)
        self.assertEqual(result.records, [])

    def test_reported_total_cannot_change_between_pages(self):
        first = json.loads(self._read_fixture('simulated_series_multipage_p1.json'))
        second = json.loads(self._read_fixture('simulated_series_multipage_p2.json'))
        second[0]['total'] = first[0]['total'] + 1

        def mock_transport(url: str, timeout: int):
            return 200, json.dumps(first if '&page=1&' in url else second), {}

        result = WorldBankAdapter(transport=mock_transport).fetch_series(
            'NGA', 'EG.ELC.ACCS.ZS', 2000, 2024, per_page=13,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.INCONSISTENT_PAGINATION)
        self.assertEqual(result.records, [])

    def test_non_finite_and_out_of_range_decimal_rejected(self):
        payload = json.loads(self._read_fixture('simulated_series_with_nulls.json'))
        for value in ('NaN', 'Infinity', '100000000.01'):
            with self.subTest(value=value):
                payload[1][0]['value'] = value
                adapter = WorldBankAdapter(
                    transport=lambda url, timeout: (200, json.dumps(payload), {}),
                )
                result = adapter.fetch_series('NGA', 'EG.ELC.ACCS.ZS', 2000, 2024)
                self.assertFalse(result.success)
                self.assertEqual(result.error_category, AdapterErrorCategory.INVALID_PAYLOAD)
                self.assertEqual(result.records, [])

    def test_non_standard_json_nan_is_rejected(self):
        with self.assertRaises(ValueError):
            WorldBankAdapter.parse_json_payload('{"value": NaN}')

    def test_transport_http_error_handling(self):
        """Models HTTP 500 error in transport, asserting structured HTTP_ERROR."""
        def mock_transport(url: str, timeout: int):
            return 500, "Internal Server Error", {}

        adapter = WorldBankAdapter(transport=mock_transport, max_retries=1)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.HTTP_ERROR)
        self.assertIn("HTTP 500", result.error_detail)
        self.assertEqual(len(result.records), 0)

    def test_transport_timeout_handling(self):
        """Models network timeout exception, asserting structured TIMEOUT after retries."""
        def mock_transport(url: str, timeout: int):
            raise TimeoutError("Connection timed out")

        adapter = WorldBankAdapter(transport=mock_transport, max_retries=1)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.TIMEOUT)
        self.assertIn("timed out", result.error_detail.lower())
        self.assertEqual(len(result.records), 0)

    def test_late_failure_in_nine_series_returns_zero_observations(self):
        """
        Simulates 8 successful series followed by a failure on the 9th series.
        Proves that fetch_scope returns success=False and exactly 0 usable observations.
        """
        captured_text = self._read_fixture('captured_wdi_nga_total_2026.json')
        call_count = 0

        def mock_transport(url: str, timeout: int):
            nonlocal call_count
            call_count += 1
            # Country KEN and Urban indicator (last series in 3x3 grid) fails
            if 'country/KEN' in url and 'indicator/EG.ELC.ACCS.UR.ZS' in url:
                return 503, "Service Unavailable", {}

            # Construct mock valid payload adapting country and indicator in URL
            country = 'NGA'
            if 'country/GHA' in url:
                country = 'GHA'
            elif 'country/KEN' in url:
                country = 'KEN'

            indicator = 'EG.ELC.ACCS.ZS'
            if 'EG.ELC.ACCS.RU.ZS' in url:
                indicator = 'EG.ELC.ACCS.RU.ZS'
            elif 'EG.ELC.ACCS.UR.ZS' in url:
                indicator = 'EG.ELC.ACCS.UR.ZS'

            # Load captured text and patch country and indicator IDs for mock series
            data = json.loads(captured_text)
            for rec in data[1]:
                rec['countryiso3code'] = country
                rec['country']['id'] = country[:2]
                rec['country']['value'] = country
                rec['indicator']['id'] = indicator
            return 200, json.dumps(data), {}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_scope(start_year=2000, end_year=2024)

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, AdapterErrorCategory.HTTP_ERROR)
        self.assertIn("Series failure for (KEN, EG.ELC.ACCS.UR.ZS)", result.error_detail)
        # Atomic guarantee: 0 records published / returned
        self.assertEqual(len(result.records), 0)
        self.assertEqual(len(result.pages), 0)

    def test_simulated_revision_fixture_structure(self):
        """Validates simulated revision fixture parses cleanly without altering provider schema."""
        fixture_text = self._read_fixture('simulated_series_revision_v2.json')

        def mock_transport(url: str, timeout: int):
            return 200, fixture_text, {}

        adapter = WorldBankAdapter(transport=mock_transport)
        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2000,
            end_year=2024,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(result.records), 25)

        # Check the simulated transitions:
        rec_2018 = next(r for r in result.records if r.observation_year == 2018)
        self.assertEqual(rec_2018.normalized_value, Decimal('58.2'))

        rec_2001 = next(r for r in result.records if r.observation_year == 2001)
        self.assertEqual(rec_2001.normalized_value, Decimal('28.0'))

        rec_2002 = next(r for r in result.records if r.observation_year == 2002)
        self.assertIsNone(rec_2002.normalized_value)

"""
CiteGrid World Bank Provider Adapter.

Implements bounded HTTPS requests, unified single-series pagination,
lossless Decimal JSON parsing, robust validation, and atomic failure handling.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import decimal
from enum import Enum
import json
import urllib.request
import urllib.error
import datetime
from typing import Callable, Optional, Any


class AdapterErrorCategory(str, Enum):
    HTTP_ERROR = 'HTTP_ERROR'
    TIMEOUT = 'TIMEOUT'
    INVALID_PAYLOAD = 'INVALID_PAYLOAD'
    INCONSISTENT_PAGINATION = 'INCONSISTENT_PAGINATION'
    SCOPE_MISMATCH = 'SCOPE_MISMATCH'
    DUPLICATE_CONFLICT = 'DUPLICATE_CONFLICT'


@dataclass(frozen=True)
class SeriesScope:
    country_code: str
    indicator_code: str


@dataclass(frozen=True)
class ObservationRecord:
    country_code: str
    country_name: str
    indicator_code: str
    indicator_name: str
    observation_year: int
    normalized_value: Optional[Decimal]
    raw_value_str: Optional[str]
    footnote: str = ''
    unit: str = ''
    obs_status: str = ''
    indicator_definition: str = ''
    provider_label: str = 'World Bank'
    source_url: str = ''
    license: str = 'CC BY-4.0'
    underlying_source: str = 'SDG 7.1.1 Electrification Dataset'


@dataclass(frozen=True)
class PageMetadata:
    request_url: str
    page: int
    pages: int
    per_page: int
    total: int
    source_id: str
    last_updated: str
    fetched_at: str


@dataclass
class AdapterResult:
    success: bool
    records: list[ObservationRecord] = field(default_factory=list)
    pages: list[PageMetadata] = field(default_factory=list)
    error_category: Optional[AdapterErrorCategory] = None
    error_detail: Optional[str] = None
    failed_url: Optional[str] = None


class WorldBankAdapter:
    """
    Adapter boundary for World Bank WDI queries.
    Enforces unified single-series requests (1 country, 1 indicator) across 9 series.
    """

    COUNTRIES = ('NGA', 'GHA', 'KEN')
    INDICATORS = (
        'EG.ELC.ACCS.ZS',      # Total electricity access (% of population)
        'EG.ELC.ACCS.RU.ZS',   # Rural access (% of rural population)
        'EG.ELC.ACCS.UR.ZS',   # Urban access (% of urban population)
    )
    SOURCE_ID = '2'
    DEFAULT_START_YEAR = 2000
    DEFAULT_PER_PAGE = 50
    DEFAULT_TIMEOUT = 15
    DEFAULT_MAX_RETRIES = 2
    USER_AGENT = 'CiteGrid/0.1.0 (+https://github.com/Lordt0m/citegrid)'

    def __init__(
        self,
        transport: Optional[Callable[[str, int], tuple[int, str, dict]]] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        """
        Initialize the adapter.
        :param transport: Optional custom callable (url, timeout) -> (status_code, body_text, headers).
                          Allows 100% offline testing with mock responses or simulated transport errors.
        :param timeout: Network timeout in seconds.
        :param max_retries: Maximum attempts for transient network errors.
        """
        self.transport = transport or self._default_transport
        self.timeout = timeout
        self.max_retries = max_retries

    @staticmethod
    def _default_transport(url: str, timeout: int) -> tuple[int, str, dict]:
        """Default HTTPS transport using standard library urllib."""
        req = urllib.request.Request(
            url,
            headers={'User-Agent': WorldBankAdapter.USER_AGENT, 'Accept': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.status
                body = response.read().decode('utf-8')
                headers = dict(response.getheaders())
                return status, body, headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8') if exc.fp else ''
            return exc.code, body, dict(exc.headers or {})
        except urllib.error.URLError as exc:
            raise exc

    def build_request_url(
        self,
        country: str,
        indicator: str,
        start_year: int,
        end_year: int,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> str:
        """
        Constructs the canonical World Bank HTTPS query for a single series.
        One country and one indicator per request.
        """
        return (
            f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
            f"?source={self.SOURCE_ID}"
            f"&date={start_year}:{end_year}"
            f"&format=json"
            f"&page={page}"
            f"&per_page={per_page}"
            f"&footnote=y"
        )

    @staticmethod
    def parse_json_payload(raw_text: str) -> Any:
        """
        Lossless JSON parsing: parses unquoted floating-point numbers directly
        into Decimal instances without passing through IEEE 754 float.
        """
        def reject_non_finite(value: str):
            raise ValueError(f"Non-finite JSON number: {value}")

        return json.loads(raw_text, parse_float=Decimal, parse_constant=reject_non_finite)

    def _execute_request_with_retry(self, url: str) -> tuple[Optional[int], Optional[str], Optional[dict], Optional[AdapterErrorCategory], Optional[str]]:
        """Executes a request with finite retries for transient transport failures."""
        attempts = 0
        last_error = None
        while attempts <= self.max_retries:
            attempts += 1
            try:
                status_code, body, headers = self.transport(url, self.timeout)
                if status_code != 200:
                    return None, None, None, AdapterErrorCategory.HTTP_ERROR, f"HTTP {status_code} returned for URL {url}"
                return status_code, body, headers, None, None
            except (TimeoutError, urllib.error.URLError) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.URLError) and 'timed out' in str(exc).lower():
                    cat = AdapterErrorCategory.TIMEOUT
                elif isinstance(exc, TimeoutError):
                    cat = AdapterErrorCategory.TIMEOUT
                else:
                    cat = AdapterErrorCategory.HTTP_ERROR
                if attempts > self.max_retries:
                    return None, None, None, cat, f"Request failed after {attempts} attempts: {last_error}"

        return None, None, None, AdapterErrorCategory.HTTP_ERROR, f"Request failed: {last_error}"

    def fetch_series(
        self,
        country: str,
        indicator: str,
        start_year: int,
        end_year: int,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> AdapterResult:
        """
        Fetches and validates all pages for one single series (one country, one indicator).
        Traverses pages until page == pages. Returns structured failure on any error.
        """
        if country not in self.COUNTRIES:
            return AdapterResult(
                success=False,
                error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                error_detail=f"Country '{country}' outside fixed scope {self.COUNTRIES}",
            )
        if indicator not in self.INDICATORS:
            return AdapterResult(
                success=False,
                error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                error_detail=f"Indicator '{indicator}' outside fixed scope {self.INDICATORS}",
            )
        if start_year > end_year:
            return AdapterResult(
                success=False,
                error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                error_detail=f"start_year ({start_year}) cannot exceed end_year ({end_year})",
            )

        all_records: list[ObservationRecord] = []
        all_pages: list[PageMetadata] = []
        seen_years: dict[int, Optional[Decimal]] = {}

        current_page = 1
        total_pages = 1
        expected_total = None
        expected_per_page = None

        while current_page <= total_pages:
            url = self.build_request_url(
                country=country,
                indicator=indicator,
                start_year=start_year,
                end_year=end_year,
                page=current_page,
                per_page=per_page,
            )

            status_code, body, headers, err_cat, err_detail = self._execute_request_with_retry(url)
            if err_cat or body is None:
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=err_cat,
                    error_detail=err_detail,
                    failed_url=url,
                )

            # Parse JSON directly to Decimal
            try:
                payload = self.parse_json_payload(body)
            except Exception as exc:
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=AdapterErrorCategory.INVALID_PAYLOAD,
                    error_detail=f"Malformed JSON in response from {url}: {exc}",
                    failed_url=url,
                )

            # Check for API error message format: [{"message": [...]}]
            if isinstance(payload, list) and len(payload) > 0 and isinstance(payload[0], dict) and 'message' in payload[0]:
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=AdapterErrorCategory.INVALID_PAYLOAD,
                    error_detail=f"World Bank API error: {payload[0]['message']}",
                    failed_url=url,
                )

            # Validate basic envelope: must be a 2-element list [header, records]
            if not (isinstance(payload, list) and len(payload) == 2 and isinstance(payload[0], dict) and isinstance(payload[1], list)):
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=AdapterErrorCategory.INVALID_PAYLOAD,
                    error_detail=f"Invalid payload envelope structure from {url}",
                    failed_url=url,
                )

            header, data_records = payload[0], payload[1]

            # Validate header pagination invariants
            page_num = header.get('page')
            pages_cnt = header.get('pages')
            reported_total = header.get('total')
            reported_per_page = header.get('per_page')
            source_id = str(header.get('sourceid', ''))
            last_updated = str(header.get('lastupdated', ''))

            if not isinstance(page_num, int) or not isinstance(pages_cnt, int) or pages_cnt < 1:
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=AdapterErrorCategory.INCONSISTENT_PAGINATION,
                    error_detail=f"Malformed pagination numbers in header: page={page_num}, pages={pages_cnt}",
                    failed_url=url,
                )

            if page_num != current_page:
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=AdapterErrorCategory.INCONSISTENT_PAGINATION,
                    error_detail=f"Header page ({page_num}) does not match requested page ({current_page})",
                    failed_url=url,
                )

            if source_id != self.SOURCE_ID:
                return AdapterResult(
                    success=False,
                    error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                    error_detail=f"Header source '{source_id}' does not match requested source '{self.SOURCE_ID}'",
                    failed_url=url,
                )

            if (type(reported_total) is not int or reported_total < 0
                    or type(reported_per_page) is not int or reported_per_page != per_page):
                return AdapterResult(
                    success=False,
                    error_category=AdapterErrorCategory.INCONSISTENT_PAGINATION,
                    error_detail=(f"Invalid pagination metadata: total={reported_total}, "
                                  f"per_page={reported_per_page}; requested per_page={per_page}"),
                    failed_url=url,
                )

            if current_page == 1:
                total_pages = pages_cnt
                expected_total = reported_total
                expected_per_page = reported_per_page
            elif (pages_cnt != total_pages or reported_total != expected_total
                  or reported_per_page != expected_per_page):
                return AdapterResult(
                    success=False,
                    records=[],
                    pages=[],
                    error_category=AdapterErrorCategory.INCONSISTENT_PAGINATION,
                    error_detail="Pagination metadata changed during traversal",
                    failed_url=url,
                )

            page_meta = PageMetadata(
                request_url=url,
                page=page_num,
                pages=pages_cnt,
                per_page=reported_per_page,
                total=reported_total,
                source_id=source_id,
                last_updated=last_updated,
                fetched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            )
            all_pages.append(page_meta)

            # Validate each record in data_records
            for rec in data_records:
                if not isinstance(rec, dict):
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=AdapterErrorCategory.INVALID_PAYLOAD,
                        error_detail="Observation item is not a dictionary",
                        failed_url=url,
                    )

                # Validate country ISO code
                rec_country = rec.get('countryiso3code') or (rec.get('country') or {}).get('id')
                if rec_country != country:
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                        error_detail=f"Record country '{rec_country}' does not match series country '{country}'",
                        failed_url=url,
                    )

                # Validate indicator code
                rec_indicator = (rec.get('indicator') or {}).get('id')
                if rec_indicator != indicator:
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                        error_detail=f"Record indicator '{rec_indicator}' does not match series indicator '{indicator}'",
                        failed_url=url,
                    )

                # Validate observation year
                date_str = str(rec.get('date', '')).strip()
                try:
                    obs_year = int(date_str)
                except ValueError:
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=AdapterErrorCategory.INVALID_PAYLOAD,
                        error_detail=f"Invalid date/year value: '{date_str}'",
                        failed_url=url,
                    )

                # Reject years outside requested range
                if obs_year < start_year or obs_year > end_year:
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=AdapterErrorCategory.SCOPE_MISMATCH,
                        error_detail=(
                            f"Observation year {obs_year} is outside requested range "
                            f"[{start_year}, {end_year}]"
                        ),
                        failed_url=url,
                    )

                # Process value: distinguish numeric 0 from None (null)
                raw_val = rec.get('value')
                normalized_val: Optional[Decimal] = None
                raw_val_str: Optional[str] = None

                if raw_val is not None:
                    raw_val_str = str(raw_val)
                    try:
                        d_val = Decimal(str(raw_val)) if not isinstance(raw_val, Decimal) else raw_val
                        if not d_val.is_finite():
                            raise decimal.InvalidOperation('Non-finite numeric value')
                        # SnapshotValue has eight integer and eight decimal places.
                        exponent = d_val.as_tuple().exponent
                        if isinstance(exponent, int) and exponent < -8:
                            d_val = d_val.quantize(Decimal('0.00000001'), rounding=decimal.ROUND_HALF_UP)
                        integer_digits = max(d_val.adjusted() + 1, 0) if d_val else 0
                        if integer_digits > 8:
                            raise decimal.InvalidOperation('Value exceeds eight integer digits')
                    except (decimal.InvalidOperation, ValueError, TypeError):
                        return AdapterResult(
                            success=False,
                            records=[],
                            pages=[],
                            error_category=AdapterErrorCategory.INVALID_PAYLOAD,
                            error_detail=f"Unparseable numeric value: '{raw_val}'",
                            failed_url=url,
                        )

                    normalized_val = d_val.normalize()

                # Check duplicate conflicts for this series
                if obs_year in seen_years:
                    prev_val = seen_years[obs_year]
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=AdapterErrorCategory.DUPLICATE_CONFLICT,
                        error_detail=(
                            f"Duplicate conflict for {country} {indicator} {obs_year}: "
                            f"existing={prev_val} vs incoming={normalized_val}"
                        ),
                        failed_url=url,
                    )
                seen_years[obs_year] = normalized_val

                # Retrieve authoritative indicator provenance
                from core.provenance import get_indicator_provenance
                prov = get_indicator_provenance(indicator)

                record = ObservationRecord(
                    country_code=country,
                    country_name=(rec.get('country') or {}).get('value', country),
                    indicator_code=indicator,
                    indicator_name=(rec.get('indicator') or {}).get('value', prov['name']),
                    observation_year=obs_year,
                    normalized_value=normalized_val,
                    raw_value_str=raw_val_str,
                    footnote=str(rec.get('footnote') or ''),
                    unit=str(rec.get('unit') or prov['unit']),
                    obs_status=str(rec.get('obs_status') or ''),
                    indicator_definition=prov['definition'],
                    provider_label=prov['provider'],
                    source_url=prov['source_url'],
                    license=prov['license'],
                    underlying_source=prov['underlying_source'],
                )
                all_records.append(record)

            current_page += 1

        if len(all_records) != expected_total:
            return AdapterResult(
                success=False,
                error_category=AdapterErrorCategory.INCONSISTENT_PAGINATION,
                error_detail=(f"Received {len(all_records)} observations but header reported "
                              f"{expected_total}"),
                failed_url=all_pages[-1].request_url,
            )

        return AdapterResult(
            success=True,
            records=all_records,
            pages=all_pages,
        )

    def fetch_scope(
        self,
        start_year: int = DEFAULT_START_YEAR,
        end_year: Optional[int] = None,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> AdapterResult:
        """
        Orchestrates requests across the full fixed scope of 9 series
        (3 countries x 3 indicators).
        
        Atomic guarantee: If any request or series fails (including on the 9th series),
        aborts immediately and returns success=False with 0 usable observations.
        """
        if end_year is None:
            end_year = datetime.datetime.now(datetime.timezone.utc).year

        total_records: list[ObservationRecord] = []
        total_pages: list[PageMetadata] = []

        # Iterate over all 9 series in deterministic order
        for country in self.COUNTRIES:
            for indicator in self.INDICATORS:
                series_res = self.fetch_series(
                    country=country,
                    indicator=indicator,
                    start_year=start_year,
                    end_year=end_year,
                    per_page=per_page,
                )

                if not series_res.success:
                    # Atomic failure: zero observations returned
                    return AdapterResult(
                        success=False,
                        records=[],
                        pages=[],
                        error_category=series_res.error_category,
                        error_detail=(
                            f"Series failure for ({country}, {indicator}): "
                            f"{series_res.error_detail}"
                        ),
                        failed_url=series_res.failed_url,
                    )

                total_records.extend(series_res.records)
                total_pages.extend(series_res.pages)

        return AdapterResult(
            success=True,
            records=total_records,
            pages=total_pages,
        )

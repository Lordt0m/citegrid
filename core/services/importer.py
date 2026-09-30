"""
CiteGrid Import Service and Publication Pipeline.

Implements two-phase publication:
1. Fetch and validate outside transaction.
2. Inside transaction: acquire row lock on PublicationPointer, re-check current latest snapshot,
   insert Snapshot and values, advance pointer.
Durable logging of ImportRun failures on rollback.
"""
from dataclasses import dataclass, asdict
import datetime
import hashlib
import json
import os
import sys
from typing import Optional

from django.db import transaction, models
from django.utils import timezone
from django.conf import settings

from core.adapters import WorldBankAdapter, ObservationRecord
from core.models import Snapshot, SnapshotValue, ImportRun, ImportStatus, PublicationPointer, Revision
from .differ import compute_snapshot_diff


@dataclass
class ImportExecutionResult:
    success: bool
    snapshot: Optional[Snapshot] = None
    import_run: Optional[ImportRun] = None
    is_new_snapshot: bool = False
    is_repeat_snapshot: bool = False
    content_hash: Optional[str] = None
    records_count: int = 0
    revisions_count: int = 0
    error_category: Optional[str] = None
    error_detail: Optional[str] = None


def compute_canonical_content_hash(records: list[ObservationRecord], source_id: str = '2') -> str:
    """
    Computes deterministic SHA-256 hash over canonical representation of observations
    and authoritative provenance.
    Excludes volatile fetch timestamps, HTTP response order, and network duration.
    """
    sorted_records = sorted(
        records,
        key=lambda r: (source_id, r.country_code, r.indicator_code, r.observation_year),
    )

    items = []
    for r in sorted_records:
        items.append({
            'source': source_id,
            'country': r.country_code,
            'indicator': r.indicator_code,
            'year': r.observation_year,
            'value': str(r.normalized_value) if r.normalized_value is not None else None,
            'footnote': r.footnote.strip() if r.footnote else '',
            'unit': r.unit.strip() if r.unit else '',
            'definition': r.indicator_definition.strip() if r.indicator_definition else '',
            'source_url': r.source_url.strip() if r.source_url else '',
            'provider': r.provider_label.strip() if r.provider_label else '',
            'underlying_source': r.underlying_source.strip() if r.underlying_source else '',
            'license': r.license.strip() if r.license else '',
        })

    canonical_bytes = json.dumps(items, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical_bytes).hexdigest()


def is_designated_test_or_demo_db() -> tuple[bool, str]:
    """
    Verifies that the active database is explicitly designated for testing or demo use.
    Returns (is_valid, reason).
    Prevents fixture imports from ever touching the default development or production database,
    even if --allow-simulation is passed.
    """
    if 'test' in sys.argv:
        return True, "Running under test runner."

    flag_set = (
        os.environ.get('CITEGRID_IS_DEMO_DB', '').lower() in ('true', '1', 'yes')
        or getattr(settings, 'IS_DEMO_DB', False)
    )
    if not flag_set:
        return False, "CITEGRID_IS_DEMO_DB=True environment variable or settings.IS_DEMO_DB is not set."

    # Inspect active database configuration
    db_name = str(settings.DATABASES['default'].get('NAME', '')).lower()
    if 'demo' not in db_name and 'test' not in db_name:
        return False, (
            f"Active database '{db_name}' is not a designated demo/test database. "
            "Configure a dedicated database whose name contains 'demo' or 'test' (e.g. DATABASE_URL=sqlite:///demo.sqlite3)."
        )

    return True, f"Designated demo/test database '{db_name}' verified."


class ImportService:
    """
    Coordinates provider retrieval, validation, content-addressing,
    and atomic publication into the immutable snapshot ledger.
    """

    @classmethod
    def run_import(
        cls,
        adapter: WorldBankAdapter,
        start_year: int = 2000,
        end_year: Optional[int] = None,
        dry_run: bool = False,
        is_simulation: bool = False,
        allow_simulation: bool = False,
    ) -> ImportExecutionResult:
        """
        Executes an import run.
        """
        # Safety check: fixture/simulation data requires a designated test/demo DB
        if is_simulation and not dry_run:
            is_valid, reason = is_designated_test_or_demo_db()
            if not is_valid:
                raise PermissionError(
                    f"Simulated fixtures may only be imported into a designated test or demo database. {reason}"
                )

        if end_year is None:
            end_year = datetime.datetime.now(datetime.timezone.utc).year

        # Dry-run: validate and hash in-memory with ZERO database writes
        if dry_run:
            try:
                fetch_res = adapter.fetch_scope(start_year=start_year, end_year=end_year)
            except Exception as exc:
                return ImportExecutionResult(
                    success=False,
                    error_category='ADAPTER_ERROR',
                    error_detail=str(exc),
                )
            if not fetch_res.success:
                return ImportExecutionResult(
                    success=False,
                    error_category=fetch_res.error_category.value if fetch_res.error_category else 'UNKNOWN',
                    error_detail=fetch_res.error_detail,
                )

            # Validate all 9 series present
            found_series = {(r.country_code, r.indicator_code) for r in fetch_res.records}
            expected_series = {(c, i) for c in adapter.COUNTRIES for i in adapter.INDICATORS}
            if found_series != expected_series:
                missing = expected_series - found_series
                return ImportExecutionResult(
                    success=False,
                    error_category='SCOPE_MISMATCH',
                    error_detail=f"Incomplete series scope: missing {missing}",
                )

            content_hash = compute_canonical_content_hash(fetch_res.records, adapter.SOURCE_ID)
            return ImportExecutionResult(
                success=True,
                content_hash=content_hash,
                records_count=len(fetch_res.records),
                is_new_snapshot=False,
                is_repeat_snapshot=False,
            )

        # Standard import: Create durable ImportRun outside publication transaction
        import_run = ImportRun.objects.create(
            status=ImportStatus.STARTED,
            is_simulation=is_simulation,
        )

        # Phase 1: Fetch and validate all 9 series outside transaction
        try:
            fetch_res = adapter.fetch_scope(start_year=start_year, end_year=end_year)
        except Exception as exc:
            import_run.status = ImportStatus.FAILED
            import_run.ended_at = timezone.now()
            import_run.error_category = 'ADAPTER_ERROR'
            import_run.error_detail = str(exc)
            import_run.save(update_fields=['status', 'ended_at', 'error_category', 'error_detail'])
            return ImportExecutionResult(
                success=False,
                import_run=import_run,
                error_category='ADAPTER_ERROR',
                error_detail=str(exc),
            )

        if not fetch_res.success:
            import_run.status = ImportStatus.FAILED
            import_run.ended_at = timezone.now()
            import_run.error_category = fetch_res.error_category.value if fetch_res.error_category else 'UNKNOWN'
            import_run.error_detail = fetch_res.error_detail
            import_run.attempted_url = fetch_res.failed_url or ''
            import_run.request_urls = [p.request_url for p in fetch_res.pages]
            import_run.save()
            return ImportExecutionResult(
                success=False,
                import_run=import_run,
                error_category=import_run.error_category,
                error_detail=import_run.error_detail,
            )

        # Scope validation: strictly require all 9 series
        found_series = {(r.country_code, r.indicator_code) for r in fetch_res.records}
        expected_series = {(c, i) for c in adapter.COUNTRIES for i in adapter.INDICATORS}
        if found_series != expected_series:
            missing = expected_series - found_series
            import_run.status = ImportStatus.FAILED
            import_run.ended_at = timezone.now()
            import_run.error_category = 'SCOPE_MISMATCH'
            import_run.error_detail = f"Incomplete series scope: expected 9 series, missing {missing}"
            import_run.save()
            return ImportExecutionResult(
                success=False,
                import_run=import_run,
                error_category='SCOPE_MISMATCH',
                error_detail=import_run.error_detail,
            )

        content_hash = compute_canonical_content_hash(fetch_res.records, adapter.SOURCE_ID)

        # Phase 2: Atomic publication inside transaction with row-level lock
        try:
            with transaction.atomic():
                # Acquire row-level lock on singleton publication pointer
                lock, _ = PublicationPointer.objects.select_for_update().get_or_create(
                    name='wdi_publication',
                )

                # Re-check current latest snapshot
                current_latest = Snapshot.objects.filter(is_latest=True).first()

                # If current latest exists and content_hash is identical: repeat detected!
                if current_latest and current_latest.content_hash == content_hash:
                    import_run.status = ImportStatus.COMPLETED
                    import_run.ended_at = timezone.now()
                    import_run.snapshot = current_latest
                    import_run.normalized_content_hash = content_hash
                    import_run.records_count = len(fetch_res.records)
                    import_run.pages_fetched = len(fetch_res.pages)
                    import_run.request_urls = [p.request_url for p in fetch_res.pages]
                    import_run.page_metadata = [asdict(p) for p in fetch_res.pages]
                    import_run.save()

                    return ImportExecutionResult(
                        success=True,
                        snapshot=current_latest,
                        import_run=import_run,
                        is_new_snapshot=False,
                        is_repeat_snapshot=True,
                        content_hash=content_hash,
                        records_count=len(fetch_res.records),
                    )

                # Content has changed or is a return to an older state (A -> B -> A):
                # Calculate monotonic version number
                max_ver = Snapshot.objects.aggregate(models.Max('version'))['version__max'] or 0
                next_version = max_ver + 1

                retrieved_dt = timezone.now()
                if fetch_res.pages:
                    try:
                        retrieved_dt = datetime.datetime.fromisoformat(fetch_res.pages[0].fetched_at)
                    except Exception:
                        retrieved_dt = timezone.now()

                # Step 1: Create new snapshot with is_latest=False initially
                new_snapshot = Snapshot.objects.create(
                    version=next_version,
                    content_hash=content_hash,
                    retrieved_at=retrieved_dt,
                    source_id=adapter.SOURCE_ID,
                    scope_countries=list(adapter.COUNTRIES),
                    scope_indicators=list(adapter.INDICATORS),
                    scope_start_year=start_year,
                    scope_end_year=end_year,
                    is_latest=False,
                )

                # Step 2: Bulk create snapshot values for new snapshot
                values_to_create = [
                    SnapshotValue(
                        snapshot=new_snapshot,
                        source_id=adapter.SOURCE_ID,
                        country_code=r.country_code,
                        country_name=r.country_name,
                        indicator_code=r.indicator_code,
                        indicator_name=r.indicator_name,
                        observation_year=r.observation_year,
                        numeric_value=r.normalized_value,
                        raw_value_str=r.raw_value_str,
                        footnote=r.footnote,
                        unit=r.unit,
                        obs_status=r.obs_status,
                        indicator_definition=r.indicator_definition,
                        provider_label=r.provider_label,
                        source_url=r.source_url,
                        license=r.license,
                        underlying_source=r.underlying_source,
                    )
                    for r in fetch_res.records
                ]
                SnapshotValue.objects.bulk_create(values_to_create)

                # Step 3: Diff stored SnapshotValues from previous and new snapshots
                revisions = []
                if current_latest:
                    previous_values = list(current_latest.values.all())
                    new_values = list(new_snapshot.values.all())
                    revisions = compute_snapshot_diff(
                        previous_snapshot=current_latest,
                        current_snapshot=new_snapshot,
                        previous_values=previous_values,
                        current_values=new_values,
                    )
                    if revisions:
                        Revision.objects.bulk_create(revisions)

                # Step 4: Advance latest pointer atomically
                if current_latest:
                    Snapshot.objects.filter(is_latest=True).update(is_latest=False)
                new_snapshot.is_latest = True
                new_snapshot.save(update_fields=['is_latest'])

                # Step 5: Update durable import run
                import_run.status = ImportStatus.COMPLETED
                import_run.ended_at = timezone.now()
                import_run.snapshot = new_snapshot
                import_run.normalized_content_hash = content_hash
                import_run.records_count = len(values_to_create)
                import_run.pages_fetched = len(fetch_res.pages)
                import_run.request_urls = [p.request_url for p in fetch_res.pages]
                import_run.page_metadata = [asdict(p) for p in fetch_res.pages]
                import_run.save()

                return ImportExecutionResult(
                    success=True,
                    snapshot=new_snapshot,
                    import_run=import_run,
                    is_new_snapshot=True,
                    is_repeat_snapshot=False,
                    content_hash=content_hash,
                    records_count=len(values_to_create),
                    revisions_count=len(revisions),
                )
        except Exception as exc:
            # Transaction rolled back:
            # Ensure ImportRun failure record is durably preserved in the database
            import_run.status = ImportStatus.FAILED
            import_run.ended_at = timezone.now()
            import_run.error_category = 'PUBLICATION_ERROR'
            import_run.error_detail = f"Publication transaction error: {exc}"
            import_run.save()

            return ImportExecutionResult(
                success=False,
                import_run=import_run,
                error_category='PUBLICATION_ERROR',
                error_detail=str(exc),
            )

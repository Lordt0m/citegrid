"""
Management command to execute the fixed-scope World Bank WDI electricity access import.
Publishes an immutable snapshot only when all 9 series validate completely.
"""
import json
import os
import sys
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from core.adapters import WorldBankAdapter
from core.services import ImportService


class Command(BaseCommand):
    help = "Import and publish complete fixed-scope electricity access data from World Bank WDI."

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-year',
            type=int,
            default=2000,
            help="Start observation year (default: 2000).",
        )
        parser.add_argument(
            '--end-year',
            type=int,
            default=None,
            help="End observation year (default: current calendar year).",
        )
        parser.add_argument(
            '--fixture',
            type=str,
            default=None,
            help="Path to an offline 9-series JSON fixture for local testing/demo.",
        )
        parser.add_argument(
            '--allow-simulation',
            action='store_true',
            help="Explicit confirmation required when importing from a simulated fixture.",
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Validate scope and compute canonical hash without committing database changes.",
        )

    def handle(self, *args, **options):
        start_year = options['start_year']
        end_year = options['end_year']
        fixture_path = options['fixture']
        allow_simulation = options['allow_simulation']
        dry_run = options['dry_run']

        is_simulation = bool(fixture_path)

        if is_simulation:
            if not allow_simulation:
                raise CommandError(
                    "Importing from a fixture requires explicit --allow-simulation."
                )

            # Enforce that fixtures may only be imported into a designated test or demo database
            if not dry_run:
                from core.services.importer import is_designated_test_or_demo_db
                is_valid, reason = is_designated_test_or_demo_db()
                if not is_valid:
                    raise CommandError(
                        f"Simulated fixtures may only be imported into a designated test or demo database. {reason}"
                    )

            p = Path(fixture_path)
            if not p.is_file():
                raise CommandError(f"Fixture file not found: {fixture_path}")

            try:
                fixture_data = json.loads(p.read_text(encoding='utf-8'))
            except Exception as exc:
                raise CommandError(f"Invalid JSON in fixture file {fixture_path}: {exc}")

            # Build mock transport serving 9-series fixture data
            def fixture_transport(url: str, timeout: int):
                # Determine country and indicator from URL
                country = None
                for c in WorldBankAdapter.COUNTRIES:
                    if f'/country/{c}/' in url:
                        country = c
                        break
                indicator = None
                for ind in WorldBankAdapter.INDICATORS:
                    if f'/indicator/{ind}' in url:
                        indicator = ind
                        break

                if not country or not indicator:
                    return 404, "Not Found", {}

                series_key = f"{country}_{indicator}"
                if isinstance(fixture_data, dict) and series_key in fixture_data:
                    return 200, json.dumps(fixture_data[series_key]), {}
                elif isinstance(fixture_data, list):
                    # Single series payload: only match if country and indicator match the payload
                    if len(fixture_data) == 2 and isinstance(fixture_data[1], list) and len(fixture_data[1]) > 0:
                        first_rec = fixture_data[1][0]
                        c = first_rec.get('countryiso3code') or (first_rec.get('country') or {}).get('id')
                        ind = (first_rec.get('indicator') or {}).get('id')
                        if country == c and indicator == ind:
                            return 200, json.dumps(fixture_data), {}
                    return 404, f"Series {series_key} not in fixture", {}
                return 404, f"Series {series_key} not in fixture", {}

            adapter = WorldBankAdapter(transport=fixture_transport)
        else:
            adapter = WorldBankAdapter()

        self.stdout.write("Running CiteGrid WDI import across 9 series...")

        try:
            result = ImportService.run_import(
                adapter=adapter,
                start_year=start_year,
                end_year=end_year,
                dry_run=dry_run,
                is_simulation=is_simulation,
                allow_simulation=allow_simulation,
            )
        except PermissionError as exc:
            raise CommandError(str(exc))

        if dry_run:
            if not result.success:
                raise CommandError(f"[DRY RUN] Scope validation failed: [{result.error_category}] {result.error_detail}")
            self.stdout.write(self.style.SUCCESS("[DRY RUN] Scope validated successfully across 9 series."))
            self.stdout.write(f"  [DRY RUN] Observations Count: {result.records_count}")
            self.stdout.write(f"  [DRY RUN] Canonical Content Hash: {result.content_hash}")
            self.stdout.write(f"  [DRY RUN] Database status: 0 records committed (pointer unchanged).")
            return

        if not result.success:
            raise CommandError(f"Import failed: [{result.error_category}] {result.error_detail}")

        if result.is_repeat_snapshot:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Repeat content detected. ImportRun #{result.import_run.pk} recorded pointing "
                    f"to existing Snapshot v{result.snapshot.version} (hash: {result.snapshot.content_hash[:8]}). "
                    f"No new snapshot version published."
                )
            )
        elif result.is_new_snapshot:
            rev_msg = f" Detected {result.revisions_count} revisions against previous snapshot." if result.revisions_count else ""
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully published Snapshot v{result.snapshot.version} "
                    f"(hash: {result.snapshot.content_hash[:8]}). "
                    f"Committed {result.records_count} observation values into the ledger.{rev_msg}"
                )
            )

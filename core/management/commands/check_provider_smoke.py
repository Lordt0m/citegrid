"""
Management command to test live reachability of the World Bank API.
This is a separately invoked smoke check; routine tests remain strictly offline.
"""
from django.core.management.base import BaseCommand, CommandError
from core.adapters import WorldBankAdapter


class Command(BaseCommand):
    help = "Verify live reachability and schema of the World Bank WDI API."

    def handle(self, *args, **options):
        self.stdout.write("Initiating live smoke check to World Bank API...")
        adapter = WorldBankAdapter(timeout=15, max_retries=1)

        result = adapter.fetch_series(
            country='NGA',
            indicator='EG.ELC.ACCS.ZS',
            start_year=2020,
            end_year=2024,
            per_page=10,
        )

        if not result.success:
            raise CommandError(
                f"Smoke check failed: [{result.error_category}] {result.error_detail} "
                f"(URL: {result.failed_url})"
            )

        page_meta = result.pages[0] if result.pages else None
        last_updated = page_meta.last_updated if page_meta else "N/A"
        req_url = page_meta.request_url if page_meta else "N/A"

        self.stdout.write(self.style.SUCCESS("World Bank API live smoke check succeeded."))
        self.stdout.write(f"  URL: {req_url}")
        self.stdout.write(f"  Provider Last Updated: {last_updated}")
        self.stdout.write(f"  Observations Fetched: {len(result.records)}")
        if result.records:
            latest = result.records[0]
            self.stdout.write(
                f"  Sample Record: {latest.country_code} {latest.observation_year} = "
                f"{latest.normalized_value}%"
            )

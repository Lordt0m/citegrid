"""
Management command to prepare an explicitly designated test or demo database.

Enforces strict isolation before any write:
1. Requires explicit --allow-simulation flag.
2. Checks that CITEGRID_IS_DEMO_DB=True and the active database name contains 'demo' or 'test'.
3. Refuses the default development or production database.
4. Executes migrations, imports baseline 9-series fixture, and seeds the public example.
5. Safe for repeated runs without replacing unrelated data.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import SavedComparison
from core.services.importer import is_designated_test_or_demo_db
from core.services.comparisons import seed_public_example


class Command(BaseCommand):
    help = "Prepare a designated demo/test database with baseline fixture and public example."

    def add_arguments(self, parser):
        parser.add_argument(
            '--allow-simulation',
            action='store_true',
            help='Explicit confirmation required to import simulated fixture data into a demo database.',
        )

    def handle(self, *args, **options):
        # 1. Require explicit --allow-simulation
        if not options.get('allow_simulation'):
            raise CommandError(
                "Refusing demo setup: --allow-simulation is required when populating a demo database."
            )

        # 2. Require designated demo/test database before ANY write
        is_valid, reason = is_designated_test_or_demo_db()
        if not is_valid:
            raise CommandError(
                f"Refusing demo setup: {reason}\n"
                "Demo setup requires an explicitly designated test or demo database.\n"
                "Set CITEGRID_IS_DEMO_DB=True in your environment and configure a database "
                "name containing 'demo' or 'test' (e.g. DATABASE_URL=sqlite:///demo.sqlite3)."
            )

        self.stdout.write("Running migrations on designated demo/test database...")
        call_command('migrate', interactive=False, stdout=self.stdout)

        self.stdout.write("Importing baseline 9-series fixture...")
        call_command(
            'import_wdi',
            fixture='core/fixtures/provider/simulated_nine_series_complete.json',
            allow_simulation=True,
            stdout=self.stdout,
        )

        self.stdout.write("Seeding public example comparison...")
        before_count = SavedComparison.objects.filter(is_public_example=True).count()
        comparison = seed_public_example()
        after_count = SavedComparison.objects.filter(is_public_example=True).count()
        if after_count > before_count:
            self.stdout.write(self.style.SUCCESS(f"Successfully seeded public example briefing '{comparison.title}'."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Public example briefing '{comparison.title}' already exists."))

        self.stdout.write(self.style.SUCCESS("Demo setup completed successfully."))

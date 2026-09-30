"""
Management command to seed the public read-only example comparison in CiteGrid.
Pins to the earliest published snapshot by version; subsequent calls are strictly idempotent.
"""
from django.core.management.base import BaseCommand, CommandError
from core.models import SavedComparison
from core.services.comparisons import seed_public_example


class Command(BaseCommand):
    help = "Seed or verify the single public read-only example comparison pinned to the earliest snapshot."

    def handle(self, *args, **options):
        existed_before = SavedComparison.objects.filter(is_public_example=True).exists()

        try:
            example = seed_public_example()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if existed_before:
            self.stdout.write(
                self.style.WARNING(
                    f"Public example already exists: '{example.title}' "
                    f"(pinned to Snapshot v{example.snapshot.version}). No changes made."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully seeded public example: '{example.title}' "
                    f"(pinned to Snapshot v{example.snapshot.version})."
                )
            )

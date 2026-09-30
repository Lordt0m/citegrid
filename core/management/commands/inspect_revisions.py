"""
Management command to inspect revision records in the CiteGrid audit ledger.
Fulfills Ticket 4 acceptance check: Inspect representative revision records with both snapshot IDs and retrieval dates.
"""
from django.core.management.base import BaseCommand
from core.models import Snapshot, Revision


class Command(BaseCommand):
    help = "Inspect revision records between adjacent snapshots in the CiteGrid ledger."

    def add_arguments(self, parser):
        parser.add_argument(
            '--snapshot',
            type=int,
            default=None,
            help="Filter revisions where current snapshot version equals this number.",
        )

    def handle(self, *args, **options):
        target_version = options.get('snapshot')
        snapshots_count = Snapshot.objects.count()

        if snapshots_count == 0:
            self.stdout.write("No data imported yet.")
            return

        if snapshots_count == 1 and target_version in (None, 1):
            self.stdout.write("Snapshot v1 is the initial publication. No earlier snapshot exists to compare.")
            return

        revisions_qs = Revision.objects.select_related('previous_snapshot', 'current_snapshot')
        if target_version is not None:
            revisions_qs = revisions_qs.filter(current_snapshot__version=target_version)

        total_revisions = revisions_qs.count()
        if total_revisions == 0:
            self.stdout.write("No changes found between the complete snapshots stored so far.")
            return

        self.stdout.write(self.style.SUCCESS(f"=== CiteGrid Revision Ledger ({total_revisions} revisions) ==="))

        for rev in revisions_qs:
            prev = rev.previous_snapshot
            curr = rev.current_snapshot

            old_str = f"{rev.old_value}%" if rev.old_value is not None else "No data"
            new_str = f"{rev.new_value}%" if rev.new_value is not None else "No data"

            prev_date_str = rev.previous_retrieved_at.strftime('%Y-%m-%d %H:%M UTC')
            curr_date_str = rev.current_retrieved_at.strftime('%Y-%m-%d %H:%M UTC')

            self.stdout.write("")
            self.stdout.write(
                f"[{rev.transition_type}] {rev.country_code} ({rev.country_name}) | "
                f"{rev.indicator_code} | Year: {rev.observation_year}"
            )
            self.stdout.write(f"  Values:               {old_str} -> {new_str}")
            self.stdout.write(f"  Previous Snapshot:    v{prev.version} ({prev.content_hash[:8]}), observed by CiteGrid on {prev_date_str}")
            self.stdout.write(f"  Current Snapshot:     v{curr.version} ({curr.content_hash[:8]}), observed by CiteGrid on {curr_date_str}")
            self.stdout.write(f"  Attribution:          {rev.new_provider_label or rev.old_provider_label} | {rev.new_license or rev.old_license}")
            self.stdout.write(f"  Source URL:           {rev.new_source_url or rev.old_source_url}")

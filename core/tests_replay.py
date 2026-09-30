"""
Automated test suite for Ticket 8: Simulated Replay and Recruiter Path.

Verifies:
1. Pure in-memory evaluation of the 4-step revision sequence (Baseline, New Value, Changed, Withdrawn).
2. Pinned briefing stability across all 4 steps (immutability demonstration).
3. Preservation of user-selected observation year across steps without auto-jumping.
4. Failed-page scenario as an isolated illustration with identical baseline chart/table.
5. Strict zero-mutation guarantee across all six models:
   Snapshot, SnapshotValue, Revision, ImportRun, SavedComparison, and PublicationPointer.
6. Mutation rejection (@require_GET returns 405 for POST/PUT/DELETE/PATCH).
7. setup_demo management command database guards, --allow-simulation enforcement, and idempotency.
8. Navigation integration and empty-state guidance linking to replay.
"""
from decimal import Decimal
import io
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Snapshot,
    SnapshotValue,
    Revision,
    ImportRun,
    SavedComparison,
    PublicationPointer,
)
from core.services.replay import get_replay_context


class ReplayServiceAndUITests(TestCase):
    """Integration tests for the in-memory fixture replay and recruiter journey."""

    def setUp(self):
        PublicationPointer.objects.get_or_create(name='wdi_publication')

    # 1. Four-step sequence evaluation
    def test_four_step_sequence_changes_and_descriptions(self):
        """
        Exercises all 4 steps of the in-memory revision sequence:
        Step 1: Baseline (Kenya Rural 2023 missing, Nigeria Total 2018 56.5%, Ghana Urban 2015 72.5%).
        Step 2: NEW_VALUE for Kenya Rural 2023 (75.00%).
        Step 3: CHANGED for Nigeria Total 2018 (56.50% -> 58.20%, +1.70 pp).
        Step 4: WITHDRAWN for Ghana Urban 2015 (72.50% -> No data).
        """
        # Step 1: Baseline
        ctx1 = get_replay_context(scenario='revisions', step=1)
        self.assertEqual(ctx1.step, 1)
        self.assertIsNone(ctx1.focused_change)
        self.assertIn("Baseline scenario", ctx1.scenario_heading)

        # Step 2: New Value
        ctx2 = get_replay_context(scenario='revisions', step=2)
        self.assertEqual(ctx2.step, 2)
        self.assertIsNotNone(ctx2.focused_change)
        self.assertEqual(ctx2.focused_change.transition_type, 'NEW_VALUE')
        self.assertEqual(ctx2.focused_change.country_code, 'KEN')
        self.assertEqual(ctx2.focused_change.year, 2023)
        self.assertEqual(ctx2.focused_change.old_value_str, 'No data')
        self.assertEqual(ctx2.focused_change.new_value_str, '75.00%')
        self.assertIn("NEW_VALUE transition", ctx2.focused_change.explanation)

        # Step 3: Changed Value
        ctx3 = get_replay_context(scenario='revisions', step=3)
        self.assertEqual(ctx3.step, 3)
        self.assertIsNotNone(ctx3.focused_change)
        self.assertEqual(ctx3.focused_change.transition_type, 'CHANGED')
        self.assertEqual(ctx3.focused_change.country_code, 'NGA')
        self.assertEqual(ctx3.focused_change.year, 2018)
        self.assertEqual(ctx3.focused_change.old_value_str, '56.50%')
        self.assertEqual(ctx3.focused_change.new_value_str, '58.20%')
        self.assertEqual(ctx3.focused_change.delta_str, '+1.70 pp')
        self.assertIn("CHANGED transition", ctx3.focused_change.explanation)

        # Step 4: Withdrawn Value
        ctx4 = get_replay_context(scenario='revisions', step=4)
        self.assertEqual(ctx4.step, 4)
        self.assertIsNotNone(ctx4.focused_change)
        self.assertEqual(ctx4.focused_change.transition_type, 'WITHDRAWN')
        self.assertEqual(ctx4.focused_change.country_code, 'GHA')
        self.assertEqual(ctx4.focused_change.year, 2015)
        self.assertEqual(ctx4.focused_change.old_value_str, '72.50%')
        self.assertEqual(ctx4.focused_change.new_value_str, 'No data')
        self.assertIn("WITHDRAWN transition", ctx4.focused_change.explanation)

    # 2. Pinned briefing stability across all 4 steps
    def test_pinned_briefing_stability_across_all_four_steps(self):
        """
        The in-memory pinned example briefing card remains 100% identical to Step 1 baseline
        across all 4 steps, proving immutability even as observation values are simulated as changed.
        """
        for step_num in (1, 2, 3, 4):
            ctx = get_replay_context(scenario='revisions', step=step_num)
            items = {item.country_code + '_' + str(item.year): item.formatted_value for item in ctx.pinned_briefing_items}
            self.assertEqual(items['NGA_2018'], '56.50%')
            self.assertEqual(items['GHA_2015'], '72.50%')
            self.assertEqual(items['KEN_2023'], 'No data')

    # 3. Preservation of selected observation year
    def test_selected_observation_year_preserved_across_steps(self):
        """
        Binding Check 2: The user's selected observation year remains stable across steps
        and is never auto-swapped to 2023, 2018, or 2015.
        """
        for step_num in (1, 2, 3, 4):
            ctx = get_replay_context(scenario='revisions', step=step_num, selected_year=2010)
            self.assertEqual(ctx.selected_year, 2010)

        # Out-of-bounds year is clamped safely to [2000, 2024]
        ctx_low = get_replay_context(scenario='revisions', step=2, selected_year=1990)
        self.assertEqual(ctx_low.selected_year, 2000)

        ctx_high = get_replay_context(scenario='revisions', step=2, selected_year=2050)
        self.assertEqual(ctx_high.selected_year, 2024)

    # 4. Failed-page scenario isolated illustration
    def test_failed_page_scenario_isolated_illustration(self):
        """
        Binding Check 3: Failed-page scenario shows a simulated failed run log with safe error summary,
        while the chart and table remain strictly identical to Step 1 baseline.
        """
        ctx_failed = get_replay_context(scenario='failed_page', step=1)
        self.assertEqual(ctx_failed.scenario, 'failed_page')
        self.assertIsNotNone(ctx_failed.failed_run_log)
        self.assertEqual(ctx_failed.failed_run_log['status'], 'FAILED')
        self.assertIn("Page 2 could not be retrieved", ctx_failed.failed_run_log['safe_message'])
        self.assertIn("ADAPTER_ERROR", ctx_failed.failed_run_log['error_category'])

        # Chart points match baseline Step 1
        ctx_base = get_replay_context(scenario='revisions', step=1)
        failed_pts = [(p.country_code, p.year, p.value) for s in ctx_failed.series_paths for p in s.points]
        base_pts = [(p.country_code, p.year, p.value) for s in ctx_base.series_paths for p in s.points]
        self.assertEqual(failed_pts, base_pts)

    # 5. Strict Zero-Mutation Guarantee across all six models
    def test_zero_database_mutations_across_all_six_models(self):
        """
        Binding Check 5: Assert no replay request writes to ANY of the six models:
        Snapshot, SnapshotValue, Revision, ImportRun, SavedComparison, PublicationPointer.
        """
        models_to_check = [
            Snapshot,
            SnapshotValue,
            Revision,
            ImportRun,
            SavedComparison,
            PublicationPointer,
        ]

        def get_counts():
            return {m.__name__: m.objects.count() for m in models_to_check}

        initial_counts = get_counts()

        # Execute GET requests across all replay variations
        urls = [
            reverse('core:replay'),
            reverse('core:replay') + '?scenario=revisions&step=1',
            reverse('core:replay') + '?scenario=revisions&step=2',
            reverse('core:replay') + '?scenario=revisions&step=3',
            reverse('core:replay') + '?scenario=revisions&step=4',
            reverse('core:replay') + '?scenario=failed_page',
            reverse('core:replay') + '?scenario=unknown&step=999&year=1800',
        ]

        for u in urls:
            resp = self.client.get(u)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Simulated example")
            self.assertContains(resp, "Interactive Fixture Replay")
            # Verify counts on all six models remain completely unchanged
            self.assertEqual(get_counts(), initial_counts)

    # 6. Mutation Guards
    def test_replay_view_rejects_mutations(self):
        """The replay route strictly enforces @require_GET; mutations return 405."""
        url = reverse('core:replay')

        post_resp = self.client.post(url, {'step': 2})
        self.assertEqual(post_resp.status_code, 405)

        put_resp = self.client.put(url)
        self.assertEqual(put_resp.status_code, 405)

        delete_resp = self.client.delete(url)
        self.assertEqual(delete_resp.status_code, 405)

        patch_resp = self.client.patch(url)
        self.assertEqual(patch_resp.status_code, 405)

    # 7. Web Page Rendering & Step Visuals
    def test_replay_template_renders_focused_badges_and_disclaimers(self):
        """Verify template rendering of badges, diff cards, and persistent labels."""
        # Step 2: NEW_VALUE
        resp2 = self.client.get(reverse('core:replay') + '?step=2')
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, "New value")
        self.assertContains(resp2, "Kenya")
        self.assertContains(resp2, "75.00%")

        # Step 3: CHANGED
        resp3 = self.client.get(reverse('core:replay') + '?step=3')
        self.assertEqual(resp3.status_code, 200)
        self.assertContains(resp3, "Changed value")
        self.assertContains(resp3, "+1.70 pp")
        self.assertContains(resp3, "56.50%")
        self.assertContains(resp3, "58.20%")

        # Step 4: WITHDRAWN
        resp4 = self.client.get(reverse('core:replay') + '?step=4')
        self.assertEqual(resp4.status_code, 200)
        self.assertContains(resp4, "Withdrawn")
        self.assertContains(resp4, "Ghana")
        self.assertContains(resp4, "72.50%")

    # 8. setup_demo command database safety guards
    def test_setup_demo_command_enforces_safety_guards(self):
        """
        Binding Check 4: setup_demo requires an explicitly designated demo/test database
        and the --allow-simulation flag before any write.
        """
        # Missing --allow-simulation
        with self.assertRaises(CommandError) as cm:
            call_command('setup_demo')
        self.assertIn("--allow-simulation is required", str(cm.exception))

        # Non-demo database rejection
        prod_db_config = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': 'citegrid_production.sqlite3',
            }
        }
        with override_settings(DATABASES=prod_db_config, IS_DEMO_DB=False):
            with patch('sys.argv', ['manage.py', 'setup_demo']):
                with patch.dict('os.environ', {'CITEGRID_IS_DEMO_DB': ''}, clear=True):
                    with self.assertRaises(CommandError) as cm:
                        call_command('setup_demo', allow_simulation=True)
                    self.assertIn("Refusing demo setup", str(cm.exception))

    # 9. setup_demo execution and idempotency in designated database
    def test_setup_demo_execution_and_idempotency(self):
        """
        When run under a designated test/demo database with --allow-simulation,
        setup_demo populates baseline data and seeds the public example.
        Repeated runs are safe and idempotent.
        """
        out = io.StringIO()
        call_command('setup_demo', allow_simulation=True, stdout=out)
        self.assertIn("Demo setup completed successfully", out.getvalue())

        # Verify Snapshot v1 and SavedComparison exist
        self.assertTrue(Snapshot.objects.filter(is_latest=True).exists())
        self.assertTrue(SavedComparison.objects.filter(is_public_example=True).exists())

        # Second execution: safe and idempotent
        out2 = io.StringIO()
        call_command('setup_demo', allow_simulation=True, stdout=out2)
        self.assertIn("already exists", out2.getvalue())
        self.assertIn("Demo setup completed successfully", out2.getvalue())

    # 10. Empty state on public example links to replay
    def test_public_example_empty_state_links_to_replay(self):
        """
        When no public example is seeded, opening /comparisons/example/
        displays an honest unavailable state and links to /replay/.
        """
        resp = self.client.get(reverse('core:public_example'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Data is not ready yet")
        self.assertContains(resp, 'href="/replay/"')
        self.assertContains(resp, "Open Fixture Replay")

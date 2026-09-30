from django.test import TestCase, Client
from django.urls import reverse
from django.db import connection
from django.conf import settings


class PlaceholderViewTests(TestCase):
    """Test suite for CiteGrid's initial runnable placeholder view."""

    def setUp(self):
        self.client = Client()

    def test_index_status_code_and_templates(self):
        """Index endpoint should return 200 and render CiteGrid templates."""
        response = self.client.get(reverse('core:index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'core/explore_empty.html')
        self.assertTemplateUsed(response, 'base.html')

    def test_index_product_copy_and_scope(self):
        """Index page must display CiteGrid identity and fixed evidence scope."""
        response = self.client.get(reverse('core:index'))
        content = response.content.decode('utf-8')

        # Brand and headline
        self.assertIn("CiteGrid", content)
        self.assertIn("Know which figure", content)
        self.assertEqual(response.context['headline'], "Know which figure you’re citing.")

        # Three target economies
        self.assertIn("NGA", content)
        self.assertIn("Nigeria", content)
        self.assertIn("GHA", content)
        self.assertIn("Ghana", content)
        self.assertIn("KEN", content)
        self.assertIn("Kenya", content)

        # Three indicators
        self.assertIn("Total", content)
        self.assertIn("Rural", content)
        self.assertIn("Urban", content)

        # Attribution
        self.assertIn("World Bank", content)
        self.assertIn("CC BY 4.0", content)

        # Clear state indicator: No data imported yet
        self.assertIn("No data imported yet.", content)
        self.assertEqual(response.context['data_status'], "No data imported yet.")

    def test_no_default_starter_branding(self):
        """Page must not contain generic Django starter branding."""
        response = self.client.get(reverse('core:index'))
        content = response.content.decode('utf-8').lower()
        self.assertNotIn("congratulations on your first django-powered page", content)
        self.assertNotIn("django default page", content)
        self.assertNotIn("the install worked successfully", content)

    def test_health_check_endpoint(self):
        """Healthz endpoint should return 200 OK JSON response."""
        response = self.client.get(reverse('core:health_check'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('status'), 'ok')
        self.assertEqual(response.json().get('app'), 'citegrid')
        self.assertEqual(self.client.post(reverse('core:health_check')).status_code, 405)


class SettingsAndDatabaseTests(TestCase):
    """Test suite for settings and database connectivity."""

    def test_database_connection(self):
        """Verify the database is operational and can execute a test query on SQLite."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
        self.assertEqual(row[0], 1)

    def test_security_settings(self):
        """Verify secret key and host configurations are valid in dev mode."""
        self.assertTrue(bool(settings.SECRET_KEY))
        self.assertIn('localhost', settings.ALLOWED_HOSTS)
        self.assertIn('127.0.0.1', settings.ALLOWED_HOSTS)


class ProductionSecurityTests(TestCase):
    """Test suite ensuring production settings reject insecure development configurations."""

    def test_production_rejects_default_secret_key(self):
        """Production mode (DEBUG=False) must fail if default or insecure secret key is used."""
        import subprocess
        import sys

        code = (
            "import os\n"
            "os.environ['DEBUG'] = 'False'\n"
            "os.environ.pop('SECRET_KEY', None)\n"
            "import django\n"
            "from citegrid import settings\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ImproperlyConfigured", proc.stderr)
        self.assertIn("Production settings (DEBUG=False) reject insecure development SECRET_KEY", proc.stderr)

    def test_production_accepts_secure_secret_key(self):
        """Production mode (DEBUG=False) succeeds when a non-default secret key is set."""
        import subprocess
        import sys

        code = (
            "import os\n"
            "os.environ['DEBUG'] = 'False'\n"
            "os.environ['SECRET_KEY'] = 'strong-unique-random-production-key-492817294'\n"
            "from citegrid import settings\n"
            "print('SETTINGS_OK')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("SETTINGS_OK", proc.stdout)

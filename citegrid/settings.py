"""
Django settings for CiteGrid project.
"""
from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env file if present (stdlib only)
env_path = BASE_DIR / '.env'
if env_path.is_file():
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            os.environ.setdefault(key.strip(), val.strip())

from django.core.exceptions import ImproperlyConfigured

# Core security configuration
INSECURE_DEV_SECRET = 'django-insecure-citegrid-local-development-only-not-for-production'
SECRET_KEY = os.environ.get('SECRET_KEY', INSECURE_DEV_SECRET)

DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1', 'yes')

# The hosting platform terminates HTTPS and redirects HTTP requests at its edge.
# Secure cookies remain required for the public site even without Django redirects.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

if not DEBUG:
    if not SECRET_KEY or SECRET_KEY == INSECURE_DEV_SECRET or SECRET_KEY.startswith('django-insecure-'):
        raise ImproperlyConfigured(
            "Production settings (DEBUG=False) reject insecure development SECRET_KEY. "
            "Set a secure, non-development SECRET_KEY in the environment."
        )

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
    if h.strip()
]

# Designated test/demo database flag (required for simulated fixture imports)
IS_DEMO_DB = os.environ.get('CITEGRID_IS_DEMO_DB', 'False').lower() in ('true', '1', 'yes')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core.apps.CoreConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'citegrid.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'citegrid.wsgi.application'
ASGI_APPLICATION = 'citegrid.asgi.application'

# Database configuration
# Defaults to local SQLite for offline zero-setup testing and development;
# Point DATABASE_URL to a PostgreSQL connection in production or local Postgres daemon.
DEFAULT_DB_URL = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASE_URL = os.environ.get('DATABASE_URL', DEFAULT_DB_URL)

DATABASES = {
    'default': dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

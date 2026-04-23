from pathlib import Path
import os
import tempfile
try:
    import dj_database_url
except ImportError:
    dj_database_url = None

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-d*g&f7)@f!#m!m93u5m8s$nuxcld4oauz5)e*if87y^@^ca!$p')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'


def _parse_env_list(raw_value: str):
    """Parse env lists that may be comma- or space-separated."""
    normalized = raw_value.replace(",", " ")
    return [item.strip() for item in normalized.split() if item.strip()]


default_hosts = "localhost 127.0.0.1 .onrender.com"
ALLOWED_HOSTS = _parse_env_list(os.environ.get("ALLOWED_HOSTS", default_hosts))

default_csrf_origins = "https://*.onrender.com"
CSRF_TRUSTED_ORIGINS = _parse_env_list(
    os.environ.get("CSRF_TRUSTED_ORIGINS", default_csrf_origins)
)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'et',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'et_site.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'et_site.wsgi.application'

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and dj_database_url:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
elif DATABASE_URL and not dj_database_url:
    raise RuntimeError(
        "DATABASE_URL is set but dj-database-url is not installed. "
        "Install dependencies from requirements.txt."
    )
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Use file-based server-side sessions for larger workflow payloads
# without DB writes and without in-memory deepcopy behavior.
SESSION_ENGINE = "django.contrib.sessions.backends.file"
default_session_dir = os.path.join(tempfile.gettempdir(), "django_sessions")
SESSION_FILE_PATH = os.environ.get("DJANGO_SESSION_FILE_PATH", default_session_dir)
os.makedirs(SESSION_FILE_PATH, exist_ok=True)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# In-process cache (per web worker) for small, frequently-hit forecast URLs.
# For multi-worker production this is still effective at reducing hot-path latency.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "et-locmem",
    }
}
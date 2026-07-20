from pathlib import Path
from django.utils.translation import gettext_lazy as _
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent

def static_lambda(path):
    from django.templatetags.static import static
    return static(path)

def reverse_lazy_lambda(viewname):
    from django.urls import reverse_lazy
    return reverse_lazy(viewname)

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dummy-key-for-tests')
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# Refuse to boot a production configuration on a known-insecure key: a
# predictable SECRET_KEY breaks sessions, CSRF and password-reset tokens.
_INSECURE_KEYS = {
    'django-insecure-dummy-key-for-tests',
    'django-insecure-replace-this-with-a-real-secret-key-in-production',
}
if not DEBUG and SECRET_KEY in _INSECURE_KEYS:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        "DEBUG=False with a placeholder SECRET_KEY — set a real SECRET_KEY env var."
    )

INSTALLED_APPS = [
    # UI Customization
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.inlines',
    'unfold.contrib.import_export',
    'unfold.contrib.guardian',
    'unfold.contrib.simple_history',

    # WYSIWYG
    'django_ckeditor_5',

    # Django Core
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # SnapAdmin Stack
    'rest_framework',               # REQUIRED
    'drf_spectacular',              # REQUIRED
    'django_filters',               # REQUIRED
    'graphene_django',              # REQUIRED for GraphQL
    'admin_auto_filters',           # Optional extra: django-snapadmin[autocomplete-filter] (LGPL; core doesn't use it)
    'rangefilter',                  # REQUIRED
    'snapadmin',                    # REQUIRED

    # Celery result/beat storage (Django DB backend)
    'django_celery_beat',            # Celery Beat admin + DB-backed schedule
    'django_celery_results',         # Store task results in Django DB

    # Other modules
    'extra_settings',               # Optional extra: pip install django-snapadmin[extra-settings]

    # Local Apps
    'demo.apps.shop',                       # label stays "demo" (DemoConfig.label) — Testing
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise serves static files under gunicorn (DEBUG=False or no runserver).
    # Must sit directly after SecurityMiddleware.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # i18n (issue #9): must sit after SessionMiddleware and before CommonMiddleware
    # so the active locale is resolved from the session/cookie/Accept-Language.
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'demo.core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'demo' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Exposes `snapadmin_sso_providers` to the admin login override.
                'snapadmin.sso.sso_providers',
            ],
        },
    },
]

WSGI_APPLICATION = 'demo.core.wsgi.application'

DB_HOST = os.getenv('POSTGRES_HOST')
if DB_HOST:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('POSTGRES_DB', 'snapadmin'),
            'USER': os.getenv('POSTGRES_USER', 'snapadmin'),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'snapadmin'),
            'HOST': DB_HOST,
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / '.db.sqlite3',
        }
    }

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

LANGUAGE_CODE = 'en'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# i18n (issue #9). SnapAdmin ships translation catalogs for these locales; a
# missing string falls back to English automatically. The admin language
# switcher (snapadmin/language_switcher.html) posts to django's set_language.
from django.utils.translation import gettext_lazy as _i18n  # noqa: E402
LANGUAGES = [
    ('en', _i18n('English')),
    ('ru', _i18n('Russian')),
    ('de', _i18n('German')),
    ('de-ch', _i18n('Swiss German')),
    ('fr', _i18n('French')),
    ('fr-ch', _i18n('Swiss French')),
    ('es', _i18n('Spanish')),
    ('it', _i18n('Italian')),
    ('pl', _i18n('Polish')),
    ('nl', _i18n('Dutch')),
]
# SnapAdmin's own catalogs live inside the package; Django also reads each app's
# locale/ dir, so this is mainly for a project-level override directory. Kept under
# demo/ (not the repo root) so the demo project stays self-contained and a stray
# `makemessages` doesn't drop an empty catalog dir at the top level.
LOCALE_PATHS = [BASE_DIR / 'demo' / 'locale']

STATIC_URL = 'static/'
STATICFILES_DIRS = []
STATIC_ROOT = BASE_DIR / ".staticfiles"

# WhiteNoise: compress collected static files (gzip/brotli). No manifest, so a
# missing reference never 500s the page.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_REDIRECT_URL = '/admin/'

# ------------------------------------------------------------------------------
# SNAPADMIN CONFIGURATION
# ------------------------------------------------------------------------------
# Feature toggles — each API surface can be switched off independently via .env
# (SNAPADMIN_REST_API_ENABLED=False removes all /api/ CRUD routes, etc.).
SNAPADMIN_REST_API_ENABLED = os.getenv('SNAPADMIN_REST_API_ENABLED', 'True') == 'True'
SNAPADMIN_SWAGGER_ENABLED = os.getenv('SNAPADMIN_SWAGGER_ENABLED', 'True') == 'True'
SNAPADMIN_GRAPHQL_ENABLED = os.getenv('SNAPADMIN_GRAPHQL_ENABLED', 'True') == 'True'
# Optional extra segment prepended to every snapadmin route (REST/Swagger/GraphQL),
# for projects whose mount point already collides (e.g. they own /api/). Empty = no-op.
SNAPADMIN_URL_PREFIX = os.getenv('SNAPADMIN_URL_PREFIX', '')
# Dashboard is staff-gated by default (it exposes infra details). True = public.
SNAPADMIN_DASHBOARD_PUBLIC = os.getenv('SNAPADMIN_DASHBOARD_PUBLIC', 'False') == 'True'

# --- API capacity & abuse protection -----------------------------------------
# These bound how much work one request can cost and how fast callers may issue
# them. Deliberately deployment-owned (.env / this file) rather than runtime-
# editable through the admin: they are operational controls, so relaxing one
# should go through the same review and rollout as the rest of the infra config.
# See demo/apps/shop/managed_settings.py for why they are excluded there.
# Default page size for the auto-generated REST API list endpoints.
SNAPADMIN_API_PAGE_SIZE = int(os.getenv('SNAPADMIN_API_PAGE_SIZE', '25'))
# Hard ceiling on a client-requested ?page_size= on the REST API.
SNAPADMIN_API_MAX_PAGE_SIZE = int(os.getenv('SNAPADMIN_API_MAX_PAGE_SIZE', '500'))
# DRF rate limits (e.g. '60/min'). Empty value disables that throttle.
SNAPADMIN_THROTTLE_ANON = os.getenv('SNAPADMIN_THROTTLE_ANON', '60/min') or None
SNAPADMIN_THROTTLE_USER = os.getenv('SNAPADMIN_THROTTLE_USER', '600/min') or None
# Row ceiling on the synchronous streaming export before it answers 413 and
# steers the caller to the async export API (0 = unlimited).
SNAPADMIN_EXPORT_MAX_ROWS = int(os.getenv('SNAPADMIN_EXPORT_MAX_ROWS', '0'))
# Hard cap clamped onto an explicit ?limit= on the streaming export (0 = no clamp).
SNAPADMIN_EXPORT_LIMIT_MAX = int(os.getenv('SNAPADMIN_EXPORT_LIMIT_MAX', '0'))

# Smart ES query routing: `?search=` API requests on DUAL models run on
# Elasticsearch (fuzzy, relevance-ranked) instead of DB icontains. Global
# kill-switch; per-model opt-out via `es_query_routing = False`.
SNAPADMIN_ES_QUERY_ROUTING = os.getenv('SNAPADMIN_ES_QUERY_ROUTING', 'True') == 'True'
# Max hits fetched from ES per routed search / ES_ONLY listing.
SNAPADMIN_ES_SEARCH_LIMIT = int(os.getenv('SNAPADMIN_ES_SEARCH_LIMIT', '1000'))

# Expose the X-Snap-Query-Backend header on list responses (elasticsearch|database).
SNAPADMIN_QUERY_BACKEND_HEADER = os.getenv('SNAPADMIN_QUERY_BACKEND_HEADER', 'True') == 'True'

# Project-wide deletion veto for the dynamic model API: dotted path to a
# Callable[[request, obj], bool]. Returning False makes DELETE respond 403.
# Combined (AND) with each model's own SnapModel.api_can_delete(request) hook.
# Unset (default) → deletes are governed solely by model permissions + hooks.
SNAPADMIN_API_DELETE_GUARD = os.getenv('SNAPADMIN_API_DELETE_GUARD') or None

# Admin-only HTTP endpoint to bulk-reindex ES-enabled SnapModels
# (POST /api/es/reindex/, IsAdminUser). Off by default; the endpoint 404s while
# disabled. When async is on, the reindex is offloaded to the
# snapadmin.run_es_reindex Celery task (needs Celery + a broker).
SNAPADMIN_REINDEX_API_ENABLED = os.getenv('SNAPADMIN_REINDEX_API_ENABLED', 'False') == 'True'
SNAPADMIN_REINDEX_API_ASYNC = os.getenv('SNAPADMIN_REINDEX_API_ASYNC', 'False') == 'True'

# Read-replica routing: alias (from DATABASES) that auto-generated read-only
# API list/retrieve querysets are pinned to via .using(). Writes always stay on
# 'default'. Empty / unknown alias → no routing (safe for single-DB installs).
SNAPADMIN_ANALYTICS_DB_ALIAS = os.getenv('SNAPADMIN_ANALYTICS_DB_ALIAS', '')

# Enterprise SSO/OAuth2 login buttons (issue #13). SnapAdmin only *renders* the
# providers you already wired into AUTHENTICATION_BACKENDS / URLconf — it adds no
# auth dependency. Exposed on the login page and at /api/sso-providers/.
# Format: {"<key>": {"label": "...", "url": "/accounts/<p>/login/", "icon": "..."}}.
SNAPADMIN_SSO_PROVIDERS = {}

# Wysiwyg HTML sanitizer. Rich-text field values are sanitized before being shown
# in the admin changelist (stored-XSS defense). Leave unset to use the built-in
# nh3 allowlist, or point this at a dotted path to your own Callable[[str], str].
# SNAPADMIN_HTML_SANITIZER = "myapp.security.clean_html"

# PII masking (issue #12). Map "app_label.ModelName" → list of sensitive fields
# obfuscated in the admin + REST API for users lacking `snapadmin.view_raw_pii`
# (superusers always see raw). Empty → masking off.
SNAPADMIN_MASKED_FIELDS = {}

# Admin-index nesting (issues #4 / #16). Fold auto-generated sections into
# existing app groups, hide groups, or rename headings — no custom AdminSite.
# All empty → the index is left exactly as Django builds it.
SNAPADMIN_NESTED_APPS = {}   # {"snapadmin": "auth"} → move snapadmin models under "auth"
SNAPADMIN_HIDDEN_APPS = []   # ["silk"]            → drop these groups from the index
SNAPADMIN_APP_LABELS = {}    # {"auth": "Administration"} → rename a group's heading

# Audit trail (issue #7 — DORA / ISO 27001). Records every admin create/update/
# delete as an immutable SnapadminAuditLog (who/what/when + before/after diff).
# Export for a SIEM with `manage.py snapadmin_audit_export`.
SNAPADMIN_AUDIT_LOG_ENABLED = os.getenv('SNAPADMIN_AUDIT_LOG_ENABLED', 'True') == 'True'
SNAPADMIN_AUDIT_RETENTION_DAYS = int(os.getenv('SNAPADMIN_AUDIT_RETENTION_DAYS', '365'))

# Large-dataset performance (issue #5). Replace the changelist's expensive
# COUNT(*) with PostgreSQL's fast planner estimate on unfiltered listings of
# tables larger than the threshold; exact count everywhere else. Off → always
# exact. Only affects huge PG tables — small/filtered/other-DB views unchanged.
SNAPADMIN_ESTIMATED_COUNT = os.getenv('SNAPADMIN_ESTIMATED_COUNT', 'True') == 'True'
SNAPADMIN_ESTIMATED_COUNT_THRESHOLD = int(os.getenv('SNAPADMIN_ESTIMATED_COUNT_THRESHOLD', '100000'))

# Async background export (issue #6). POST /api/exports/ enqueues a Celery job
# that streams a model's rows to CSV/JSON in resumable chunks; poll, cancel and
# download via the API. Requires Celery + a broker (runs inline under eager mode).
SNAPADMIN_EXPORT_ENABLED = os.getenv('SNAPADMIN_EXPORT_ENABLED', 'True') == 'True'
SNAPADMIN_EXPORT_CHUNK_SIZE = int(os.getenv('SNAPADMIN_EXPORT_CHUNK_SIZE', '1000'))
SNAPADMIN_EXPORT_DIR = os.getenv('SNAPADMIN_EXPORT_DIR', str(BASE_DIR / 'exports'))
# Dotted path to a django.core.files.storage.Storage subclass export files are
# published to and downloaded from. Unset (default) → local FileSystemStorage
# rooted at SNAPADMIN_EXPORT_DIR — set this in a split deployment where the web
# process and the Celery worker don't share a filesystem.
SNAPADMIN_EXPORT_STORAGE = os.getenv('SNAPADMIN_EXPORT_STORAGE', '')

# GraphQL security: require authentication + per-model view permission on every
# resolver (mirrors the REST API contract). Never disable in production.
SNAPADMIN_GRAPHQL_REQUIRE_AUTH = os.getenv('SNAPADMIN_GRAPHQL_REQUIRE_AUTH', 'True') == 'True'
# GraphiQL playground — defaults to DEBUG; keep it out of production.
SNAPADMIN_GRAPHIQL_ENABLED = os.getenv('SNAPADMIN_GRAPHIQL_ENABLED', str(DEBUG)) == 'True'

# Admin-only user management API (/api/users/, /api/permissions/).
# Off by default; the demo enables it so the endpoints show up in Swagger.
SNAPADMIN_USER_API_ENABLED = os.getenv('SNAPADMIN_USER_API_ENABLED', 'True') == 'True'

# API authentication classes (dotted paths). Package default (unset) is
# SnapAdmin token auth only; the demo also enables SessionAuthentication so the
# browsable API and the admin-only user-management API work from a logged-in
# admin session. Add JWT here, e.g.:
#   rest_framework_simplejwt.authentication.JWTAuthentication
_SNAP_AUTH = os.getenv('SNAPADMIN_API_AUTHENTICATION_CLASSES', '')
if _SNAP_AUTH:
    SNAPADMIN_API_AUTHENTICATION_CLASSES = [c.strip() for c in _SNAP_AUTH.split(',') if c.strip()]
else:
    SNAPADMIN_API_AUTHENTICATION_CLASSES = [
        'snapadmin.api.authentication.APITokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ]

# ------------------------------------------------------------------------------
# EMAIL (required for SnapAdmin error alerts / daily digest)
# ------------------------------------------------------------------------------
# Console backend in DEBUG so alerts are visible without an SMTP server;
# real SMTP in production via the EMAIL_* env vars.
EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend' if DEBUG
    else 'django.core.mail.backends.smtp.EmailBackend',
)
EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'snapadmin@localhost')

# ------------------------------------------------------------------------------
# ERROR MONITORING (optional email notifications)
# ------------------------------------------------------------------------------
# Records every unhandled exception / 5xx as an ErrorEvent (visible in the
# admin) and emails a spike alert when the 15-minute threshold is crossed.
SNAPADMIN_ERROR_MONITOR_ENABLED = os.getenv('SNAPADMIN_ERROR_MONITOR_ENABLED', 'True') == 'True'
if SNAPADMIN_ERROR_MONITOR_ENABLED:
    MIDDLEWARE.append('snapadmin.middleware.SnapErrorMonitorMiddleware')

# Spike alert: N errors within the window → one email per cooldown.
SNAPADMIN_ERROR_ALERT_ENABLED = os.getenv('SNAPADMIN_ERROR_ALERT_ENABLED', 'True') == 'True'
SNAPADMIN_ERROR_ALERT_THRESHOLD = int(os.getenv('SNAPADMIN_ERROR_ALERT_THRESHOLD', '20'))
SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES = int(os.getenv('SNAPADMIN_ERROR_ALERT_WINDOW_MINUTES', '15'))
SNAPADMIN_ERROR_ALERT_EMAILS = [
    e.strip() for e in os.getenv('SNAPADMIN_ERROR_ALERT_EMAILS', '').split(',') if e.strip()
]

# Daily digest: grouped report of the last 24h, capped at MAX_GROUPS groups.
SNAPADMIN_ERROR_DIGEST_ENABLED = os.getenv('SNAPADMIN_ERROR_DIGEST_ENABLED', 'True') == 'True'
SNAPADMIN_ERROR_DIGEST_EMAILS = [
    e.strip() for e in os.getenv('SNAPADMIN_ERROR_DIGEST_EMAILS', '').split(',') if e.strip()
]
SNAPADMIN_ERROR_DIGEST_MAX_GROUPS = int(os.getenv('SNAPADMIN_ERROR_DIGEST_MAX_GROUPS', '20'))
SNAPADMIN_ERROR_DIGEST_HOUR = int(os.getenv('SNAPADMIN_ERROR_DIGEST_HOUR', '8'))
SNAPADMIN_ERROR_DIGEST_MINUTE = int(os.getenv('SNAPADMIN_ERROR_DIGEST_MINUTE', '0'))

# ErrorEvent rows older than this are purged by the digest task.
SNAPADMIN_ERROR_RETENTION_DAYS = int(os.getenv('SNAPADMIN_ERROR_RETENTION_DAYS', '30'))

# ------------------------------------------------------------------------------
# 3-2-1 DATABASE BACKUPS (optional)
# ------------------------------------------------------------------------------
# Three destinations, each with its own frequency: LOCAL dir on this server,
# NETWORK dir (a mounted share on another server), REMOTE offsite FTP.
SNAPADMIN_BACKUP_ENABLED = os.getenv('SNAPADMIN_BACKUP_ENABLED', 'False') == 'True'
SNAPADMIN_BACKUP_KEEP = int(os.getenv('SNAPADMIN_BACKUP_KEEP', '7'))

SNAPADMIN_BACKUP_LOCAL_DIR = os.getenv('SNAPADMIN_BACKUP_LOCAL_DIR', str(BASE_DIR / 'backups'))
SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS = int(os.getenv('SNAPADMIN_BACKUP_LOCAL_EVERY_HOURS', '24'))

SNAPADMIN_BACKUP_NETWORK_DIR = os.getenv('SNAPADMIN_BACKUP_NETWORK_DIR', '')
SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS = int(os.getenv('SNAPADMIN_BACKUP_NETWORK_EVERY_HOURS', '24'))

SNAPADMIN_BACKUP_FTP_HOST = os.getenv('SNAPADMIN_BACKUP_FTP_HOST', '')
SNAPADMIN_BACKUP_FTP_PORT = int(os.getenv('SNAPADMIN_BACKUP_FTP_PORT', '21'))
SNAPADMIN_BACKUP_FTP_USER = os.getenv('SNAPADMIN_BACKUP_FTP_USER', '')
SNAPADMIN_BACKUP_FTP_PASSWORD = os.getenv('SNAPADMIN_BACKUP_FTP_PASSWORD', '')
SNAPADMIN_BACKUP_FTP_DIR = os.getenv('SNAPADMIN_BACKUP_FTP_DIR', '/')
SNAPADMIN_BACKUP_FTP_TLS = os.getenv('SNAPADMIN_BACKUP_FTP_TLS', 'False') == 'True'
SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS = int(os.getenv('SNAPADMIN_BACKUP_REMOTE_EVERY_HOURS', '168'))

# Copy 3 (alternative) — SFTP over SSH; needs the optional paramiko dependency.
SNAPADMIN_BACKUP_SFTP_HOST = os.getenv('SNAPADMIN_BACKUP_SFTP_HOST', '')
SNAPADMIN_BACKUP_SFTP_PORT = int(os.getenv('SNAPADMIN_BACKUP_SFTP_PORT', '22'))
SNAPADMIN_BACKUP_SFTP_USER = os.getenv('SNAPADMIN_BACKUP_SFTP_USER', '')
SNAPADMIN_BACKUP_SFTP_PASSWORD = os.getenv('SNAPADMIN_BACKUP_SFTP_PASSWORD', '')
SNAPADMIN_BACKUP_SFTP_KEY_FILE = os.getenv('SNAPADMIN_BACKUP_SFTP_KEY_FILE', '')
SNAPADMIN_BACKUP_SFTP_DIR = os.getenv('SNAPADMIN_BACKUP_SFTP_DIR', '/')
SNAPADMIN_BACKUP_SFTP_EVERY_HOURS = int(os.getenv('SNAPADMIN_BACKUP_SFTP_EVERY_HOURS', '168'))

# ------------------------------------------------------------------------------
# ELASTICSEARCH
# ------------------------------------------------------------------------------
# Read from the environment so the docker-compose `--profile es` stack (which sets
# ELASTICSEARCH_ENABLED=True / ELASTICSEARCH_URL in .env) actually activates ES in
# the demo. Defaults keep ES off for local dev and the test suite.
ELASTICSEARCH_ENABLED = os.getenv('ELASTICSEARCH_ENABLED', 'False') == 'True'
ELASTICSEARCH_URL = os.getenv('ELASTICSEARCH_URL', 'http://localhost:9200')

# Extra kwargs merged into the Elasticsearch(...) constructor: api_key,
# basic_auth, ca_certs, verify_certs, request_timeout, max_retries, ...
# (For fully custom clients set SNAPADMIN_ES_CLIENT_FACTORY to a dotted path.)
ELASTICSEARCH_KWARGS = {
    'request_timeout': int(os.getenv('ELASTICSEARCH_TIMEOUT', '5')),
}

# ------------------------------------------------------------------------------
# REST FRAMEWORK
# ------------------------------------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'snapadmin.api.authentication.APITokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # Brute-force / abuse protection: anonymous callers are limited hard
    # (they can only probe auth), authenticated clients generously.
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('SNAPADMIN_THROTTLE_ANON', '60/min'),
        'user': os.getenv('SNAPADMIN_THROTTLE_USER', '600/min'),
    },
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'SnapAdmin API',
    'DESCRIPTION': 'SnapAdmin Auto-generated API Documentation',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SECURITY': [{'TokenAuth': []}],
    'COMPONENT_SPLIT_PATCH': True,
    'COMPONENT_SPLIT_REQUEST': True,
    'APPEND_COMPONENTS': {
        "securitySchemes": {
            "TokenAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": 'Enter your token in the format: "Token <your_token_value>"'
            }
        }
    },
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': True,
        'filter': True,
    },
}

# ------------------------------------------------------------------------------
# CELERY
# ------------------------------------------------------------------------------
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Celery Beat scheduled tasks
# Each task runs on its own cron schedule and is visible in the dashboard.
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "reindex-products-to-es": {
        "task": "demo.tasks.reindex_products_to_elasticsearch",
        "schedule": crontab(hour=0, minute=0),  # daily midnight
        "description": "Sync all Product records from DB to Elasticsearch (DUAL mode demo)",
    },
    "purge-expired-data": {
        "task": "snapadmin.purge_expired_data",
        "schedule": crontab(hour=1, minute=0),  # daily 1am
        "description": "GDPR - delete records older than data_retention_days on each model",
    },
    "generate-daily-stats": {
        "task": "demo.tasks.generate_daily_stats",
        "schedule": crontab(hour=2, minute=0),  # daily 2am
        "description": "Compute and log daily business stats (products, customers, orders, revenue)",
    },
    "purge-expired-tokens": {
        "task": "snapadmin.purge_expired_tokens",
        "schedule": crontab(hour=3, minute=0),  # daily 3am
        "description": "Remove expired API tokens",
    },
    "send-error-digest": {
        "task": "snapadmin.send_error_digest",
        # Send time is env-configurable: SNAPADMIN_ERROR_DIGEST_HOUR / _MINUTE
        "schedule": crontab(hour=SNAPADMIN_ERROR_DIGEST_HOUR, minute=SNAPADMIN_ERROR_DIGEST_MINUTE),
        "description": "Email the grouped 24h error digest and purge expired ErrorEvents",
    },
    "run-db-backups": {
        "task": "snapadmin.run_db_backups",
        # Hourly check — each destination fires only when its own
        # SNAPADMIN_BACKUP_*_EVERY_HOURS interval has elapsed.
        "schedule": crontab(minute=30),
        "description": "3-2-1 DB backups to local / network / remote FTP when due",
    },
}

# ------------------------------------------------------------------------------
# EXTRA SETTINGS (optional — django-snapadmin[extra-settings]; used by the demo only)
# ------------------------------------------------------------------------------
# EXTRA_SETTINGS_ADMIN_APP must match an INSTALLED_APPS entry *literally* (extra_settings
# checks membership in settings.INSTALLED_APPS, not the app's label) — this project lists
# the demo app as 'demo.apps.shop' (DemoConfig.name), even though its app_label is the shorter
# 'demo' (DemoConfig.label), so this must be the dotted path, not the bare label.
EXTRA_SETTINGS_ADMIN_APP = "demo.apps.shop"
EXTRA_SETTINGS_CACHE_NAME = "extra_settings"
EXTRA_SETTINGS_VERBOSE_NAME = _("Settings")

# Surface a curated set of *runtime-editable* SNAPADMIN_* settings as DB-backed,
# admin-editable extra_settings rows (demo-only bridge; see
# demo/apps/shop/managed_settings.py for the how/why and the exclusion rules). The
# seed value for each row is the demo's own configured value when settings.py
# already defines one, otherwise the package default carried in the spec.
from demo.apps.shop.managed_settings import (  # noqa: E402
    MANAGED_SETTING_NAMES,
    build_extra_settings_defaults,
)

EXTRA_SETTINGS_DEFAULTS = build_extra_settings_defaults(
    overrides={
        name: globals()[name]
        for name in MANAGED_SETTING_NAMES
        if name in globals()
    }
)

# ------------------------------------------------------------------------------
# CKEDITOR 5 CONFIGURATION
# ------------------------------------------------------------------------------

CKEDITOR_5_CONFIGS = {
    'default': {
        'toolbar': ['heading', '|', 'bold', 'italic', 'link',
                    'bulletedList', 'numberedList', 'blockQuote', 'imageUpload', ],

    },
    'extends': {
        'blockToolbar': [
            'paragraph', 'heading1', 'heading2', 'heading3',
            '|',
            'bulletedList', 'numberedList',
            '|',
            'blockQuote',
        ],
        'toolbar': ['heading', '|', 'outdent', 'indent', '|', 'bold', 'italic', 'link', 'underline', 'strikethrough',
        'code','subscript', 'superscript', 'highlight', '|', 'codeBlock', 'sourceEditing', 'insertImage',
                    'bulletedList', 'numberedList', 'todoList', '|',  'blockQuote', 'imageUpload', '|',
                    'fontSize', 'fontFamily', 'fontColor', 'fontBackgroundColor', 'mediaEmbed', 'removeFormat',
                    'insertTable',],
        'image': {
            'toolbar': ['imageTextAlternative', '|', 'imageStyle:alignLeft',
                        'imageStyle:alignCenter', 'imageStyle:alignRight'],
            'styles': [
                'alignLeft',
                'alignCenter',
                'alignRight',
            ]
        }
    },
    'list': {
        'properties': {
            'styles': 'true',
            'startIndex': 'true',
            'reversed': 'true',
        }
    }
}

# ------------------------------------------------------------------------------
# UNFOLD CONFIGURATION
# ------------------------------------------------------------------------------

UNFOLD = {
    "SITE_TITLE": "SnapAdmin",
    "SITE_HEADER": "SnapAdmin",
    "SITE_SUBHEADER": "The ultimate Django admin",
    "SITE_DROPDOWN": [
        {
            "icon": "diamond",
            "title": _("My site"),
            "link": "https://example.com",
        },
    ],
    "SITE_URL": "/",
    "SITE_ICON": {
        "light": lambda request: static_lambda("snapadmin/snap-logo.svg"),
        "dark": lambda request: static_lambda("snapadmin/snap-logo.svg"),
    },
    "SITE_LOGO": {
        "light": lambda request: static_lambda("snapadmin/snap-logo.svg"),
        "dark": lambda request: static_lambda("snapadmin/snap-logo.svg"),
    },
    "SITE_SYMBOL": "speed",
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "32x32",
            "type": "image/svg+xml",
            "href": lambda request: static_lambda("favicon.svg"),
        },
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": False,
    "THEME": "light",
    "LOGIN": {
        "image": lambda request: static_lambda("sample/login-bg.jpg"),
        "redirect_after": lambda request: reverse_lazy_lambda("admin:index"),
    },
    "STYLES": [
        lambda request: static_lambda("snapadmin/css/admin.css"),
    ],
    "SCRIPTS": [
        lambda request: static_lambda("snapadmin/js/admin.js"),
    ],
    "BORDER_RADIUS": "6px",
    "COLORS": {
        "base": {
            "50": "oklch(98.5% .002 247.839)",
            "100": "oklch(96.7% .003 264.542)",
            "200": "oklch(92.8% .006 264.531)",
            "300": "oklch(87.2% .01 258.338)",
            "400": "oklch(70.7% .022 261.325)",
            "500": "oklch(55.1% .027 264.364)",
            "600": "oklch(44.6% .03 256.802)",
            "700": "oklch(37.3% .034 259.733)",
            "800": "oklch(27.8% .033 256.848)",
            "900": "oklch(21% .034 264.665)",
            "950": "oklch(13% .028 261.692)",
        },
        "primary": {
            "50": "oklch(97.7% .014 308.299)",
            "100": "oklch(94.6% .033 307.174)",
            "200": "oklch(90.2% .063 306.703)",
            "300": "oklch(82.7% .119 306.383)",
            "400": "oklch(71.4% .203 305.504)",
            "500": "oklch(62.7% .265 303.9)",
            "600": "oklch(55.8% .288 302.321)",
            "700": "oklch(49.6% .265 301.924)",
            "800": "oklch(43.8% .218 303.724)",
            "900": "oklch(38.1% .176 304.987)",
            "950": "oklch(29.1% .149 302.717)",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",
            "subtle-dark": "var(--color-base-400)",
            "default-light": "var(--color-base-600)",
            "default-dark": "var(--color-base-300)",
            "important-light": "var(--color-base-900)",
            "important-dark": "var(--color-base-100)",
        },
    },
    "EXTENSIONS": {
        "modeltranslation": {
            "flags": {
                "en": "🇬🇧",
                "fr": "🇫🇷",
                "nl": "🇧🇪",
            },
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "command_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": _("Navigation"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "dashboard",
                        "link": reverse_lazy_lambda("admin:index"),
                        "badge": lambda request: "Snap",
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Users"),
                        "icon": "people",
                        "link": reverse_lazy_lambda("admin:auth_user_changelist"),
                    },
                ],
            },
            {
                "title": _("Business Logic"),
                "items": [
                    {
                        "title": _("Categories"),
                        "icon": "category",
                        "link": reverse_lazy_lambda("admin:demo_category_changelist"),
                    },
                    {
                        "title": _("Tags"),
                        "icon": "label",
                        "link": reverse_lazy_lambda("admin:demo_tag_changelist"),
                    },
                    {
                        "title": _("Products"),
                        "icon": "inventory_2",
                        "link": reverse_lazy_lambda("admin:demo_product_changelist"),
                    },
                    {
                        "title": _("Orders"),
                        "icon": "shopping_cart",
                        "link": reverse_lazy_lambda("admin:demo_order_changelist"),
                    },
                    {
                        "title": _("Customers"),
                        "icon": "person",
                        "link": reverse_lazy_lambda("admin:demo_customer_changelist"),
                    },
                ],
            },
            {
                "title": _("System & Logs"),
                "items": [
                    {
                        "title": _("Showcase"),
                        "icon": "biotech",
                        "link": reverse_lazy_lambda("admin:demo_showcase_changelist"),
                    },
                    {
                        "title": _("Search Logs"),
                        "icon": "manage_search",
                        "link": reverse_lazy_lambda("admin:demo_searchlog_changelist"),
                    },
                    {
                        "title": _("Exchange Rates"),
                        "icon": "currency_exchange",
                        "link": reverse_lazy_lambda("admin:demo_exchangerate_changelist"),
                    },
                    {
                        "title": _("Error Events"),
                        "icon": "report",
                        "link": reverse_lazy_lambda("admin:snapadmin_errorevent_changelist"),
                    },
                ],
            },
            {
                "title": _("Security"),
                "items": [
                    {
                        "title": _("API Tokens"),
                        "icon": "key",
                        "link": reverse_lazy_lambda("admin:snapadmin_apitoken_changelist"),
                    },
                ],
            },
        ],
    },
    "TABS": [
        {
            "models": ["demo.product"],
            "items": [
                {
                    "title": _("Basic Info"),
                    "link": reverse_lazy_lambda("admin:demo_product_changelist"),
                },
                {
                    "title": _("Advanced Settings"),
                    "link": reverse_lazy_lambda("admin:demo_product_changelist"),
                },
            ],
        },
    ],
}

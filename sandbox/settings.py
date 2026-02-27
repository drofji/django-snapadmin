from pathlib import Path
from django.utils.translation import gettext_lazy as _
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '').split(',') if os.getenv('ALLOWED_HOSTS') else []

INSTALLED_APPS = [
    # UI Customization
    'admin_interface',              # Optional
    'colorfield',                   # Optional

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
    'admin_auto_filters',           # REQUIRED
    'rangefilter',                  # REQUIRED
    'snapadmin',                    # REQUIRED

    # Other modules
    'extra_settings',               # Optional

    # Local Apps
    'demo',                          # Testing
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

ROOT_URLCONF = 'sandbox.urls'

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

WSGI_APPLICATION = 'sandbox.wsgi.application'

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

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [
    BASE_DIR / "static",
]
STATIC_ROOT = BASE_DIR / ".staticfiles"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


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
}

SPECTACULAR_SETTINGS = {
    # 1. Basic Metadata
    'TITLE': 'Your Project API',
    'DESCRIPTION': 'Detailed API documentation for my Django project.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,  # Hides the schema endpoint from the docs list

    # 2. Authentication / Security (The "Authorize" Button)
    # This defines the global security schemes for the Swagger UI
    'SECURITY': [
        {'TokenAuth': []},
    ],
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

    # 3. UI Customization
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,  # Keeps you logged in after page refresh
        'displayOperationId': True,  # Shows function names next to paths
        'filter': True,  # Adds a search bar to filter endpoints
    },

    # 4. Local Assets (Optional but recommended)
    # Use 'SIDECAR' if you have drf-spectacular-sidecar installed to avoid CDN issues
    # 'SWAGGER_UI_DIST': 'SIDECAR',
    # 'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    # 'REDOC_DIST': 'SIDECAR',

    # 5. Advanced Schema Features
    'ENUM_NAME_OVERRIDES': {
        # Custom names for choices/enums if they look messy in the docs
    },
}


# ------------------------------------------------------------------------------
# EXTRA SETTINGS
# https://pypi.org/project/django-extra-settings/
# ------------------------------------------------------------------------------

# the name of the installed app for registering the extra settings admin.
EXTRA_SETTINGS_ADMIN_APP = "demo"
# the name of the cache to use, if not found the "default" cache will be used.
EXTRA_SETTINGS_CACHE_NAME = "extra_settings"
# if True, settings names will be forced to honor the standard django settings format
EXTRA_SETTINGS_ENFORCE_UPPERCASE_SETTINGS = True
# if True, the template tag will fallback to django.conf.settings,
# very useful to retrieve conf settings such as DEBUG.
EXTRA_SETTINGS_FALLBACK_TO_CONF_SETTINGS = True
# the upload_to path value of settings of type 'file'
EXTRA_SETTINGS_FILE_UPLOAD_TO = "files"
# the upload_to path value of settings of type 'image'
EXTRA_SETTINGS_IMAGE_UPLOAD_TO = "images"
# if True, settings name prefix list filter will be shown in the admin changelist
EXTRA_SETTINGS_SHOW_NAME_PREFIX_LIST_FILTER = False
# if True, settings type list filter will be shown in the admin changelist
EXTRA_SETTINGS_SHOW_TYPE_LIST_FILTER = False
# the package name displayed in the admin
EXTRA_SETTINGS_VERBOSE_NAME = _("Settings")


EXTRA_SETTINGS_DEFAULTS = [
    # --- Basic Text Types ---
    {'name': 'SITE_NAME', 'type': 'string', 'value': 'My Awesome Project'},
    {'name': 'SUPPORT_EMAIL', 'type': 'email', 'value': 'support@example.com'},
    {'name': 'SITE_DESCRIPTION', 'type': 'text', 'value': 'A long description of the project...'},

    # --- Numbers and Logic ---
    {'name': 'MAX_LOGIN_ATTEMPTS', 'type': 'int', 'value': 5},
    {'name': 'TAX_RATE', 'type': 'float', 'value': 18.5},
    {'name': 'MAINTENANCE_MODE', 'type': 'bool', 'value': False},

    # --- Web and Visual ---
    {'name': 'EXTERNAL_API_URL', 'type': 'url', 'value': 'https://api.provider.com/v1/'},

    # --- Date and Time ---
    # Values should be strings in ISO format for the defaults
    {'name': 'CAMPAIGN_START', 'type': 'date', 'value': '2024-01-01'},
    {'name': 'LAUNCH_DATETIME', 'type': 'datetime', 'value': '2024-12-31 23:59:59'},

    # --- Complex Data ---
    # JSON type allows dictionaries and lists
    {'name': 'SOCIAL_LINKS', 'type': 'json', 'value': {
        'facebook': 'fb.com/page',
        'twitter': '@handle',
        'tags': ['django', 'python']
    }},

    # --- Files and Images ---
    # Value is the path relative to MEDIA_ROOT
    {'name': 'FAVICON', 'type': 'image', 'value': 'defaults/favicon.ico'},
    {'name': 'TERMS_PDF', 'type': 'file', 'value': 'docs/terms.pdf'},
]

# ------------------------------------------------------------------------------
# CELERY BEAT SCHEDULE (Dummy for Dashboard Verification)
# ------------------------------------------------------------------------------

CELERY_BEAT_SCHEDULE = {
    'sync-products-to-es': {
        'task': 'demo.tasks.sync_products',
        'schedule': 3600.0,
        'description': 'Synchronize products from DB to Elasticsearch every hour.'
    },
    'cleanup-expired-tokens': {
        'task': 'snapadmin.api.tasks.cleanup_tokens',
        'schedule': 86400.0,
        'description': 'Remove expired API tokens daily.'
    },
}
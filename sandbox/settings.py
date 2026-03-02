from pathlib import Path
from django.utils.translation import gettext_lazy as _
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dummy-key-for-tests')
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

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
    'admin_auto_filters',           # REQUIRED
    'rangefilter',                  # REQUIRED
    'snapadmin',                    # REQUIRED
    'graphene_django',              # REQUIRED

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
        'DIRS': [BASE_DIR / 'templates'],
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
STATICFILES_DIRS = []
STATIC_ROOT = BASE_DIR / ".staticfiles"

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_REDIRECT_URL = '/admin/'

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
# EXTRA SETTINGS
# ------------------------------------------------------------------------------
EXTRA_SETTINGS_ADMIN_APP = "demo"
EXTRA_SETTINGS_CACHE_NAME = "extra_settings"
EXTRA_SETTINGS_VERBOSE_NAME = _("Settings")

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
# UNFOLD CONFIGURATION (Comprehensive example)
# ------------------------------------------------------------------------------

UNFOLD = {
    "SITE_TITLE": "SnapAdmin Demo",
    "SITE_HEADER": "SnapAdmin Demo",
    "SITE_URL": "/admin/",
    "SITE_ICON": None,  # path to static icon
    "SITE_SYMBOL": "speed",  # icon from Material Symbols
    "SHOW_HISTORY": True, # show button to history of model
    "SHOW_VIEW_ON_SITE": True, # show button to view on site
    "THEME": "dark", # dark, light or auto
    "COLORS": {
        "primary": {
            "50": "250 245 255",
            "100": "243 232 255",
            "200": "233 213 255",
            "300": "216 180 254",
            "400": "192 132 252",
            "500": "168 85 247",
            "600": "147 51 234",
            "700": "126 34 206",
            "800": "107 33 168",
            "900": "88 28 135",
            "950": "59 7 100",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": _("Main"),
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "dashboard",
                        "link": "/admin/",
                    },
                ],
            },
            {
                "title": _("Business Logic"),
                "items": [
                    {
                        "title": _("Products"),
                        "icon": "inventory_2",
                        "link": "admin:demo_product_changelist",
                    },
                    {
                        "title": _("Orders"),
                        "icon": "shopping_cart",
                        "link": "admin:demo_order_changelist",
                    },
                    {
                        "title": _("Customers"),
                        "icon": "person",
                        "link": "admin:demo_customer_changelist",
                    },
                ],
            },
            {
                "title": _("Security"),
                "items": [
                    {
                        "title": _("API Tokens"),
                        "icon": "key",
                        "link": "admin:snapadmin_apitoken_changelist",
                    },
                    {
                        "title": _("Users"),
                        "icon": "people",
                        "link": "admin:auth_user_changelist",
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
                    "link": "admin:demo_product_changelist",
                },
                {
                    "title": _("Advanced Settings"),
                    "link": "admin:demo_product_changelist",
                },
            ],
        },
    ],
    "STYLES": [
        # lambda request: static("css/style.css"),
    ],
    "SCRIPTS": [
        # lambda request: static("js/script.js"),
    ],
}

"""
demo/core/settings_test.py

Lightweight settings override used exclusively by the pytest suite.

Inherits everything from the main settings but:
- Uses SQLite by default, and the *same* suite against a real PostgreSQL when
  ``SNAPADMIN_TEST_POSTGRES`` names a host (see the database block below)
- Disables Elasticsearch
- Silences Celery (tasks run eagerly, no broker needed)
- Turns off structlog colour output to keep CI logs clean
- Uses a fast password hasher to speed up User.create_superuser()
"""

import os

from demo.core.settings import *  # noqa: F401, F403

# ── Secret Key for tests ─────────────────────────────────────────────────────
SECRET_KEY = "test-secret-key-123"

# ── DEBUG: match what the tests actually run under ───────────────────────────
# demo/core/settings.py defaults DEBUG to True, and pytest-django then forces
# settings.DEBUG = False for the run — but only *after* Django is set up, so
# every admin registration built during app-ready was built under DEBUG=True
# while every test reads DEBUG=False. The two disagree exactly where Django
# picks an asset filename (``jquery.js`` vs ``jquery.min.js``), so the media on
# a startup registration never matched the media a test computed, and any test
# comparing the two passed only when an earlier test happened to have
# re-registered the model first. Pinning it here makes app-ready and the tests
# agree (#QA1b).
DEBUG = False

# ── Database: SQLite by default, PostgreSQL when one is offered ──────────────
# The everyday run stays on in-memory SQLite: no service to start, no container,
# and the whole suite in about forty seconds. That is also its limitation — the
# package advertises PostgreSQL support, and a backend difference (a
# vendor-specific query, a transaction behaviour, a column type) cannot fail a
# test that never talks to PostgreSQL.
#
# Setting SNAPADMIN_TEST_POSTGRES points the identical suite at a real server
# instead. CI runs both: the 6-way Python x Django matrix on SQLite, and one
# extra job on PostgreSQL. Nothing in the tests branches on the backend — the
# point is that the same assertions have to hold on either (#QA1f).
_POSTGRES_HOST = os.environ.get("SNAPADMIN_TEST_POSTGRES")

if _POSTGRES_HOST:
    _POSTGRES = {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": _POSTGRES_HOST,
        "PORT": os.environ.get("SNAPADMIN_TEST_POSTGRES_PORT", "5432"),
        "NAME": os.environ.get("SNAPADMIN_TEST_POSTGRES_DB", "snapadmin_test"),
        "USER": os.environ.get("SNAPADMIN_TEST_POSTGRES_USER", "snapadmin"),
        "PASSWORD": os.environ.get("SNAPADMIN_TEST_POSTGRES_PASSWORD", "snapadmin"),
    }
    DATABASES = {
        "default": dict(_POSTGRES),
        # Same alias as below, and the same MIRROR trick: a second connection to
        # the same test database, so a queryset routed here with .using() sees
        # rows written through "default".
        "replica": dict(_POSTGRES, TEST={"MIRROR": "default"}),
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        },
        # A read-replica alias for exercising SNAPADMIN_ANALYTICS_DB_ALIAS routing.
        # TEST.MIRROR makes it share the default test connection, so rows written to
        # ``default`` are visible when a queryset is routed here via ``.using()``.
        # Routing stays off unless a test sets SNAPADMIN_ANALYTICS_DB_ALIAS, so this
        # is inert for the rest of the suite.
        "replica": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
            "TEST": {"MIRROR": "default"},
        },
    }

# ── Field encryption: a fixed test keyset ────────────────────────────────────
# CustomerProfile.tax_id is encrypted, and an encrypted field with no keyset is
# a startup error by design. Pinned here rather than inherited so the suite is
# never at the mercy of SNAPADMIN_ENCRYPTION_KEYS in the developer's shell, and
# so a test that asserts on stored ciphertext gets the same key every run.
# Worthless by construction: it is in version control.
SNAPADMIN_ENCRYPTION = {
    "KEYS": [{"id": "test", "key": "c25hcGFkbWluLXRlc3Qta2V5LW5vdC1hLXNlY3JldCE="}],
}

# ── Elasticsearch: always disabled ───────────────────────────────────────────
ELASTICSEARCH_ENABLED = False

# ── Throttling: off — the suite fires hundreds of requests per minute ────────
# The DRF-level DEFAULT_THROTTLE_CLASSES/DEFAULT_THROTTLE_RATES pop below only
# matters for views that still rely on DRF's global throttle config.
# DynamicModelViewSet no longer does: SnapAnonRateThrottle/SnapUserRateThrottle
# read SNAPADMIN_THROTTLE_ANON/SNAPADMIN_THROTTLE_USER directly via get_rate(),
# bypassing DEFAULT_THROTTLE_RATES entirely — so throttling must also be
# disabled here, at the source those classes actually consult. A falsy value
# (None) makes DRF's SimpleRateThrottle treat the scope as unlimited.
REST_FRAMEWORK = {**REST_FRAMEWORK}  # noqa: F405
REST_FRAMEWORK.pop("DEFAULT_THROTTLE_CLASSES", None)
REST_FRAMEWORK.pop("DEFAULT_THROTTLE_RATES", None)
SNAPADMIN_THROTTLE_ANON = None
SNAPADMIN_THROTTLE_USER = None

# ── extra_settings: seed no managed rows in the test DB ──────────────────────
# The demo bridges a curated set of SNAPADMIN_* settings through
# django-extra-settings (demo/apps/shop/managed_settings.py). Seeding those rows here
# would let the first-request sync overwrite the test overrides above (e.g. the
# throttle=None pins) with the seeded package defaults. The suite doesn't
# exercise that bridge except in test_extra_settings_sync.py (which creates its
# own rows in isolation), so start empty.
EXTRA_SETTINGS_DEFAULTS = []

# ── Celery: run tasks synchronously, no broker ───────────────────────────────
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# ── Fast password hasher ──────────────────────────────────────────────────────
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ── Disable structlog colour noise in CI ─────────────────────────────────────
JSON_LOGS = False
LOG_LEVEL = "CRITICAL"

# Re-run logging config with the overridden level
from snapadmin.logging_config import configure_logging  # noqa: E402
configure_logging(log_level="CRITICAL", json_logs=False)

# ── Media / static (in-memory, no filesystem needed) ─────────────────────────
DEFAULT_FILE_STORAGE = "django.core.files.storage.InMemoryStorage"

# ── Background export files → throwaway temp dir ─────────────────────────────
import tempfile as _tempfile  # noqa: E402
SNAPADMIN_EXPORT_DIR = _tempfile.mkdtemp(prefix="snap-exports-test-")

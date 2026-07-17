"""
demo/core/settings_test.py

Lightweight settings override used exclusively by the pytest suite.

Inherits everything from the main settings but:
- Forces SQLite (never needs PostgreSQL)
- Disables Elasticsearch
- Silences Celery (tasks run eagerly, no broker needed)
- Turns off structlog colour output to keep CI logs clean
- Uses a fast password hasher to speed up User.create_superuser()
"""

from demo.core.settings import *  # noqa: F401, F403

# ── Secret Key for tests ─────────────────────────────────────────────────────
SECRET_KEY = "test-secret-key-123"

# ── Database: always SQLite for tests ────────────────────────────────────────
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

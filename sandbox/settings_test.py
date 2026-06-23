"""
sandbox/settings_test.py

Lightweight settings override used exclusively by the pytest suite.

Inherits everything from the main settings but:
- Forces SQLite (never needs PostgreSQL)
- Disables Elasticsearch
- Silences Celery (tasks run eagerly, no broker needed)
- Turns off structlog colour output to keep CI logs clean
- Uses a fast password hasher to speed up User.create_superuser()
"""

from sandbox.settings import *  # noqa: F401, F403

# ── Secret Key for tests ─────────────────────────────────────────────────────
SECRET_KEY = "test-secret-key-123"

# ── Database: always SQLite for tests ────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ── Elasticsearch: always disabled ───────────────────────────────────────────
ELASTICSEARCH_ENABLED = False

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

"""
Version & feature-flags collector for ``snapadmin_info``.

Reports the SnapAdmin / Django / Python versions and the resolved ``SNAPADMIN_*_ENABLED``
feature toggles. Each flag's fallback mirrors how the package itself reads that setting, so the
report shows the *effective* value a running project would see, not a guess.
"""

from __future__ import annotations

import platform

import django
from django.conf import settings

from snapadmin import __version__
from snapadmin.diagnostics.registry import register

# (label, setting name, default). ``default=None`` means "follow DEBUG" (graphiql only).
_FEATURE_FLAGS: tuple[tuple[str, str, bool | None], ...] = (
    ("rest_api", "SNAPADMIN_REST_API_ENABLED", True),
    ("swagger", "SNAPADMIN_SWAGGER_ENABLED", True),
    ("graphql", "SNAPADMIN_GRAPHQL_ENABLED", True),
    ("graphiql", "SNAPADMIN_GRAPHIQL_ENABLED", None),
    ("user_api", "SNAPADMIN_USER_API_ENABLED", False),
    ("reindex_api", "SNAPADMIN_REINDEX_API_ENABLED", False),
    ("audit_log", "SNAPADMIN_AUDIT_LOG_ENABLED", True),
    ("export", "SNAPADMIN_EXPORT_ENABLED", True),
    ("error_monitor", "SNAPADMIN_ERROR_MONITOR_ENABLED", True),
    ("backup", "SNAPADMIN_BACKUP_ENABLED", False),
    ("elasticsearch", "ELASTICSEARCH_ENABLED", False),
)

#: Pre-release markers that mean the installed version is not a stable release.
_PRERELEASE_MARKERS = ("a", "b", "rc", "dev")


def _flag(setting: str, default: bool | None) -> bool:
    if default is None:
        default = bool(getattr(settings, "DEBUG", False))
    return bool(getattr(settings, setting, default))


@register("version", title="Version & Status", icon="📦", order=10)
def collect(*, verbose: bool) -> dict:
    """Collect the version and feature-flag section."""
    prerelease = any(marker in __version__ for marker in _PRERELEASE_MARKERS)
    return {
        "version": __version__,
        "status": "pre-release" if prerelease else "stable",
        "django": django.get_version(),
        "python": platform.python_version(),
        "features": {label: _flag(setting, default) for label, setting, default in _FEATURE_FLAGS},
    }

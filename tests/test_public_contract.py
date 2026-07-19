"""
Backward-compatibility contract for the public SnapAdmin API surface.

These tests pin the names, defaults and signatures that PyPI users rely on.
If one of them fails, you are about to ship a breaking change: either restore
compatibility or add a deprecation path, and update this contract deliberately
in the same commit.
"""

import inspect

import pytest
from django.urls import reverse
from django.utils.module_loading import import_string


# ─────────────────────────────────────────────────────────────────────────────
# Import surface: every public name must stay importable from its module
# ─────────────────────────────────────────────────────────────────────────────

PUBLIC_IMPORTS = [
    # Core models & managers
    "snapadmin.models.SnapModel",
    "snapadmin.models.APIToken",
    "snapadmin.models.ErrorEvent",
    "snapadmin.models.EsManager",
    "snapadmin.models.EsQuerySet",
    "snapadmin.models.EsStorageMode",
    "snapadmin.models.SnapPurgeError",
    "snapadmin.models.SnapSaveMixin",
    "snapadmin.models.hash_token_key",
    # Fields
    "snapadmin.fields.SnapField",
    "snapadmin.fields.SnapCharField",
    "snapadmin.fields.SnapTextField",
    "snapadmin.fields.SnapEmailField",
    "snapadmin.fields.SnapIntegerField",
    "snapadmin.fields.SnapDecimalField",
    "snapadmin.fields.SnapDateTimeField",
    "snapadmin.fields.SnapBooleanField",
    "snapadmin.fields.SnapJSONField",
    "snapadmin.fields.SnapFileField",
    "snapadmin.fields.SnapImageField",
    "snapadmin.fields.SnapForeignKey",
    "snapadmin.fields.SnapOneToOneField",
    "snapadmin.fields.SnapManyToManyField",
    "snapadmin.fields.SnapRichTextField",
    "snapadmin.fields.SnapPhoneField",
    "snapadmin.fields.SnapColorField",
    "snapadmin.fields.SnapFunctionField",
    "snapadmin.fields.SnapStatusBadgeField",
    "snapadmin.fields.SnapStatusBadgeFieldChoice",
    # Validators
    "snapadmin.validators.SnapPhoneValidator",
    "snapadmin.validators.SnapColorValidator",
    "snapadmin.validators.SnapFileValidator",
    # Admin helpers
    "snapadmin.admin.APITokenAdmin",
    "snapadmin.admin.ErrorEventAdmin",
    "snapadmin.admin.SnapTabularInline",
    "snapadmin.admin.SnapStackedInline",
    # API layer
    "snapadmin.api.authentication.APITokenAuthentication",
    "snapadmin.api.views.DynamicModelViewSet",
    "snapadmin.api.views.APITokenViewSet",
    "snapadmin.api.views.ModelSchemaView",
    "snapadmin.api.health.HealthCheckView",
    # Views / infra
    "snapadmin.views.DashboardView",
    "snapadmin.logging_config.configure_logging",
    "snapadmin.logging_config.get_logger",
    # Error monitoring (v0.1.0a5)
    "snapadmin.middleware.SnapErrorMonitorMiddleware",
    "snapadmin.monitoring.record_error",
    "snapadmin.monitoring.maybe_send_spike_alert",
    "snapadmin.monitoring.send_error_digest",
    "snapadmin.monitoring.purge_expired_events",
    "snapadmin.monitoring.get_config",
    # 3-2-1 backups (v0.1.0a5)
    "snapadmin.backup.run_backup",
    "snapadmin.backup.run_due_backups",
    "snapadmin.backup.due_destinations",
    "snapadmin.backup.create_db_dump",
    "snapadmin.backup.get_backup_config",
    "snapadmin.backup.BackupError",
    # Integrator pass (v0.1.0a6)
    "snapadmin.api.authentication.get_api_authentication_classes",
    "snapadmin.api.authentication.SnapAPIAuthMixin",
    "snapadmin.etl.upsert_from_source",
    "snapadmin.etl.stale_sync",
    "snapadmin.etl.StaleSyncAbort",
]


@pytest.mark.parametrize("dotted_path", PUBLIC_IMPORTS)
def test_public_name_importable(dotted_path):
    assert import_string(dotted_path) is not None


# ─────────────────────────────────────────────────────────────────────────────
# SnapModel class-attribute defaults: users configure models by overriding
# these — renaming one or changing a default silently changes behaviour
# ─────────────────────────────────────────────────────────────────────────────

def test_snapmodel_attribute_defaults():
    from snapadmin.models import EsStorageMode, SnapModel

    expected = {
        "admin_enabled": True,
        "js_admin_files": [],
        "css_admin_files": [],
        "snap_inlines": [],
        "admin_sections": [],
        "admin_tabs": [],
        "compressed_fields": False,
        "warn_unsaved_form": True,
        "list_filter_submit": True,
        "es_index_enabled": False,
        "es_storage_mode": EsStorageMode.DB_ONLY,
        "es_index_name": None,
        "es_mapping": None,
        "es_index_settings": None,
        "es_auto_mapping": False,
        "es_query_routing": True,
        "api_exclude_fields": [],
        "api_write_fields": None,
        "data_retention_days": None,
        "data_retention_field": "created_at",
        "offline_mode": False,
        "offline_cache_limit": 100,
        "list_per_page": 100,
        "list_max_show_all": 200,
        "show_full_result_count": True,
    }
    for name, default in expected.items():
        assert getattr(SnapModel, name) == default, f"SnapModel.{name} default changed"


def test_esstoragemode_members():
    from snapadmin.models import EsStorageMode

    assert EsStorageMode.DB_ONLY.value == "db_only"
    assert EsStorageMode.DUAL.value == "dual"
    assert EsStorageMode.ES_ONLY.value == "es_only"


# ─────────────────────────────────────────────────────────────────────────────
# Method signatures users call directly
# ─────────────────────────────────────────────────────────────────────────────

def _params(func) -> list[str]:
    return list(inspect.signature(func).parameters)


def test_search_method_signatures():
    from snapadmin.models import SnapModel

    assert _params(SnapModel.es_search) == ["query_string", "limit"]
    assert _params(SnapModel.snap_search) == ["query_string", "limit"]
    assert _params(SnapModel.es_filter) == ["query_string", "limit", "terms"]
    assert _params(SnapModel.es_aggregate) == ["fields", "size", "query_string", "terms"]
    assert _params(SnapModel.es_scan) == ["query_string", "page_size", "terms"]
    assert _params(SnapModel.es_reindex_all) == ["chunk_size"]
    assert _params(SnapModel.purge_expired) == ["now", "dry_run"]


def test_apitoken_signatures():
    from snapadmin.models import APIToken

    assert _params(APIToken.create_for_user) == [
        "user", "token_name", "allowed_models", "expires_in_days",
    ]
    assert _params(APIToken.can_access_model) == ["self", "app_label", "model_name"]


def test_monitoring_signatures():
    from snapadmin.models import ErrorEvent
    from snapadmin.monitoring import record_error, send_error_digest

    assert _params(record_error) == ["request", "exception", "status_code"]
    assert _params(send_error_digest) == ["hours"]
    assert _params(ErrorEvent.record) == [
        "exception_class", "message", "path", "method", "status_code", "traceback_text",
    ]


def test_apitoken_user_fk_is_swappable():
    """APIToken must work with a custom AUTH_USER_MODEL — never hard-code auth.User."""
    from django.conf import settings as django_settings
    from snapadmin.models import APIToken

    field = APIToken._meta.get_field("user")
    assert field.deconstruct()[3]["to"] == django_settings.AUTH_USER_MODEL.lower()


def test_no_hardcoded_user_imports_in_package():
    """Custom user models break on `from django.contrib.auth.models import User`."""
    from pathlib import Path
    import snapadmin

    offenders = [
        str(path)
        for path in Path(snapadmin.__file__).parent.rglob("*.py")
        if "from django.contrib.auth.models import User" in path.read_text()
    ]
    assert offenders == []


def test_etl_and_auth_signatures():
    from snapadmin.etl import upsert_from_source
    from snapadmin.api.authentication import get_api_authentication_classes

    assert _params(upsert_from_source) == [
        "model", "rows", "unique_fields", "update_fields", "batch_size", "reindex",
    ]
    assert _params(get_api_authentication_classes) == []

    from snapadmin.etl import stale_sync

    assert _params(stale_sync) == [
        "model", "seen_keys", "key_field", "max_fraction", "queryset", "dry_run",
    ]


def test_charfield_required_null_parity():
    """required=False → null=True for CharField, matching the other field types."""
    from snapadmin.fields import SnapCharField, SnapPhoneField, SnapColorField

    assert SnapCharField(max_length=10).null is True
    assert SnapCharField(max_length=10, required=True).null is False
    assert SnapPhoneField().null is True
    assert SnapColorField().null is True


def test_snap_field_deconstruct_round_trips_required():
    """A required Snap field must reconstruct to the same null/blank (stable migrations)."""
    from snapadmin.fields import SnapCharField, SnapIntegerField

    for field in (SnapCharField(max_length=10, required=True), SnapIntegerField(required=True)):
        name, path, args, kwargs = field.deconstruct()
        rebuilt = type(field)(*args, **kwargs)
        assert rebuilt.null is False and rebuilt.blank is False
    optional = SnapCharField(max_length=10)
    _, _, args, kwargs = optional.deconstruct()
    rebuilt = SnapCharField(*args, **kwargs)
    assert rebuilt.null is True and rebuilt.blank is True


def test_backup_signatures_and_defaults():
    from snapadmin.backup import get_backup_config, run_backup

    assert _params(run_backup) == ["destinations", "config"]
    config = get_backup_config()
    assert config.enabled is False          # backups are strictly opt-in
    assert config.keep == 7
    assert (config.local_every_hours, config.network_every_hours) == (24, 24)
    assert config.remote_every_hours == 168  # offsite weekly


# ─────────────────────────────────────────────────────────────────────────────
# Settings knobs: documented names with their documented defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_error_monitoring_setting_defaults():
    from snapadmin.monitoring import get_config

    config = get_config()
    assert (config.enabled, config.alert_enabled, config.digest_enabled) == (True, True, True)
    assert config.alert_threshold == 20
    assert config.alert_window_minutes == 15
    assert config.digest_max_groups == 20
    assert config.retention_days == 30


def test_feature_toggle_setting_names_still_read():
    # snapadmin/urls.py must keep honouring these names — they are documented
    # kill-switches for each API surface.
    import snapadmin.urls as snap_urls

    source = inspect.getsource(snap_urls)
    for name in (
        "SNAPADMIN_REST_API_ENABLED",
        "SNAPADMIN_SWAGGER_ENABLED",
        "SNAPADMIN_GRAPHQL_ENABLED",
        "SNAPADMIN_GRAPHIQL_ENABLED",
    ):
        assert name in source, f"{name} is no longer honoured by snapadmin.urls"


# ─────────────────────────────────────────────────────────────────────────────
# URL names: reversing these must keep working for API consumers/templates
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,args",
    [
        ("model-list", ["demo", "Product"]),
        ("model-detail", ["demo", "Product", 1]),
        ("model-schema", []),
        ("api-health", []),
        ("api-token-list", []),
        ("api-schema", []),
        ("swagger-ui", []),
        ("redoc", []),
        ("graphql", []),
        ("offline-models", []),
        ("dashboard", []),
    ],
)
def test_url_names_reversible(name, args):
    assert reverse(name, args=args)

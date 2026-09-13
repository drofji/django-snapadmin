"""
tests/test_public_surface_snapshot.py

A generated, exhaustive backstop for test_public_contract.py's hand-curated
``PUBLIC_IMPORTS``: every function and class actually *defined* (not merely
imported) at the top level of every top-level ``snapadmin/*.py`` module, frozen
below as ``SNAPSHOT``.

``PUBLIC_IMPORTS`` pins the names SECURITY.md promises PyPI users; that list is
maintained by hand and only grows when someone remembers to add an entry. This
file works the other direction: it is generated from the actual source (``ast``,
not memory) and catches what a hand-maintained list cannot — a name quietly
removed, renamed or added without anyone updating either list. There is no way
to delete or rename a public function/class here without this test failing,
whether the change was deliberate or an accidental side effect of a refactor —
including one made by an AI assistant, which is exactly the failure mode this
file exists to catch (see the "Stable public surface" rule in
``.claude/rules.md``).

A failure here is not automatically wrong. Adding a genuinely new public helper
is normal — allowed in any ``1.x`` minor per SECURITY.md's semver table — and
just needs ``SNAPSHOT`` updated in the same commit, deliberately: add a
docstring to the new name, and add it to ``PUBLIC_IMPORTS`` in
``test_public_contract.py`` too if it is meant to be part of the stable
contract (leave it out, and give it a leading underscore instead, if it was
only ever meant to be a private implementation detail). A *removal* is the case
to slow down for: per SECURITY.md, removing or renaming a public name is a
major-version-only change — confirm that is genuinely what is happening, and
that a deprecation path was considered, before updating ``SNAPSHOT`` to match.

Out of scope, deliberately: names inside subpackages (``snapadmin/api/``,
``snapadmin/diagnostics/``, ``snapadmin/es/``, ...). Each already has its own
dedicated test suite, and the module-level facades built on top of them
(``snapadmin.es``, ``snapadmin.jobs``, ...) are covered by the identity-reexport
tests already in ``test_public_contract.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import snapadmin

PACKAGE_ROOT = Path(snapadmin.__file__).resolve().parent

#: Top-level snapadmin/*.py modules deliberately excluded (mirrors
#: test_ai_entry_points.py's _UNMAPPED_TOP_LEVEL_MODULES): apps.py is
#: referenced by string in INSTALLED_APPS, never imported directly, and
#: defines nothing meant to be used on its own.
_EXCLUDED_MODULES = {"apps"}


def _defined_public_names(source: str) -> list[str]:
    """Every function/class name defined at module top level, including inside
    a module-level ``if``/``try`` block (the pattern used for optional-import
    fallbacks), excluding anything leading-underscore-prefixed."""
    tree = ast.parse(source)
    names: list[str] = []

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.append(node.name)
            elif isinstance(node, ast.If):
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.Try):
                visit(node.body)
                for handler in node.handlers:
                    visit(handler.body)
                visit(node.orelse)
                visit(node.finalbody)

    visit(tree.body)
    return sorted(set(names))


def _actual_top_level_modules() -> set[str]:
    return {
        f"snapadmin.{path.stem}"
        for path in PACKAGE_ROOT.glob("*.py")
        if path.stem != "__init__" and path.stem not in _EXCLUDED_MODULES
    }


# Generated 2026-09-04 against django-snapadmin 0.1.0b8 (ast scan of every
# top-level snapadmin/*.py module) — see the module docstring above for how to
# update this deliberately when the surface legitimately changes.
SNAPSHOT: dict[str, list[str]] = {
    "snapadmin.admin": [
        "APITokenAdmin",
        "ErrorEventAdmin",
        "SnapStackedInline",
        "SnapTabularInline",
        "SnapadminAuditLogAdmin",
        "display",
    ],
    "snapadmin.admin_gen": [
        "AdminGenMixin",
        "unfold_display",
    ],
    "snapadmin.alerts": [
        "Alert",
        "AlertChannel",
        "AlertDeliveryError",
        "DeliveryResult",
        "DiscordChannel",
        "EmailChannel",
        "JsonWebhookChannel",
        "SlackChannel",
        "TeamsChannel",
        "TelegramChannel",
        "WebhookChannel",
        "arm_cooldown",
        "build_channels",
        "build_webhook_channel",
        "dispatch",
        "get_webhook_channels",
        "mask_webhook_url",
        "post_json",
        "release_cooldown",
        "send_email_alert",
    ],
    "snapadmin.audit": [
        "audit_enabled",
        "change_entry",
        "client_ip",
        "diff_rows",
        "field_is_encrypted",
        "display_value",
        "format_value",
        "record_audit",
        "user_agent",
        "visible_audit_queryset",
    ],
    "snapadmin.auth_admin": [
        "apply_unfold_auth_admin",
    ],
    "snapadmin.backup": [
        "BackupConfig",
        "BackupError",
        "build_backup_bundle",
        "check_env_requires_encryption",
        "create_db_dump",
        "create_encrypted_db_dump",
        "create_env_bundle",
        "create_media_bundle",
        "due_destinations",
        "fetch_local",
        "fetch_network",
        "fetch_remote_ftp",
        "fetch_remote_sftp",
        "fetch_s3",
        "get_backup_config",
        "list_local",
        "list_network",
        "list_remote_ftp",
        "list_remote_sftp",
        "list_s3",
        "run_backup",
        "run_due_backups",
        "s3_ambient_credentials_likely",
        "sha256_file",
        "store_local",
        "store_network",
        "store_remote_ftp",
        "store_remote_sftp",
        "store_s3",
        "write_manifest",
    ],
    "snapadmin.celery_compat": [
        "UnavailableTask",
        "shared_task",
    ],
    "snapadmin.checks": [
        "check_analytics_db_alias",
        "check_api_extras_installed",
        "check_api_read_only",
        "check_api_write_fields",
        "check_backup_age_recipients",
        "check_backup_env_requires_encryption",
        "check_backup_s3_configuration",
        "check_backup_schedule_cadence",
        "check_empty_admin_forms",
        "check_encrypted_field_usage",
        "check_encrypted_fields_not_indexed",
        "check_encryption_key_file",
        "check_encryption_keys",
        "check_encryption_required",
        "check_extra_settings_admin_app",
        "check_fetch_by_max_values",
        "check_masked_fields",
        "check_masking_rules",
        "check_nested_apps",
        "check_nesting_active_site",
        "check_retention_purge_scheduled",
        "check_sharding_config",
        "check_sharding_ranges",
        "check_sharding_strategy",
        "check_snap_action_read_only_conflict",
        "check_snapadmin_profile",
        "check_snapadmin_profile_contradiction",
        "check_sso_providers",
        "check_subject_paths",
        "check_tenant_scoping",
        "check_unfold_theme",
        "register_checks",
    ],
    "snapadmin.conf": [
        "get_setting",
    ],
    "snapadmin.crypto": [
        "AgeError",
        "decrypt_stream",
        "encrypt_stream",
        "fingerprint",
        "generate_keypair",
        "looks_like_recipient",
        "resolve_backend",
    ],
    "snapadmin.db": [
        "analytics_db_alias",
        "route_read",
    ],
    "snapadmin.etl": [
        "StaleSyncAbort",
        "stale_sync",
        "upsert_from_source",
    ],
    "snapadmin.exporting": [
        "ExportRowSource",
        "export_chunk_size",
        "export_dir",
        "export_enabled",
        "export_file_name",
        "export_retention_days",
        "get_export_source",
        "get_export_storage",
        "output_path",
        "purge_expired_export_jobs",
        "run_export_job",
        "xlsx_available",
    ],
    "snapadmin.extra_settings_admin": [
        "apply_unfold_styling",
    ],
    "snapadmin.fields": [
        "DjangoFieldAttributeEnum",
        "SanitizedHtmlOnSaveMixin",
        "SnapBigIntegerField",
        "SnapBooleanField",
        "SnapBlindIndexField",
        "SnapCharField",
        "SnapColorField",
        "SnapDateField",
        "SnapDateTimeField",
        "SnapDecimalField",
        "SnapDurationField",
        "SnapEmailField",
        "SnapEncryptedCharField",
        "SnapEncryptedDateField",
        "SnapEncryptedDateTimeField",
        "SnapEncryptedDecimalField",
        "SnapEncryptedEmailField",
        "SnapEncryptedField",
        "SnapEncryptedIntegerField",
        "SnapEncryptedJSONField",
        "SnapEncryptedTextField",
        "SnapField",
        "SnapFieldAttributeEnum",
        "SnapFileField",
        "SnapFloatField",
        "SnapForeignKey",
        "SnapFunctionField",
        "SnapGenericIPAddressField",
        "SnapImageField",
        "SnapIntegerField",
        "SnapJSONField",
        "SnapManyToManyField",
        "SnapNotDatabaseField",
        "SnapOneToOneField",
        "SnapPhoneField",
        "SnapPositiveBigIntegerField",
        "SnapPositiveIntegerField",
        "SnapPositiveSmallIntegerField",
        "SnapRichTextField",
        "SnapSlugField",
        "SnapSmallIntegerField",
        "SnapStatusBadgeField",
        "SnapStatusBadgeFieldChoice",
        "SnapTextField",
        "SnapTimeField",
        "SnapURLField",
        "SnapUUIDField",
        "snap_field",
    ],
    "snapadmin.health": [
        "HealthAlertConfig",
        "failing_probes",
        "get_health_config",
        "probe_lines",
        "run_health_probes",
        "send_health_alert",
    ],
    "snapadmin.importing": [
        "SnapImportError",
        "check_write_surface",
        "guess_import_format",
        "import_chunk_size",
        "iter_input_rows",
        "read_header",
        "resolve_column_map",
        "resolve_natural_key",
        "run_import_job",
        "start_import",
    ],
    "snapadmin.jobs": [
        "SnapExportJob",
        "SnapImportJob",
        "SnapJobBase",
        "SnapReindexJob",
    ],
    "snapadmin.licensing": [
        "LicenseInfo",
        "PackageStatus",
        "Tier",
        "audit_uncurated",
        "classify",
        "commercial_verdict",
        "curated_staleness",
        "is_compatible_with",
        "scan_curated",
    ],
    "snapadmin.limits": [
        "Reservation",
        "cooldown",
        "in_cooldown",
        "reserve",
    ],
    "snapadmin.logging_config": [
        "ColourConsoleRenderer",
        "configure_logging",
        "get_logger",
    ],
    "snapadmin.manage_cli": [
        "find_manage_py",
        "forward",
        "info_main",
        "license_check_main",
    ],
    "snapadmin.masking": [
        "apply_masking_rule",
        "get_masked_fields",
        "get_masking_rules",
        "mask_changes",
        "mask_field",
        "mask_value",
        "user_can_access_field",
        "user_can_view_pii",
    ],
    "snapadmin.middleware": [
        "SnapErrorMonitorMiddleware",
    ],
    "snapadmin.models": [
        "APIToken",
        "AdminFieldSets",
        "DjangoAdminClassAttributeEnum",
        "ErrorEvent",
        "PIIMaskingAdminMixin",
        "SnapModel",
        "SnapModelAttributeEnum",
        "SnapPurgeError",
        "SnapSaveMixin",
        "SnapadminAuditLog",
        "formatted_id",
        "hash_token_key",
        "reindexable_snapmodels",
        "run_reindex",
        "snap_model",
        "snap_property",
        "validate_allowed_models",
        "validate_allowed_scopes",
    ],
    "snapadmin.monitoring": [
        "ErrorMonitorConfig",
        "get_config",
        "group_events",
        "group_lines",
        "maybe_send_spike_alert",
        "purge_expired_events",
        "record_error",
        "send_error_digest",
    ],
    "snapadmin.nesting": [
        "apply_nested_apps",
        "get_app_label_overrides",
        "get_hidden_apps",
        "get_nested_apps",
        "nesting_configured",
    ],
    "snapadmin.pagination": [
        "EstimatedCountPaginator",
        "estimated_count_enabled",
        "pg_estimated_count",
    ],
    "snapadmin.registry": [
        "get_model_meta",
        "is_registered",
        "meta_for",
        "register",
    ],
    "snapadmin.reindexing": [
        "run_reindex_job",
        "start_reindex",
        "verify_index",
    ],
    "snapadmin.restore": [
        "ResolvedSource",
        "RestoreError",
        "check_version_compatibility",
        "fetch_parts",
        "identity_required_message",
        "last_restore_run",
        "list_bundles",
        "parse_source",
        "perform_restore",
        "plan_restore",
        "record_restore_run",
        "resolve_source",
        "restore_db",
        "restore_env",
        "restore_media",
        "select_parts",
        "verify_checksums",
    ],
    "snapadmin.sanitize": [
        "sanitize_html",
    ],
    "snapadmin.snapshot": [
        "SnapshotError",
        "latest_snapshot_id",
        "list_snapshots",
        "load_snapshot_manifest",
        "snapshot_dir",
        "snapshot_keep",
        "take_snapshot",
    ],
    "snapadmin.sso": [
        "get_sso_providers",
        "sso_enabled",
        "sso_providers",
    ],
    "snapadmin.tasks": [
        "ReindexError",
        "purge_expired_data",
        "purge_expired_tokens",
        "run_db_backups",
        "run_es_reindex",
        "run_export",
        "send_error_digest",
        "send_health_alert",
    ],
    "snapadmin.tenancy": [
        "SnapTenantMiddleware",
        "SnapTenantRebindMixin",
        "bind_tenant",
        "get_current_tenant",
        "is_tenant_scoped",
        "resolve_tenant_for_request",
        "resolve_tenant_for_user",
        "scope_queryset",
        "tenant_context_bound",
        "tenant_field",
        "tenant_field_name",
        "unbind_tenant",
        "use_all_tenants",
        "use_tenant",
    ],
    "snapadmin.theme_i18n": [],
    "snapadmin.urls": [],
    "snapadmin.validators": [
        "FileEncodingEnum",
        "FileExtensionEnum",
        "SnapColorValidator",
        "SnapFileValidator",
        "SnapPhoneValidator",
    ],
    "snapadmin.views": [
        "DashboardView",
        "StaffRequiredMixin",
    ],
    "snapadmin.widgets": [
        "SmartModelSelectorWidget",
    ],
}


def test_snapshot_covers_every_top_level_module():
    """A new snapadmin/*.py file must be added to SNAPSHOT, not silently skipped,
    and a deleted one must be dropped from it (#AUDIT1b-style drift check, applied
    to this file instead of the docstring's module map)."""
    actual = _actual_top_level_modules()
    missing = sorted(actual - set(SNAPSHOT))
    assert not missing, f"new top-level module(s) not yet in SNAPSHOT: {missing}"

    extra = sorted(set(SNAPSHOT) - actual)
    assert not extra, f"SNAPSHOT references module(s) that no longer exist: {extra}"


@pytest.mark.parametrize("module_path", sorted(SNAPSHOT))
def test_defined_public_names_match_snapshot(module_path):
    stem = module_path.rsplit(".", 1)[1]
    source = (PACKAGE_ROOT / f"{stem}.py").read_text(encoding="utf-8")
    actual = _defined_public_names(source)
    expected = SNAPSHOT[module_path]

    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    assert not added and not removed, (
        f"{module_path}'s defined public names drifted from the pinned snapshot — "
        f"added: {added or 'none'}, removed: {removed or 'none'}. "
        "If this is a deliberate addition: update SNAPSHOT in this file, and add a "
        "docstring plus a test_public_contract.py PUBLIC_IMPORTS entry if the name "
        "is meant to be part of the stable public contract (or a leading underscore "
        "if it was only ever an implementation detail). If a name went missing: "
        "per SECURITY.md's semver policy, removing or renaming a public name is a "
        "major-version-only change — confirm that is genuinely intended, with a "
        "deprecation path already served, before updating SNAPSHOT to match."
    )

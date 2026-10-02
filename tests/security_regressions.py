"""
tests/security_regressions.py

The security regression suite: every security fix this package ever shipped,
and the tests that keep it fixed.

A fix without a test is a fix that can quietly come back — a refactor restores
the old code path, a "cleanup" drops a guard nobody remembered the reason for.
This registry names, for each one, the tests that fail if it returns. It is data,
not tests: ``tests/conftest.py`` marks every test it names ``security_regression``,
so the whole suite runs as one command::

    python -m pytest -m security_regression

and ``tests/test_security_regressions.py`` keeps the registry honest:

* every entry in a ``Security`` section of ``docs/releases/*.txt`` has exactly
  one entry here — a security fix published without a named test fails the build;
* every test named here exists, and none is skipped or marked ``xfail``;
* the registry never shrinks below its recorded size.

**Adding a security fix:** write its regression test, describe the fix under
``Security`` in ``docs/releases/Unreleased.txt``, and add an entry here whose
``title`` is the start of that entry's first line. A fix published outside a
``Security`` section (a fail-open found and fixed before it shipped, or listed
under ``Fixed``) gets ``in_release_notes=False`` and says where it came from.

Test ids are pytest node ids relative to the repository root: a module, a
class (every test in it) or a single test (every parametrisation of it).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityFix:
    """One shipped security fix and the tests that fail if it regresses."""

    #: The start of the fix's first line in its release note, markup included
    #: as written there; for a fix outside the notes, a description of it.
    title: str
    #: Node ids of the tests that pin the fix.
    tests: tuple[str, ...]
    #: ``False`` for a fix that has no entry in a ``Security`` section.
    in_release_notes: bool = True
    #: Where a fix outside the release notes' Security sections came from.
    origin: str = ""


REGISTRY: tuple[SecurityFix, ...] = (
    # ── 0.1.0b1 ─────────────────────────────────────────────────────────────
    SecurityFix(
        "The system dashboard is now staff-gated by default",
        (
            "tests/test_dashboard.py::test_dashboard_redirects_anonymous_to_login",
            "tests/test_dashboard.py::test_dashboard_forbids_non_staff",
        ),
    ),
    SecurityFix(
        "Wysiwyg field values are now sanitized before they are rendered in the admin changelist",
        (
            "tests/test_wysiwyg_sanitize.py::TestSanitizeHtml",
            "tests/test_wysiwyg_sanitize.py::TestWysiwygChangelistRender",
        ),
    ),
    # ── 0.1.0b2 ─────────────────────────────────────────────────────────────
    SecurityFix(
        "``DynamicModelViewSet`` (the generic ``/api/models/<app>/<model>/`` endpoint)",
        ("tests/test_model_api.py::TestNonSnapModelRejected",),
    ),
    # ── 0.1.0b3 ─────────────────────────────────────────────────────────────
    SecurityFix(
        "GraphQL relation permission + PII masking parity with REST",
        (
            "tests/test_graphql_security.py::TestGraphQLRelationPermission",
            "tests/test_graphql_security.py::TestGraphQLMasking",
        ),
    ),
    SecurityFix(
        "New ``api_write_fields`` mass-assignment guard",
        ("tests/test_model_api.py::TestApiWriteFieldsAllowlist",),
    ),
    SecurityFix(
        "``mask_value()`` type handling",
        (
            "tests/test_pii_masking.py::TestMaskValue::test_short_value_fully_masked",
            "tests/test_pii_masking.py::TestMaskValue::test_bool_returns_sentinel",
            "tests/test_pii_masking.py::TestMaskValue::test_list_masks_each_element",
            "tests/test_pii_masking.py::TestMaskValue::test_dict_masks_values_not_keys",
        ),
    ),
    SecurityFix(
        "``SmartModelSelectorWidget`` fail-open fix",
        ("tests/test_widget_security.py",),
    ),
    SecurityFix(
        "SSO provider open-redirect fix",
        (
            "tests/test_sso.py::TestGetSsoProviders::test_protocol_relative_url_dropped",
            "tests/test_sso.py::TestSsoProviderEndpoint"
            "::test_endpoint_never_resolves_protocol_relative_url_to_external_origin",
            "tests/test_checks.py::TestSsoProviders::test_protocol_relative_url_warns",
        ),
    ),
    SecurityFix(
        "Export filters restricted to the target model's own fields",
        (
            "tests/test_export.py::TestValidateExportFilters::test_relation_traversal_rejected",
            "tests/test_export.py::TestExportFilterValidationApi::test_relation_traversal_rejected",
        ),
    ),
    SecurityFix(
        "Database backup hardening",
        ("tests/test_backup.py::TestDestinations::test_store_sftp_unknown_host_rejected",),
    ),
    SecurityFix(
        "``Snap*Field`` upload-validator config loss",
        ("tests/test_fields.py::TestFileFieldValidatorConfig",),
    ),
    SecurityFix(
        "Assorted cosmetic-vs-security and deployment-topology fixes",
        (
            "tests/test_checks.py::TestNestingActiveSite::test_other_site_with_registered_models_warns",
            "tests/test_error_monitoring.py::TestErrorDigest"
            "::test_digest_total_and_body_stay_consistent_when_purge_overlaps_window",
        ),
    ),
    SecurityFix(
        "PII masking closed on export, audit trail and API filter/ordering/search",
        (
            "tests/test_export.py::TestExportMasksPii",
            "tests/test_audit_trail.py::TestAuditMasking",
            "tests/test_model_api.py::TestMaskedFieldsExcludedFromApiQuerying",
        ),
    ),
    # ── 0.1.0b7 ─────────────────────────────────────────────────────────────
    SecurityFix(
        "The audit-log change form no longer renders the unmasked diff",
        ("tests/test_audit_trail.py::TestAuditMasking::test_change_form_renders_the_masked_diff",),
    ),
    SecurityFix(
        "Wysiwyg HTML sanitization now fails closed if ``nh3`` cannot be imported",
        (
            "tests/test_wysiwyg_sanitize.py::TestSanitizeHtmlFailsClosedWithoutNh3",
            "tests/test_wysiwyg_sanitize.py::TestWysiwygSaveFailsClosedWithoutNh3",
            "tests/test_wysiwyg_sanitize.py::TestWysiwygChangelistFailsClosedWithoutNh3",
        ),
    ),
    # ── 0.1.0b8 ─────────────────────────────────────────────────────────────
    SecurityFix(
        "``wysiwyg=True`` sanitize-on-write now covers ``snap_field()`` too",
        ("tests/test_wysiwyg_sanitize.py::TestSnapFieldWysiwygSanitizedOnSave",),
    ),
    SecurityFix(
        "An unresolvable model on the dynamic API now denies every HTTP verb",
        ("tests/test_model_api.py::TestUnresolvableModelGuardOrderingAndScope",),
    ),
    # ── 0.1.0b9: found and fixed before the encryption subsystem shipped ────
    SecurityFix(
        "An encryption key entry written the wrong way round never reaches an error message",
        (
            "tests/test_encryption_keys.py::TestEncryptionKey"
            "::test_a_reversed_entry_never_puts_the_key_in_the_message",
        ),
        in_release_notes=False,
        origin="#CRYPT1 review, fixed before 0.1.0b9 shipped the encryption subsystem",
    ),
    SecurityFix(
        "A blind-index sibling column is masked together with the encrypted column it indexes",
        ("tests/test_encryption_leak_surfaces.py::TestTheSiblingIsProtectedToo",),
        in_release_notes=False,
        origin="#CRYPT1 review, fixed before 0.1.0b9 shipped the encryption subsystem",
    ),
    SecurityFix(
        "Two field names differing only in case never share an encryption binding (AAD)",
        (
            "tests/test_encryption_cipher.py::TestAadBinding"
            "::test_two_field_names_differing_only_in_case_do_not_share_a_binding",
        ),
        in_release_notes=False,
        origin="#CRYPT1 review, fixed before 0.1.0b9 shipped the encryption subsystem",
    ),
    # ── 0.1.0b10 ────────────────────────────────────────────────────────────
    SecurityFix(
        "The serializer mixins mask and gate a hand-built ``ModelSerializer`` too",
        ("tests/test_serializer_mixins_hand_built.py",),
        in_release_notes=False,
        origin="#RM1a — a fail-open published under Fixed: masking turned off silently",
    ),
    SecurityFix(
        "The audit trail no longer stores a related object's label",
        ("tests/test_audit_trail.py::TestRelationsAreRecordedByKey",),
    ),
    SecurityFix(
        "An audit row's object label is masked like the fields it is made of",
        (
            "tests/test_audit_object_repr_masking.py::TestMaskObjectRepr",
            "tests/test_audit_object_repr_masking.py::TestAuditSurfaces",
        ),
    ),
    SecurityFix(
        "Django's own admin history message stores a masked field masked",
        ("tests/test_audit_object_repr_masking.py::TestLogEntryMessage",),
    ),
    SecurityFix(
        "``formatted_id`` escaped a non-integer primary key into the changelist as markup",
        ("tests/test_models.py::TestFormattedId::test_a_non_integer_pk_is_html_escaped",),
    ),
    SecurityFix(
        "Alert webhooks refuse any scheme but",
        ("tests/test_alert_channels.py::TestPostJsonRefusesNonHttpSchemes",),
    ),
    SecurityFix(
        "``snapadmin_restore`` quotes the database name in the SQL it sends to ``psql``",
        ("tests/test_restore_target_database.py::TestTerminateStatementQuotesTheDatabaseName",),
    ),
    SecurityFix(
        "CSV exports never open as spreadsheet formulas (CWE-1236)",
        (
            "tests/test_export.py::TestRunExportJob"
            "::test_csv_cell_opening_with_a_formula_trigger_is_neutralised",
            "tests/test_audit_trail.py::TestExportCommand"
            "::test_csv_export_neutralises_a_formula_in_attacker_controlled_text",
            "tests/test_properties_exporting.py::TestCsvLaws",
        ),
    ),
    SecurityFix(
        "No part of a sharding DSN's password reaches an error message",
        (
            "tests/test_sharding_registration.py::TestRedactDsn",
            "tests/test_properties_sharding.py::TestNoPasswordEscapes",
        ),
    ),
    SecurityFix(
        "A masking setting in the wrong shape no longer fails open",
        ("tests/test_masking_hostile_settings.py",),
    ),
    SecurityFix(
        "Export ``filters`` are validated by shape before the allowlist runs",
        (
            "tests/test_export.py::TestExportFilterValidationApi"
            "::test_a_malformed_filters_payload_is_a_400_naming_the_problem",
            "tests/test_fuzz_api.py::TestExportApiUnderHostileFilters",
        ),
    ),
)

#: The registry only grows: a fix leaves it only if the code it guarded is gone,
#: and then this number goes down in the same change, with the reason.
REGISTRY_FLOOR = 31


def node_ids() -> frozenset[str]:
    """Every test id the registry names."""
    return frozenset(test for fix in REGISTRY for test in fix.tests)


def is_security_regression(nodeid: str, registered: frozenset[str]) -> bool:
    """Whether the collected test ``nodeid`` is one of, or inside, a registered id.

    A registered module or class covers every test in it, and a registered test
    covers each of its parametrisations: ``a.py::T`` matches ``a.py::T::test_x``
    and ``a.py::T::test_x[1]``, but not ``a.py::TestOther``.
    """
    return any(
        nodeid == registered_id
        or nodeid.startswith(registered_id + "::")
        or nodeid.startswith(registered_id + "[")
        for registered_id in registered
    )

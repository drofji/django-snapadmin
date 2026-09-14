"""
tests/test_encryption_leak_surfaces.py

Where the plaintext comes back (#CRYPT1e) — and what stops it there.

An encrypted field decrypts transparently, which is the feature and also the
problem: every layer above the ORM receives an ordinary Python value and would
happily emit it. The column is protected; the *plaintext in Python* is not, and
each surface that handles it is a separate place a secret can escape into a
store with a different threat model.

Five of them, each with a tested default:

* **Elasticsearch** — a second datastore, usually on a different host, often
  with weaker access control and no encryption at rest. An encrypted field is
  excluded from the mapping and from the document; naming one in an explicit
  ``es_mapping`` is a check error, not a warning.
* **The audit log** — records *that* an encrypted field changed, never what it
  changed from or to. An audit trail of secrets is a second copy of the
  secrets, in a table people are given broad read access to precisely because
  it is supposed to be safe.
* **Exports and the API** (REST, GraphQL, CSV/XLSX) — masked unless the caller
  has PII access, reusing the existing masking machinery rather than inventing
  a second permission concept.
* **The admin** — ``searchable=True`` without a blind index is a check error;
  Django would build an ``icontains`` over ciphertext that matches nothing.
* **Ordering and indexes** — ``Meta.ordering`` on an encrypted field sorts by
  ciphertext, which is not an error anywhere, just wrong. It is refused.

Each is checked both ways: that the default keeps the plaintext in, and that a
caller who is *supposed* to see it still can.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db import models as django_models
from django.test.utils import isolate_apps

from snapadmin import audit, checks
from snapadmin import fields as snap_fields
from snapadmin import models as snap_models
from snapadmin.encryption import keys as keymod
from snapadmin.masking import get_masked_fields


def _material(byte: int) -> bytes:
    return bytes([byte]) * keymod.KEY_BYTES


@pytest.fixture
def keyset(settings):
    settings.SNAPADMIN_ENCRYPTION = {
        "KEYS": [{"id": "k", "key": keymod.encode_key(_material(21))}]
    }
    keymod.reset_keyset()
    yield keymod.get_keyset()
    keymod.reset_keyset()


@contextlib.contextmanager
def _indexed_model():
    """A model that mirrors to Elasticsearch *and* holds an encrypted column.

    Yielded from inside ``isolate_apps`` rather than returned: the masking
    helper and the system checks both resolve the model through the app
    registry, so it has to still be in one while they run.
    """
    with isolate_apps("snapadmin"):
        class Chart(snap_models.SnapModel):
            title = snap_fields.SnapCharField(max_length=100, searchable=True)
            diagnosis = snap_fields.SnapEncryptedTextField()

            es_auto_mapping = True
            es_storage_mode = snap_models.EsStorageMode.DUAL

            class Meta:
                app_label = "snapadmin"

        yield Chart


# ─────────────────────────────────────────────────────────────────────────────
# Elasticsearch
# ─────────────────────────────────────────────────────────────────────────────

class TestElasticsearch:
    def test_the_auto_mapping_skips_an_encrypted_field(self):
        with _indexed_model() as model:
            mapping = model.get_es_mapping()
        assert "title" in mapping
        assert "diagnosis" not in mapping

    def test_the_document_omits_an_encrypted_field(self):
        with _indexed_model() as model:
            doc = model(pk=1, title="Chart", diagnosis="a private note").get_es_document()
        assert doc["title"] == "Chart"
        assert "diagnosis" not in doc
        assert "a private note" not in str(doc)

    def test_an_explicit_mapping_cannot_smuggle_one_in(self):
        """Defence in depth: the check errors, and the document still omits it."""
        with isolate_apps("snapadmin"):
            class Forced(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50)
                es_mapping = {"secret": {"type": "text"}}
                es_storage_mode = snap_models.EsStorageMode.DUAL

                class Meta:
                    app_label = "snapadmin"

        doc = Forced(pk=1, secret="leak me").get_es_document()
        assert "secret" not in doc

    def test_a_plain_field_still_reaches_the_document(self):
        """Proof the exclusion is targeted and not a broken document builder."""
        with _indexed_model() as model:
            assert model(pk=7, title="Chart", diagnosis="x").get_es_document()["id"] == 7


# ─────────────────────────────────────────────────────────────────────────────
# The audit log
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditRedaction:
    def test_an_encrypted_field_records_that_it_changed_only(self):
        with _indexed_model() as model:
            entry = audit.change_entry(model, "diagnosis", "before", "after")
        assert entry == {"old": audit.REDACTED, "new": audit.REDACTED}

    def test_a_plain_field_records_its_values(self):
        with _indexed_model() as model:
            entry = audit.change_entry(model, "title", "before", "after")
        assert entry == {"old": "before", "new": "after"}

    def test_an_absent_old_value_stays_absent(self):
        """A created row shows "nothing -> something", not "something -> something"."""
        with _indexed_model() as model:
            entry = audit.change_entry(model, "diagnosis", None, "first value")
        assert entry == {"old": None, "new": audit.REDACTED}

    def test_an_unknown_field_is_recorded_normally(self):
        """A form field with no model field behind it must not break the trail."""
        with _indexed_model() as model:
            entry = audit.change_entry(model, "not_a_field", None, "x")
        assert entry == {"old": None, "new": "x"}

    def test_a_model_without_meta_is_tolerated(self):
        assert audit.field_is_encrypted(object(), "anything") is False

    def test_the_redaction_marker_carries_no_value(self):
        assert "before" not in audit.REDACTED and "after" not in audit.REDACTED

    def test_field_is_encrypted_detects_by_marker(self):
        with _indexed_model() as model:
            assert audit.field_is_encrypted(model, "diagnosis") is True
            assert audit.field_is_encrypted(model, "title") is False


@pytest.mark.django_db(transaction=True)
class TestAuditTrailEndToEnd:
    def test_an_admin_change_never_stores_the_plaintext(self, keyset, admin_user, rf):
        """The full admin write path, not the helper in isolation."""
        from django.contrib.admin.sites import AdminSite

        from snapadmin.models import SnapadminAuditLog

        with isolate_apps("snapadmin"):
            class Record(snap_models.SnapModel):
                label = snap_fields.SnapCharField(max_length=50, show_in_form=True)
                note = snap_fields.SnapEncryptedCharField(max_length=50, show_in_form=True)

                class Meta:
                    app_label = "snapadmin"

        with connection.schema_editor(atomic=False) as editor:
            editor.create_model(Record)
        try:
            class _Form:
                changed_data = ["label", "note"]
                initial = {"label": "old label", "note": "old secret"}
                cleaned_data = {"label": "new label", "note": "new secret"}

            admin = snap_models.SnapSaveMixin
            request = rf.post("/")
            request.user = admin_user
            obj = Record(label="new label", note="new secret")

            from django.contrib.admin import ModelAdmin

            class RecordAdmin(snap_models.SnapSaveMixin, ModelAdmin):
                pass

            RecordAdmin(Record, AdminSite()).save_model(request, obj, _Form(), change=True)

            entry = SnapadminAuditLog.objects.latest("id")
            rendered = str(entry.changes)
            assert "old secret" not in rendered
            assert "new secret" not in rendered
            assert "new label" in rendered
            assert entry.changes["note"] == {"old": audit.REDACTED, "new": audit.REDACTED}
            assert admin is snap_models.SnapSaveMixin
        finally:
            with connection.schema_editor(atomic=False) as editor:
                editor.delete_model(Record)

    def test_a_created_row_does_not_snapshot_the_plaintext(self, keyset, admin_user, rf):
        from django.contrib.admin import ModelAdmin
        from django.contrib.admin.sites import AdminSite

        from snapadmin.models import SnapadminAuditLog

        with isolate_apps("snapadmin"):
            class Fresh(snap_models.SnapModel):
                label = snap_fields.SnapCharField(max_length=50, show_in_form=True)
                note = snap_fields.SnapEncryptedCharField(max_length=50, show_in_form=True)

                class Meta:
                    app_label = "snapadmin"

        with connection.schema_editor(atomic=False) as editor:
            editor.create_model(Fresh)
        try:
            class _Form:
                cleaned_data = {"label": "a label", "note": "a secret"}

            class FreshAdmin(snap_models.SnapSaveMixin, ModelAdmin):
                pass

            request = rf.post("/")
            request.user = admin_user
            obj = Fresh(label="a label", note="a secret")
            FreshAdmin(Fresh, AdminSite()).save_model(request, obj, _Form(), change=False)

            entry = SnapadminAuditLog.objects.latest("id")
            assert "a secret" not in str(entry.changes)
            assert entry.changes["note"] == {"old": None, "new": audit.REDACTED}
        finally:
            with connection.schema_editor(atomic=False) as editor:
                editor.delete_model(Fresh)


# ─────────────────────────────────────────────────────────────────────────────
# Exports and the API, through the masking machinery
# ─────────────────────────────────────────────────────────────────────────────

class TestMaskedByDefault:
    """Checked against the demo's real ``CustomerProfile.tax_id``.

    The model has to be in the live app registry for this: ``get_masked_fields``
    takes an app label and a model name, resolves the model itself, and an
    ``isolate_apps`` model is invisible to the global registry by construction.
    Using the shipped demo model also means these assertions are about what a
    user actually gets, not about a fixture built to pass.
    """

    def test_an_encrypted_field_is_masked_without_being_configured(self):
        assert "tax_id" in get_masked_fields("demo", "customerprofile")

    def test_a_plain_field_is_not(self):
        assert "newsletter" not in get_masked_fields("demo", "customerprofile")

    def test_configured_masking_still_comes_first(self, settings):
        """Declared order is preserved; the encrypted field is appended."""
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.CustomerProfile": ["newsletter"]}
        masked = get_masked_fields("demo", "customerprofile")
        assert masked[0] == "newsletter"
        assert "tax_id" in masked

    def test_an_encrypted_field_is_not_listed_twice(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"demo.CustomerProfile": ["tax_id"]}
        assert get_masked_fields("demo", "customerprofile").count("tax_id") == 1

    def test_an_unknown_model_is_handled(self):
        """Masking settings may name a model that does not exist."""
        assert get_masked_fields("nosuchapp", "nosuchmodel") == []

    def test_a_malformed_model_reference_is_handled(self):
        assert get_masked_fields("", "") == []

    def test_a_per_field_rule_can_still_unlock_it(self, settings):
        """The escape hatch: a project that wants the value exposed to a role."""
        from snapadmin.masking import mask_field

        settings.SNAPADMIN_MASKING_RULES = {
            "demo.CustomerProfile": {"tax_id": {"permission": "demo.view_tax_id"}}
        }

        class _User:
            is_active = True
            is_authenticated = True
            is_superuser = True

        assert mask_field("demo", "customerprofile", "tax_id", "DE12345", _User()) == "DE12345"

    def test_without_the_permission_the_value_is_masked(self, settings):
        from snapadmin.masking import mask_field

        class _Anonymous:
            is_active = False
            is_authenticated = False
            is_superuser = False

        masked = mask_field("demo", "customerprofile", "tax_id", "DE12345", _Anonymous())
        assert masked != "DE12345"
        assert "12345" not in str(masked)


# ─────────────────────────────────────────────────────────────────────────────
# System checks
# ─────────────────────────────────────────────────────────────────────────────

def _ids(errors) -> list[str]:
    return sorted(e.id for e in errors)


def _run(check, isolated):
    """Run a check against an isolated app registry.

    ``isolate_apps`` swaps ``Options.default_apps`` so newly declared models
    land in a private registry; the global ``django.apps.apps`` the checks walk
    never sees them. Pointing the check module's own ``apps`` at the isolated
    registry is what makes a one-model fixture testable at all.
    """
    with mock.patch.object(checks, "apps", isolated):
        return check(None)


class TestChecks:
    def test_searchable_without_a_blind_index_errors(self):
        with isolate_apps("snapadmin") as isolated:
            class Searchy(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50, searchable=True)

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.E021"]
        assert "secret" in found[0].msg
        # The error points at the offending field, not merely at the model —
        # that is what makes `manage.py check` output actionable.
        assert found[0].obj is Searchy._meta.get_field("secret")

    def test_searchable_with_a_blind_index_is_fine(self):
        with isolate_apps("snapadmin") as isolated:
            class Fine(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(
                    max_length=50, searchable=True, blind_index=True
                )

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_encrypted_field_usage, isolated) == []
            # The arrangement under test, stated: searchable *and* blind-indexed.
            declared = Fine._meta.get_field("secret")
            assert (declared.searchable, declared.blind_index) == (True, True)

    def test_unique_without_a_blind_index_errors(self):
        with isolate_apps("snapadmin") as isolated:
            class Uniq(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50, unique=True)

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.E022"]
        assert found[0].obj is Uniq._meta.get_field("secret")

    def test_ordering_by_an_encrypted_field_errors(self):
        with isolate_apps("snapadmin") as isolated:
            class Sorted(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50)

                class Meta:
                    app_label = "snapadmin"
                    ordering = ["-secret"]

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.E023"]
        # Ordering is declared on Meta, so this one is reported against the model.
        assert found[0].obj is Sorted

    def test_a_useless_index_warns(self):
        with isolate_apps("snapadmin") as isolated:
            class Indexed(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50, db_index=True)

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.W019"]
        assert found[0].obj is Indexed._meta.get_field("secret")

    def test_filterable_errors(self):
        """A sidebar filter lists the column's distinct values — decrypted."""
        with isolate_apps("snapadmin") as isolated:
            class Filtered(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50, filterable=True)

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.E024"]
        assert found[0].obj is Filtered._meta.get_field("secret")

    def test_filterable_errors_even_with_a_blind_index(self):
        """Unlike the others, this one a blind index cannot make safe."""
        with isolate_apps("snapadmin") as isolated:
            class StillFiltered(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(
                    max_length=50, filterable=True, blind_index=True
                )

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.E024"]
        assert found[0].obj is StillFiltered._meta.get_field("secret")

    def test_a_sibling_pointing_at_an_unencrypted_field_warns(self):
        with isolate_apps("snapadmin") as isolated:
            class Mispointed(snap_models.SnapModel):
                plain = snap_fields.SnapCharField(max_length=50)
                plain_bi = snap_fields.SnapBlindIndexField(source_field="plain")

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
            # Reading the attribute must still work rather than raising.
            assert Mispointed(plain="x", plain_bi="stored").plain_bi == "stored"
        assert _ids(found) == ["snapadmin.W020"]
        assert "not an encrypted field" in [w.msg for w in found if w.id == "snapadmin.W020"][0]

    def test_a_dangling_blind_index_sibling_warns(self):
        with isolate_apps("snapadmin") as isolated:
            class Dangling(snap_models.SnapModel):
                loose = snap_fields.SnapBlindIndexField(source_field="gone")

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_field_usage, isolated)
        assert _ids(found) == ["snapadmin.W020"]
        # The dangling sibling itself is named, not the field it failed to find.
        assert found[0].obj is Dangling._meta.get_field("loose")

    def test_an_encrypted_field_in_an_es_mapping_errors(self):
        with isolate_apps("snapadmin") as isolated:
            class Mapped(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50)
                es_mapping = {"secret": {"type": "text"}}
                es_storage_mode = snap_models.EsStorageMode.DUAL

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_encrypted_fields_not_indexed, isolated)
        assert _ids(found) == ["snapadmin.E020"]
        assert "Elasticsearch" in found[0].msg
        # The mapping is a model-level declaration, so the model is the subject.
        assert found[0].obj is Mapped

    def test_a_model_with_no_es_mapping_is_fine(self):
        with isolate_apps("snapadmin") as isolated:
            class Quiet(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50)

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_encrypted_fields_not_indexed, isolated) == []
            # Encrypted field present, no es_mapping at all: nothing to report.
            assert Quiet.es_mapping is None

    def test_a_plain_field_in_an_es_mapping_is_fine(self):
        with isolate_apps("snapadmin") as isolated:
            class Plain(snap_models.SnapModel):
                title = snap_fields.SnapCharField(max_length=50)
                es_mapping = {"title": {"type": "text"}}
                es_storage_mode = snap_models.EsStorageMode.DUAL

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_encrypted_fields_not_indexed, isolated) == []
            # A mapping does exist — it just names a field that is not encrypted.
            assert Plain.es_mapping == {"title": {"type": "text"}}

    def test_both_checks_are_registered(self):
        assert checks.check_encrypted_field_usage in checks.ALL_CHECKS
        assert checks.check_encrypted_fields_not_indexed in checks.ALL_CHECKS

    def test_the_shipped_demo_declares_its_encrypted_field_correctly(self):
        """Run against the live registry: `CustomerProfile.tax_id` must be clean.

        The demo is the package's own dogfood, so these checks passing on it is
        a statement about the documented example, not about a test fixture.
        """
        assert checks.check_encrypted_field_usage(None) == []
        assert checks.check_encrypted_fields_not_indexed(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# The blind index is as sensitive as the column it indexes
# ─────────────────────────────────────────────────────────────────────────────

class TestTheSiblingIsProtectedToo:
    """A deterministic index served to a caller denied the column is an oracle.

    `<field>_bi` is `HMAC(key, value)` — stable for a given value — so anyone
    holding it can test a guess offline and can tell which rows share a value.
    Handing it to a caller who is masked out of `tax_id` itself would give away
    most of what masking `tax_id` was protecting.
    """

    def test_the_sibling_is_masked_as_well(self):
        assert "tax_id_bi" in get_masked_fields("demo", "customerprofile")

    def test_the_sibling_is_masked_for_a_caller_without_pii_access(self):
        from snapadmin.masking import mask_field

        class _Anonymous:
            is_active = False
            is_authenticated = False
            is_superuser = False

        index = "0123456789abcdef0123456789abcdef0123456789a"
        assert mask_field("demo", "customerprofile", "tax_id_bi", index, _Anonymous()) != index


class TestApiFiltersSkipEncryptedColumns:
    """A generated filter over ciphertext is a documented 500.

    `?tax_id__icontains=` would reach the field's own lookup guard and raise —
    a FieldError, i.e. a 500, on a query parameter the schema advertised. And a
    filter on `tax_id_bi` would *work*, which is worse: equality matching on a
    column the same caller is not allowed to read.
    """

    def test_no_filter_is_generated_for_an_encrypted_field(self):
        pytest.importorskip("django_filters")
        from snapadmin.api.filters import build_filterset_for_model
        from demo.apps.shop.models import CustomerProfile

        generated = build_filterset_for_model(CustomerProfile).base_filters
        assert not [name for name in generated if name.startswith("tax_id")]

    def test_a_third_party_field_named_source_field_keeps_its_filters(self):
        """The sibling is detected by marker, not by the attribute name.

        `source_field` is a common attribute name on third-party fields;
        duck-typing on it would silently drop their query parameters, which is
        the kind of bug nobody thinks to look for.
        """
        pytest.importorskip("django_filters")
        from snapadmin.api.filters import build_filterset_for_model

        with isolate_apps("snapadmin"):
            class Imposter(snap_models.SnapModel):
                label = snap_fields.SnapCharField(max_length=30)

                class Meta:
                    app_label = "snapadmin"

            Imposter._meta.get_field("label").source_field = "something"
            generated = build_filterset_for_model(Imposter).base_filters
            assert any(name.startswith("label") for name in generated)

    def test_a_plain_field_still_gets_its_filters(self):
        pytest.importorskip("django_filters")
        from snapadmin.api.filters import build_filterset_for_model
        from demo.apps.shop.models import CustomerProfile

        generated = build_filterset_for_model(CustomerProfile).base_filters
        assert "newsletter" in generated


class TestTheChangelistCannotSortByCiphertext:
    """ORDER BY never passes through the lookup guard, so it is taken away here."""

    def test_an_encrypted_field_is_not_sortable(self):
        from django.contrib import admin as django_admin

        from demo.apps.shop.models import CustomerProfile

        registered = django_admin.site._registry[CustomerProfile]
        assert "tax_id" not in (registered.sortable_by or [])

    def test_plain_columns_stay_sortable(self):
        from django.contrib import admin as django_admin

        from demo.apps.shop.models import CustomerProfile

        registered = django_admin.site._registry[CustomerProfile]
        assert set(registered.sortable_by) <= set(registered.list_display)
        assert registered.sortable_by

    def test_an_encrypted_field_stays_off_the_changelist_by_default(self):
        field = snap_fields.SnapEncryptedCharField(max_length=10)
        assert field.show_in_list is False

    def test_an_explicit_show_in_list_still_wins(self):
        field = snap_fields.SnapEncryptedCharField(max_length=10, show_in_list=True)
        assert field.show_in_list is True

    def test_a_plain_field_keeps_the_old_default(self):
        assert snap_fields.SnapCharField(max_length=10).show_in_list is True

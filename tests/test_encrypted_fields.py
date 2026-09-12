"""
tests/test_encrypted_fields.py

The ``SnapEncrypted*Field`` family (#CRYPT1c) — ciphertext in the column, an
ordinary Python value on the instance.

The property every test here exists to protect is narrow and absolute: **the
database never sees the plaintext, and the application never sees anything
else.** So the round-trip assertions are paired — each one reads the value back
through the ORM *and* reads the raw column through a cursor, asserting the
plaintext is not in it. A test that only checked the ORM round-trip would pass
just as happily if the field did nothing at all.

The other failure modes pinned here are the quiet ones:

* **Double encryption.** Re-saving a loaded instance, a ``bulk_update``, a
  queryset ``.update()`` and a fixture reload must not encrypt an envelope a
  second time. Nothing raises when that happens — the row simply becomes
  unreadable, and the backup that could have fixed it ages out.
* **Silent plaintext.** A save with no keyset configured must raise, never fall
  back to writing the value as-is.
* **Migration noise.** ``deconstruct()`` must report nothing SnapAdmin-specific,
  so adding these fields to a project produces one column change and no churn
  afterwards.

The models are declared with ``isolate_apps`` and given real tables through
``schema_editor``, the same technique ``test_wysiwyg_sanitize.py`` uses: these
need a genuine column to read back, and none of the demo models should carry an
encrypted field purely to satisfy a test.
"""
from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db import models as django_models
from django.test.utils import isolate_apps
from django.utils import timezone

from snapadmin import fields as snap_fields
from snapadmin.encryption import cipher as ciphermod
from snapadmin.encryption import keys as keymod


KEY_ID = "test-key"


def _material(byte: int) -> bytes:
    return bytes([byte]) * keymod.KEY_BYTES


@pytest.fixture
def keyset(settings):
    """A real, deterministic keyset for the duration of one test."""
    settings.SNAPADMIN_ENCRYPTION = {
        "KEYS": [{"id": KEY_ID, "key": keymod.encode_key(_material(11))}]
    }
    keymod.reset_keyset()
    yield keymod.get_keyset()
    keymod.reset_keyset()


@pytest.fixture
def no_keyset(settings, monkeypatch):
    settings.SNAPADMIN_ENCRYPTION = {}
    monkeypatch.delenv(keymod.ENV_KEYS, raising=False)
    monkeypatch.delenv(keymod.ENV_KEY_FILE, raising=False)
    keymod.reset_keyset()
    yield
    keymod.reset_keyset()


def _make_model():
    """One isolated model carrying every member of the family, plus a control."""
    with isolate_apps("snapadmin"):
        class Vault(django_models.Model):
            note = snap_fields.SnapEncryptedCharField(max_length=200)
            body = snap_fields.SnapEncryptedTextField()
            email = snap_fields.SnapEncryptedEmailField()
            payload = snap_fields.SnapEncryptedJSONField()
            count = snap_fields.SnapEncryptedIntegerField()
            amount = snap_fields.SnapEncryptedDecimalField(max_digits=10, decimal_places=2)
            day = snap_fields.SnapEncryptedDateField()
            moment = snap_fields.SnapEncryptedDateTimeField()
            plain = snap_fields.SnapCharField(max_length=200)  # control: must stay readable

            class Meta:
                app_label = "snapadmin"

        return Vault


@pytest.fixture
def vault_model(transactional_db):
    model = _make_model()
    with connection.schema_editor(atomic=False) as editor:
        editor.create_model(model)
    try:
        yield model
    finally:
        with connection.schema_editor(atomic=False) as editor:
            editor.delete_model(model)


SAMPLE = {
    "note": "patient is allergic to penicillin",
    "body": "long free text\nwith newlines",
    "email": "private@example.org",
    "payload": {"iban": "DE00 1234", "tags": ["a", "b"], "n": 3},
    "count": 42,
    "amount": Decimal("1234.56"),
    "day": datetime.date(2026, 3, 14),
    "moment": datetime.datetime(2026, 3, 14, 15, 9, 26, tzinfo=datetime.timezone.utc),
    "plain": "not a secret",
}

ENCRYPTED_COLUMNS = [c for c in SAMPLE if c != "plain"]


def _raw(model, pk: int, column: str) -> object:
    """The value as the database actually holds it, bypassing every converter."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {model._meta.db_table} WHERE id = %s", [pk])
        return cursor.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# The declaration itself
# ─────────────────────────────────────────────────────────────────────────────

FAMILY = [
    snap_fields.SnapEncryptedCharField,
    snap_fields.SnapEncryptedTextField,
    snap_fields.SnapEncryptedEmailField,
    snap_fields.SnapEncryptedJSONField,
    snap_fields.SnapEncryptedIntegerField,
    snap_fields.SnapEncryptedDecimalField,
    snap_fields.SnapEncryptedDateField,
    snap_fields.SnapEncryptedDateTimeField,
]


class TestTheFamily:
    @pytest.mark.parametrize("cls", FAMILY, ids=lambda c: c.__name__)
    def test_every_member_carries_the_marker(self, cls):
        """`has_encrypted_fields()` and the system checks detect by attribute."""
        assert cls.is_snap_encrypted is True

    @pytest.mark.parametrize("cls", FAMILY, ids=lambda c: c.__name__)
    def test_every_member_is_a_snap_field(self, cls):
        """The whole Snap kwarg surface must work on an encrypted field too."""
        assert issubclass(cls, snap_fields.SnapField)

    def test_snap_kwargs_are_accepted_and_stripped(self):
        field = snap_fields.SnapEncryptedCharField(
            max_length=50, show_in_list=False, searchable=False, tab="Sensitive"
        )
        assert field.show_in_list is False
        assert field.tab == "Sensitive"
        kwargs = field.deconstruct()[3]
        for snap_only in ("show_in_list", "searchable", "tab", "row", "filterable"):
            assert snap_only not in kwargs

    @pytest.mark.parametrize("cls", FAMILY, ids=lambda c: c.__name__)
    def test_column_is_text(self, cls):
        extra = {}
        if cls is snap_fields.SnapEncryptedDecimalField:
            extra = {"max_digits": 8, "decimal_places": 2}
        elif cls is snap_fields.SnapEncryptedCharField:
            extra = {"max_length": 10}
        field = cls(**extra)
        assert field.db_type(connection) == connection.data_types["TextField"]

    @pytest.mark.parametrize("cls", FAMILY, ids=lambda c: c.__name__)
    def test_no_column_check_constraint(self, cls):
        """A numeric CHECK on a text column would reject every ciphertext."""
        extra = {}
        if cls is snap_fields.SnapEncryptedDecimalField:
            extra = {"max_digits": 8, "decimal_places": 2}
        elif cls is snap_fields.SnapEncryptedCharField:
            extra = {"max_length": 10}
        assert cls(**extra).db_check(connection) is None

    def test_char_max_length_is_optional(self):
        """The column is text, so max_length only bounds the plaintext."""
        field = snap_fields.SnapEncryptedCharField()
        field.set_attributes_from_name("secret")
        assert [e.id for e in field.check()] == []

    def test_char_max_length_still_validates_the_plaintext(self):
        field = snap_fields.SnapEncryptedCharField(max_length=5)
        field.set_attributes_from_name("secret")
        assert any(v.limit_value == 5 for v in field.validators if hasattr(v, "limit_value"))

    def test_relation_target_is_also_text(self):
        """A field pointing at an encrypted column must declare the same type."""
        field = snap_fields.SnapEncryptedCharField(max_length=10)
        assert field.rel_db_type(connection) == connection.data_types["TextField"]

    def test_integer_drops_the_column_range_validators(self):
        """The column is text: a 32-bit ceiling would be a fiction, and looking
        one up under the TextField internal type raises outright."""
        field = snap_fields.SnapEncryptedIntegerField()
        field.set_attributes_from_name("count")
        assert field.validators == []

    def test_integer_keeps_the_validators_the_caller_declared(self):
        from django.core.validators import MinValueValidator

        declared = MinValueValidator(0)
        field = snap_fields.SnapEncryptedIntegerField(validators=[declared])
        field.set_attributes_from_name("count")
        assert declared in field.validators

    def test_formfield_keeps_the_natural_type(self):
        """The admin must render a date picker, not a textarea of ciphertext."""
        from django import forms

        assert isinstance(
            snap_fields.SnapEncryptedDateField().formfield(), forms.DateField
        )
        assert isinstance(
            snap_fields.SnapEncryptedIntegerField().formfield(), forms.IntegerField
        )


# ─────────────────────────────────────────────────────────────────────────────
# The round trip — and what the database actually holds
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestRoundTrip:
    def test_every_type_survives_the_database(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        loaded = vault_model.objects.get(pk=row.pk)
        for name, expected in SAMPLE.items():
            assert getattr(loaded, name) == expected, name

    def test_every_encrypted_column_holds_an_envelope(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        for column in ENCRYPTED_COLUMNS:
            stored = _raw(vault_model, row.pk, column)
            assert isinstance(stored, str), column
            assert stored.startswith("snap1."), column
            assert ciphermod.looks_encrypted(stored), column

    def test_no_plaintext_reaches_the_column(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        assert "penicillin" not in _raw(vault_model, row.pk, "note")
        assert "example.org" not in _raw(vault_model, row.pk, "email")
        assert "1234" not in _raw(vault_model, row.pk, "payload")
        assert "42" not in json.dumps(_raw(vault_model, row.pk, "count"))
        assert "2026-03-14" not in _raw(vault_model, row.pk, "day")

    def test_the_control_field_stays_plaintext(self, vault_model, keyset):
        """Proof the test is measuring encryption and not a broken column."""
        row = vault_model.objects.create(**SAMPLE)
        assert _raw(vault_model, row.pk, "plain") == "not a secret"

    def test_each_row_gets_its_own_nonce(self, vault_model, keyset):
        first = vault_model.objects.create(**SAMPLE)
        second = vault_model.objects.create(**SAMPLE)
        assert _raw(vault_model, first.pk, "note") != _raw(vault_model, second.pk, "note")

    def test_the_envelope_names_the_active_key(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        assert _raw(vault_model, row.pk, "note").split(".")[1] == KEY_ID

    def test_refresh_from_db_decrypts(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        row.refresh_from_db()
        assert row.note == SAMPLE["note"]

    def test_values_list_decrypts(self, vault_model, keyset):
        vault_model.objects.create(**SAMPLE)
        assert list(vault_model.objects.values_list("count", flat=True)) == [42]

    def test_json_field_keeps_structure_and_types(self, vault_model, keyset):
        row = vault_model.objects.create(**{**SAMPLE, "payload": {"a": [1, 2, {"b": None}]}})
        row.refresh_from_db()
        assert row.payload == {"a": [1, 2, {"b": None}]}

    def test_decimal_keeps_its_scale(self, vault_model, keyset):
        row = vault_model.objects.create(**{**SAMPLE, "amount": Decimal("0.10")})
        row.refresh_from_db()
        assert row.amount == Decimal("0.10")

    def test_datetime_keeps_its_timezone(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        row.refresh_from_db()
        assert timezone.is_aware(row.moment)
        assert row.moment == SAMPLE["moment"]


# ─────────────────────────────────────────────────────────────────────────────
# NULL, empty, and the difference between them
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestNullAndEmpty:
    def test_none_stays_sql_null(self, vault_model, keyset):
        row = vault_model.objects.create(**{**SAMPLE, "note": None})
        assert _raw(vault_model, row.pk, "note") is None
        row.refresh_from_db()
        assert row.note is None

    def test_empty_string_is_encrypted_like_any_other_value(self, vault_model, keyset):
        row = vault_model.objects.create(**{**SAMPLE, "note": ""})
        stored = _raw(vault_model, row.pk, "note")
        assert stored is not None and stored.startswith("snap1.")
        row.refresh_from_db()
        assert row.note == ""

    def test_null_and_empty_are_distinguishable_after_a_round_trip(self, vault_model, keyset):
        empty = vault_model.objects.create(**{**SAMPLE, "note": ""})
        null = vault_model.objects.create(**{**SAMPLE, "note": None})
        empty.refresh_from_db()
        null.refresh_from_db()
        assert empty.note == "" and null.note is None


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency — the silent corruption this feature can cause
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestNeverEncryptTwice:
    def test_resaving_a_loaded_instance_stays_readable(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        row.refresh_from_db()
        row.save()
        row.refresh_from_db()
        assert row.note == SAMPLE["note"]

    def test_resaving_does_not_nest_envelopes(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        row.refresh_from_db()
        row.save()
        assert _raw(vault_model, row.pk, "note").count("snap1.") == 1

    def test_bulk_update_round_trips(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        row.note = "changed"
        vault_model.objects.bulk_update([row], ["note"])
        row.refresh_from_db()
        assert row.note == "changed"
        assert _raw(vault_model, row.pk, "note").startswith("snap1.")

    def test_queryset_update_encrypts(self, vault_model, keyset):
        row = vault_model.objects.create(**SAMPLE)
        vault_model.objects.filter(pk=row.pk).update(note="updated through the queryset")
        row.refresh_from_db()
        assert row.note == "updated through the queryset"
        assert "updated" not in _raw(vault_model, row.pk, "note")

    def test_an_envelope_assigned_directly_is_stored_verbatim(self, vault_model, keyset):
        """The guard that makes a fixture reload safe."""
        token = ciphermod.encrypt(
            "already encrypted",
            aad=ciphermod.aad_for("snapadmin", "vault", "note"),
            keyset=keyset,
        )
        row = vault_model.objects.create(**{**SAMPLE, "note": token})
        assert _raw(vault_model, row.pk, "note") == token
        row.refresh_from_db()
        assert row.note == "already encrypted"

    def test_a_plaintext_that_merely_starts_with_snap1_is_still_encrypted(
        self, vault_model, keyset
    ):
        """A user typing 'snap1.…' must not be mistaken for stored ciphertext."""
        row = vault_model.objects.create(**{**SAMPLE, "note": "snap1.not.an.envelope"})
        row.refresh_from_db()
        assert row.note == "snap1.not.an.envelope"


# ─────────────────────────────────────────────────────────────────────────────
# dumpdata / loaddata keep the ciphertext
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestFixtures:
    def test_serialization_emits_ciphertext_not_plaintext(self, vault_model, keyset):
        from django.core import serializers

        row = vault_model.objects.create(**SAMPLE)
        dumped = serializers.serialize("json", [vault_model.objects.get(pk=row.pk)])
        assert "penicillin" not in dumped
        assert "snap1." in dumped

    def test_a_dump_reloads_into_the_same_value(self, vault_model, keyset):
        """The loaddata path, driven field by field.

        ``serializers.deserialize`` cannot be used directly here — the model
        lives in an isolated app registry and ``snapadmin.vault`` does not
        resolve — so this walks the same two steps the deserialiser does:
        ``to_python()`` on each dumped value, then ``save()``. That is exactly
        where the never-twice guard has to hold.
        """
        from django.core import serializers

        row = vault_model.objects.create(**SAMPLE)
        dumped = json.loads(
            serializers.serialize("json", [vault_model.objects.get(pk=row.pk)])
        )[0]["fields"]
        vault_model.objects.all().delete()

        rebuilt = vault_model(
            pk=row.pk,
            **{
                name: vault_model._meta.get_field(name).to_python(value)
                for name, value in dumped.items()
            },
        )
        rebuilt.save()

        reloaded = vault_model.objects.get(pk=row.pk)
        for name, expected in SAMPLE.items():
            assert getattr(reloaded, name) == expected, name

    def test_a_null_serialises_as_null(self, vault_model, keyset):
        row = vault_model.objects.create(**{**SAMPLE, "note": None})
        row.refresh_from_db()
        assert vault_model._meta.get_field("note").value_to_string(row) is None

    def test_an_instance_already_holding_an_envelope_is_not_re_encrypted(
        self, vault_model, keyset
    ):
        """Serialising a row read back without decryption must not nest envelopes."""
        token = ciphermod.encrypt(
            "kept as is",
            aad=ciphermod.aad_for("snapadmin", "vault", "note"),
            keyset=keyset,
        )
        row = vault_model(**{**SAMPLE, "note": token})
        assert vault_model._meta.get_field("note").value_to_string(row) == token

    def test_a_reloaded_dump_keeps_the_original_ciphertext(self, vault_model, keyset):
        """No re-encryption on the way in: the stored bytes are the dumped ones."""
        from django.core import serializers

        row = vault_model.objects.create(**SAMPLE)
        dumped = json.loads(
            serializers.serialize("json", [vault_model.objects.get(pk=row.pk)])
        )[0]["fields"]
        vault_model.objects.all().delete()

        field = vault_model._meta.get_field("note")
        rebuilt = vault_model(
            pk=row.pk,
            **{
                name: vault_model._meta.get_field(name).to_python(value)
                for name, value in dumped.items()
            },
        )
        rebuilt.save()
        assert _raw(vault_model, row.pk, "note") == dumped["note"]
        assert field.is_snap_encrypted is True


# ─────────────────────────────────────────────────────────────────────────────
# Failing closed
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestFailsClosed:
    def test_saving_without_a_keyset_raises_instead_of_storing_plaintext(
        self, vault_model, no_keyset
    ):
        with pytest.raises(ImproperlyConfigured):
            vault_model.objects.create(**SAMPLE)
        assert vault_model.objects.count() == 0

    def test_reading_under_a_different_key_fails_loudly(self, vault_model, keyset, settings):
        row = vault_model.objects.create(**SAMPLE)
        settings.SNAPADMIN_ENCRYPTION = {
            "KEYS": [{"id": KEY_ID, "key": keymod.encode_key(_material(99))}]
        }
        keymod.reset_keyset()
        with pytest.raises(ciphermod.DecryptionError):
            vault_model.objects.get(pk=row.pk)

    def test_legacy_plaintext_in_the_column_names_the_adoption_command(
        self, vault_model, keyset
    ):
        row = vault_model.objects.create(**SAMPLE)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {vault_model._meta.db_table} SET note = %s WHERE id = %s",
                ["still plaintext", row.pk],
            )
        with pytest.raises(ciphermod.DecryptionError) as exc:
            vault_model.objects.get(pk=row.pk)
        assert "snapadmin_encrypt_fields" in str(exc.value)

    def test_a_field_not_attached_to_a_model_explains_itself(self):
        field = snap_fields.SnapEncryptedCharField(max_length=10)
        with pytest.raises(ImproperlyConfigured) as exc:
            field.encryption_aad()
        assert "model" in str(exc.value).lower()

    def test_the_aad_is_the_field_home(self, vault_model):
        field = vault_model._meta.get_field("note")
        assert field.encryption_aad() == "snapadmin.vault.note"

    def test_a_value_bound_to_another_column_does_not_decrypt(self, vault_model, keyset):
        """Proof the AAD binding is live on the real field, not just in the cipher."""
        row = vault_model.objects.create(**SAMPLE)
        stolen = _raw(vault_model, row.pk, "note")
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {vault_model._meta.db_table} SET body = %s WHERE id = %s",
                [stolen, row.pk],
            )
        with pytest.raises(ciphermod.DecryptionError):
            vault_model.objects.get(pk=row.pk)


# ─────────────────────────────────────────────────────────────────────────────
# Migration hygiene
# ─────────────────────────────────────────────────────────────────────────────

class TestDeconstruct:
    @pytest.mark.parametrize("cls", FAMILY, ids=lambda c: c.__name__)
    def test_round_trips_to_an_equivalent_field(self, cls):
        extra = {}
        if cls is snap_fields.SnapEncryptedDecimalField:
            extra = {"max_digits": 8, "decimal_places": 2}
        elif cls is snap_fields.SnapEncryptedCharField:
            extra = {"max_length": 10}
        original = cls(required=True, **extra)
        name, path, args, kwargs = original.deconstruct()
        rebuilt = cls(*args, **kwargs)
        assert rebuilt.deconstruct()[1:] == (path, args, kwargs)
        assert rebuilt.null is False and rebuilt.blank is False

    @pytest.mark.parametrize("cls", FAMILY, ids=lambda c: c.__name__)
    def test_path_points_at_the_public_module(self, cls):
        extra = {}
        if cls is snap_fields.SnapEncryptedDecimalField:
            extra = {"max_digits": 8, "decimal_places": 2}
        elif cls is snap_fields.SnapEncryptedCharField:
            extra = {"max_length": 10}
        assert cls(**extra).deconstruct()[1].startswith("snapadmin.fields.")


@pytest.mark.django_db(transaction=True)
class TestDetection:
    def test_a_declared_field_answers_the_detection_predicate(self, vault_model):
        """`has_encrypted_fields()` walks installed models asking exactly this.

        It is asserted against a real declared field rather than by calling
        ``has_encrypted_fields()`` itself: this model lives in an isolated app
        registry and is invisible to ``apps.get_models()`` by construction.
        ``tests/test_encryption_keys.py`` covers the walk.
        """
        assert vault_model._meta.get_field("note").is_snap_encrypted is True
        assert getattr(vault_model._meta.get_field("plain"), "is_snap_encrypted", False) is False

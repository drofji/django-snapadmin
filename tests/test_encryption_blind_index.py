"""
tests/test_encryption_blind_index.py

Lookup guards and the blind index (#CRYPT1d) — what you can still ask the
database about a column it cannot read.

An encrypted column is opaque to SQL. That is the point, and it costs every
lookup: ``icontains``, ``gt``, ``startswith`` and ``ORDER BY`` are *impossible*,
not merely unsupported, and even ``exact`` compares against a ciphertext
carrying a fresh random nonce, so it would match nothing every single time.

The danger is not that those queries fail. It is that they could **succeed and
return an empty queryset** — encrypted data quietly becoming invisible data, a
failure no exception ever announces and no test catches downstream. So the
contract pinned here is: every unsupported lookup raises a ``FieldError``
naming the field and the reason, and the one lookup that genuinely matters —
equality — works properly through an opt-in blind index or not at all.

The blind index's own leak is pinned too, because it is the thing a reader must
weigh: two rows holding the same value get the same index, so **equality is
observable** to anyone who can read the column. That is fine for an email
address and wrong for a national ID with a known format and a small search
space.
"""
from __future__ import annotations

import pytest
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import connection
from django.db import models as django_models
from django.test.utils import isolate_apps

from snapadmin import fields as snap_fields
from snapadmin.encryption import blind_index as bimod
from snapadmin.encryption import keys as keymod


def _material(byte: int) -> bytes:
    return bytes([byte]) * keymod.KEY_BYTES


KEY_OLD = {"id": "old", "key": keymod.encode_key(_material(5))}
KEY_NEW = {"id": "new", "key": keymod.encode_key(_material(6))}


@pytest.fixture
def keyset(settings):
    settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_OLD]}
    keymod.reset_keyset()
    yield keymod.get_keyset()
    keymod.reset_keyset()


@pytest.fixture
def rotated_keyset(settings):
    """A rotation in progress: `new` encrypts, `old` still opens existing rows."""
    settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
    keymod.reset_keyset()
    yield keymod.get_keyset()
    keymod.reset_keyset()


def _make_model():
    with isolate_apps("snapadmin"):
        class Member(django_models.Model):
            email = snap_fields.SnapEncryptedEmailField(blind_index=True)
            secret = snap_fields.SnapEncryptedCharField(max_length=100)
            badge = snap_fields.SnapEncryptedIntegerField(blind_index=True)
            passport = snap_fields.SnapEncryptedCharField(
                max_length=40, blind_index=True, unique=True
            )
            joined = snap_fields.SnapEncryptedDateField()

            class Meta:
                app_label = "snapadmin"

        return Member


@pytest.fixture
def member_model(transactional_db):
    model = _make_model()
    with connection.schema_editor(atomic=False) as editor:
        editor.create_model(model)
    try:
        yield model
    finally:
        with connection.schema_editor(atomic=False) as editor:
            editor.delete_model(model)


AAD = "snapadmin.member.email"


# ─────────────────────────────────────────────────────────────────────────────
# The primitive
# ─────────────────────────────────────────────────────────────────────────────

class TestIndexPrimitive:
    def test_the_same_value_indexes_the_same_way(self, keyset):
        assert bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset) == bimod.index_for_write(
            "a@b.c", aad=AAD, keyset=keyset
        )

    def test_different_values_index_differently(self, keyset):
        assert bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset) != bimod.index_for_write(
            "d@e.f", aad=AAD, keyset=keyset
        )

    def test_the_index_does_not_contain_the_value(self, keyset):
        assert "a@b.c" not in bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset)

    def test_the_index_fits_the_column(self, keyset):
        index = bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset)
        assert len(index) == bimod.INDEX_CHARS
        assert index.isascii()

    def test_the_same_value_in_another_column_indexes_differently(self, keyset):
        """Domain separation: an index cannot be correlated across columns."""
        assert bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset) != bimod.index_for_write(
            "a@b.c", aad="snapadmin.member.secret", keyset=keyset
        )

    def test_a_different_key_indexes_differently(self, keyset, rotated_keyset):
        under_old = bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset)
        under_new = bimod.index_for_write("a@b.c", aad=AAD, keyset=rotated_keyset)
        assert under_old != under_new

    def test_the_index_key_is_not_the_encryption_key(self, keyset):
        """HKDF-derived: the index key must never be the key that decrypts."""
        derived = bimod.derive_index_key(keyset.active, aad=AAD)
        assert derived != keyset.active.material
        assert len(derived) == keymod.KEY_BYTES

    def test_unicode_is_normalised_before_indexing(self, keyset):
        """Composed and decomposed forms of one string are one value."""
        composed, decomposed = "é", "é"
        assert composed != decomposed
        assert bimod.index_for_write(composed, aad=AAD, keyset=keyset) == bimod.index_for_write(
            decomposed, aad=AAD, keyset=keyset
        )

    def test_case_is_significant(self, keyset):
        """Matching Django's own `exact`, which is case-sensitive."""
        assert bimod.index_for_write("A@B.c", aad=AAD, keyset=keyset) != bimod.index_for_write(
            "a@b.c", aad=AAD, keyset=keyset
        )

    def test_candidates_cover_every_key_in_the_keyset(self, rotated_keyset):
        """A lookup during a rotation must find rows indexed under either key."""
        candidates = bimod.index_candidates("a@b.c", aad=AAD, keyset=rotated_keyset)
        assert len(candidates) == 2
        assert bimod.index_for_write("a@b.c", aad=AAD, keyset=rotated_keyset) in candidates

    def test_writing_uses_the_active_key(self, rotated_keyset):
        expected = bimod.index_value(
            "a@b.c", aad=AAD, key=rotated_keyset.active
        )
        assert bimod.index_for_write("a@b.c", aad=AAD, keyset=rotated_keyset) == expected

    def test_without_a_keyset_it_refuses(self, settings, monkeypatch):
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.delenv(keymod.ENV_KEYS, raising=False)
        keymod.reset_keyset()
        with pytest.raises(ImproperlyConfigured):
            bimod.index_for_write("a@b.c", aad=AAD)
        keymod.reset_keyset()


# ─────────────────────────────────────────────────────────────────────────────
# The sibling column
# ─────────────────────────────────────────────────────────────────────────────

class TestSiblingColumn:
    def test_blind_index_adds_the_sibling(self):
        model = _make_model()
        sibling = model._meta.get_field("email_bi")
        # get_field() raises when a field is missing, so "not None" proved
        # nothing — what matters is which field was added and what it points at.
        assert type(sibling) is snap_fields.SnapBlindIndexField
        assert sibling.source_field == "email"

    def test_the_sibling_is_indexed_and_hidden_from_forms(self):
        model = _make_model()
        sibling = model._meta.get_field("email_bi")
        assert sibling.db_index is True
        assert sibling.editable is False

    def test_no_sibling_without_the_kwarg(self):
        model = _make_model()
        assert "secret_bi" not in {f.name for f in model._meta.get_fields()}

    def test_the_field_names_its_sibling(self):
        model = _make_model()
        assert model._meta.get_field("email").blind_index_name == "email_bi"
        assert model._meta.get_field("secret").blind_index_name is None

    def test_deconstruct_round_trips_the_kwarg(self):
        field = snap_fields.SnapEncryptedEmailField(blind_index=True)
        assert field.deconstruct()[3]["blind_index"] is True

    def test_deconstruct_omits_the_kwarg_when_off(self):
        assert "blind_index" not in snap_fields.SnapEncryptedEmailField().deconstruct()[3]

    def test_the_sibling_is_not_added_twice(self):
        """Rebuilding a model from a migration must not duplicate the column.

        A historical model is rendered from a field list that already contains
        the sibling; the encrypted field would then contribute a second one.
        """
        with isolate_apps("snapadmin"):
            class Rebuilt(django_models.Model):
                email = snap_fields.SnapEncryptedEmailField(blind_index=True)
                email_bi = snap_fields.SnapBlindIndexField(max_length=64)

                class Meta:
                    app_label = "snapadmin"

            assert [f.name for f in Rebuilt._meta.local_fields].count("email_bi") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Lookups that must refuse
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestRefusedLookups:
    @pytest.mark.parametrize(
        "lookup", ["icontains", "contains", "gt", "gte", "lt", "lte", "startswith",
                   "endswith", "iexact", "regex", "range"],
    )
    def test_an_unsupported_lookup_raises(self, member_model, keyset, lookup):
        with pytest.raises(FieldError) as exc:
            list(member_model.objects.filter(**{f"secret__{lookup}": "x"}))
        message = str(exc.value)
        assert "secret" in message
        assert lookup in message

    def test_the_error_explains_itself_and_names_the_way_out(self, member_model, keyset):
        with pytest.raises(FieldError) as exc:
            list(member_model.objects.filter(secret__icontains="x"))
        message = str(exc.value)
        assert "encrypted" in message.lower()
        assert "blind_index" in message

    def test_exact_without_a_blind_index_refuses_rather_than_matching_nothing(
        self, member_model, keyset
    ):
        """The whole point: an empty result would look like 'no such row'."""
        member_model.objects.create(email="a@b.c", secret="hunter2", badge=7)
        with pytest.raises(FieldError):
            list(member_model.objects.filter(secret="hunter2"))

    def test_isnull_is_always_allowed(self, member_model, keyset):
        """NULL is never encrypted, so the database can still see it."""
        member_model.objects.create(email="a@b.c", secret=None, badge=7)
        assert member_model.objects.filter(secret__isnull=True).count() == 1
        assert member_model.objects.filter(secret__isnull=False).count() == 0

    def test_an_unsupported_lookup_on_an_indexed_field_still_refuses(
        self, member_model, keyset
    ):
        """A blind index buys equality, not ordering or substring search."""
        with pytest.raises(FieldError):
            list(member_model.objects.filter(email__icontains="@"))


# ─────────────────────────────────────────────────────────────────────────────
# Equality through the blind index
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestEqualityLookups:
    def test_exact_finds_the_row(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        member_model.objects.create(email="d@e.f", secret="s", badge=2)
        found = member_model.objects.get(email="a@b.c")
        assert found.badge == 1

    def test_exact_does_not_find_a_different_value(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        assert member_model.objects.filter(email="nobody@example.org").count() == 0

    def test_explicit_exact_works_too(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        assert member_model.objects.filter(email__exact="a@b.c").count() == 1

    def test_in_finds_every_match(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        member_model.objects.create(email="d@e.f", secret="s", badge=2)
        member_model.objects.create(email="g@h.i", secret="s", badge=3)
        found = member_model.objects.filter(email__in=["a@b.c", "g@h.i"])
        assert sorted(found.values_list("badge", flat=True)) == [1, 3]

    def test_an_empty_in_matches_nothing(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        assert member_model.objects.filter(email__in=[]).count() == 0

    def test_exclude_works(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        member_model.objects.create(email="d@e.f", secret="s", badge=2)
        assert member_model.objects.exclude(email="a@b.c").get().badge == 2

    def test_a_non_text_field_indexes_its_natural_value(self, member_model, keyset):
        member_model.objects.create(email="a@b.c", secret="s", badge=99)
        assert member_model.objects.filter(badge=99).count() == 1
        assert member_model.objects.filter(badge=98).count() == 0

    def test_the_query_never_carries_the_plaintext(self, member_model, keyset):
        query = str(member_model.objects.filter(email="a@b.c").query)
        assert "a@b.c" not in query
        assert "email_bi" in query

    def test_the_sibling_column_holds_the_index(self, member_model, keyset):
        row = member_model.objects.create(email="a@b.c", secret="s", badge=1)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT email_bi FROM {member_model._meta.db_table} WHERE id = %s", [row.pk]
            )
            stored = cursor.fetchone()[0]
        assert stored == bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset)

    def test_a_null_value_indexes_to_null(self, member_model, keyset):
        row = member_model.objects.create(email=None, secret="s", badge=1)
        row.refresh_from_db()
        assert row.email_bi is None

    def test_the_index_updates_when_the_value_changes(self, member_model, keyset):
        row = member_model.objects.create(email="a@b.c", secret="s", badge=1)
        row.email = "d@e.f"
        row.save()
        assert member_model.objects.filter(email="d@e.f").count() == 1
        assert member_model.objects.filter(email="a@b.c").count() == 0


@pytest.mark.django_db(transaction=True)
class TestRotation:
    def test_a_row_indexed_under_the_old_key_is_still_found(
        self, member_model, keyset, settings
    ):
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        keymod.reset_keyset()
        assert member_model.objects.filter(email="a@b.c").count() == 1

    def test_new_rows_index_under_the_new_key(self, member_model, keyset, settings):
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        keymod.reset_keyset()
        member_model.objects.create(email="a@b.c", secret="s", badge=1)
        assert member_model.objects.filter(email="a@b.c").count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# The leak the blind index buys its usefulness with
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestTheDocumentedLeak:
    def test_equal_values_are_visibly_equal_in_the_column(self, member_model, keyset):
        """Stated out loud: a blind index makes equality observable.

        Anyone who can read the table can tell which rows share a value —
        acceptable for an email address, wrong for a national ID with a known
        format and a small search space. The docs say so; this pins it as real
        behaviour rather than a warning nobody checked.
        """
        first = member_model.objects.create(email="same@example.org", secret="s", badge=1)
        second = member_model.objects.create(email="same@example.org", secret="s", badge=2)
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.email_bi == second.email_bi

    def test_the_ciphertext_itself_still_differs(self, member_model, keyset):
        """Only the index repeats — the encrypted column keeps its fresh nonce."""
        first = member_model.objects.create(email="same@example.org", secret="s", badge=1)
        second = member_model.objects.create(email="same@example.org", secret="s", badge=2)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT email FROM {member_model._meta.db_table} ORDER BY id"
            )
            stored = [r[0] for r in cursor.fetchall()]
        assert stored[0] != stored[1]
        assert first.pk != second.pk


# ─────────────────────────────────────────────────────────────────────────────
# Uniqueness moves to the index
# ─────────────────────────────────────────────────────────────────────────────

class TestUnique:
    def test_the_constraint_moves_to_the_sibling(self):
        model = _make_model()
        assert model._meta.get_field("passport").unique is False
        assert model._meta.get_field("passport_bi").unique is True

    def test_the_sibling_is_not_also_indexed_twice(self):
        """A unique constraint already provides the index."""
        assert _make_model()._meta.get_field("passport_bi").db_index is False

    def test_deconstruct_still_reports_what_the_model_declared(self):
        """Otherwise a migration would rebuild the pair without the constraint."""
        model = _make_model()
        kwargs = model._meta.get_field("passport").deconstruct()[3]
        assert kwargs["unique"] is True
        assert kwargs["blind_index"] is True


@pytest.mark.django_db(transaction=True)
class TestUniqueEnforcement:
    def test_a_duplicate_value_is_rejected_by_the_database(self, member_model, keyset):
        import datetime

        from django.db import IntegrityError

        member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1", joined=datetime.date(2026, 1, 1)
        )
        with pytest.raises(IntegrityError):
            member_model.objects.create(
                email="d@e.f", secret="s", badge=2, passport="X1",
                joined=datetime.date(2026, 1, 2),
            )

    def test_validate_unique_sees_the_index_before_the_save(self, member_model, keyset):
        """The index is derived on read, so Django's pre-flight check is correct."""
        import datetime

        from django.core.exceptions import ValidationError

        member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1", joined=datetime.date(2026, 1, 1)
        )
        duplicate = member_model(
            email="d@e.f", secret="s", badge=2, passport="X1",
            joined=datetime.date(2026, 1, 2),
        )
        with pytest.raises(ValidationError):
            duplicate.validate_unique()


# ─────────────────────────────────────────────────────────────────────────────
# The edges of the sibling column
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSiblingEdges:
    def test_a_deferred_source_falls_back_to_the_stored_index(self, member_model, keyset):
        import datetime

        row = member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1", joined=datetime.date(2026, 1, 1)
        )
        partial = member_model.objects.only("id", "email_bi").get(pk=row.pk)
        assert "email" not in partial.__dict__
        assert partial.email_bi == bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset)

    def test_a_direct_assignment_never_beats_the_derived_value(
        self, member_model, keyset
    ):
        """The index cannot be talked out of agreeing with the value it indexes."""
        import datetime

        row = member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1", joined=datetime.date(2026, 1, 1)
        )
        row.email_bi = "a value someone set by hand"
        assert row.email_bi == bimod.index_for_write("a@b.c", aad=AAD, keyset=keyset)

    def test_a_sibling_with_no_source_returns_the_stored_value(self):
        with isolate_apps("snapadmin"):
            class Orphan(django_models.Model):
                loose = snap_fields.SnapBlindIndexField()

                class Meta:
                    app_label = "snapadmin"

        instance = Orphan(loose="stored")
        assert instance.loose == "stored"

    def test_a_sibling_naming_a_missing_source_returns_the_stored_value(self):
        with isolate_apps("snapadmin"):
            class Dangling(django_models.Model):
                loose = snap_fields.SnapBlindIndexField(source_field="gone")

                class Meta:
                    app_label = "snapadmin"

        instance = Dangling(loose="stored")
        assert instance.loose == "stored"

    def test_the_sibling_deconstructs_with_its_source(self):
        field = snap_fields.SnapBlindIndexField(source_field="email")
        assert field.deconstruct()[3]["source_field"] == "email"

    def test_a_bare_sibling_deconstructs_without_one(self):
        assert "source_field" not in snap_fields.SnapBlindIndexField().deconstruct()[3]

    def test_no_keyset_leaves_the_index_unset_rather_than_raising(
        self, member_model, settings, monkeypatch
    ):
        """Reading an attribute is the wrong place to report a missing key.

        The write path refuses loudly on its own, so nothing wrong can reach
        the column — but blowing up on attribute access would surface the
        misconfiguration somewhere nobody can act on it.
        """
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.delenv(keymod.ENV_KEYS, raising=False)
        keymod.reset_keyset()
        assert member_model._meta.get_field("email").blind_index_of("a@b.c") is None
        keymod.reset_keyset()

    def test_a_field_without_a_blind_index_has_no_index_value(self, member_model, keyset):
        assert member_model._meta.get_field("secret").blind_index_of("x") is None


@pytest.mark.django_db(transaction=True)
class TestLookupEdges:
    def test_none_inside_an_in_list_is_skipped(self, member_model, keyset):
        import datetime

        member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1", joined=datetime.date(2026, 1, 1)
        )
        assert member_model.objects.filter(email__in=["a@b.c", None]).count() == 1

    def test_an_in_list_of_only_none_matches_nothing(self, member_model, keyset):
        import datetime

        member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1", joined=datetime.date(2026, 1, 1)
        )
        assert member_model.objects.filter(email__in=[None]).count() == 0

    def test_an_expression_that_is_not_a_column_is_refused(self, member_model, keyset):
        """The rewrite needs a real column to swap for its sibling."""
        from django.db.models import Value
        from django.db.models.functions import Coalesce

        with pytest.raises(FieldError):
            list(
                member_model.objects.annotate(
                    fallback=Coalesce("email", Value("none@example.org"))
                ).filter(fallback="a@b.c")
            )

    def test_a_transform_is_refused_with_the_same_explanation(self, member_model, keyset):
        """`joined__year__gt` asks for a lookup before it asks for a transform."""
        with pytest.raises(FieldError) as exc:
            list(member_model.objects.filter(joined__year__gt=2020))
        assert "encrypted" in str(exc.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# The two ways the index could silently fall out of step
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def scoped_model(transactional_db):
    """A ``SnapModel`` — the route on which partial saves keep the index in step."""
    from snapadmin import models as snap_models

    with isolate_apps("snapadmin"):
        class Scoped(snap_models.SnapModel):
            email = snap_fields.SnapEncryptedEmailField(blind_index=True)
            label = snap_fields.SnapCharField(max_length=40)

            class Meta:
                app_label = "snapadmin"

        with connection.schema_editor(atomic=False) as editor:
            editor.create_model(Scoped)
        try:
            yield Scoped
        finally:
            with connection.schema_editor(atomic=False) as editor:
                editor.delete_model(Scoped)


@pytest.mark.django_db(transaction=True)
class TestPartialSaves:
    def test_update_fields_carries_the_index_along(self, scoped_model, keyset):
        """`save(update_fields=["email"])` writes exactly what it is told.

        Without help that moves the ciphertext and leaves the index behind, and
        the row silently stops being findable by its own value — the
        invisible-data failure the whole lookup guard exists to prevent.
        """
        row = scoped_model.objects.create(email="a@b.c", label="first")
        row.email = "moved@example.org"
        row.save(update_fields=["email"])

        assert scoped_model.objects.filter(email="moved@example.org").count() == 1
        assert scoped_model.objects.filter(email="a@b.c").count() == 0

    def test_update_fields_without_the_encrypted_field_is_untouched(
        self, scoped_model, keyset
    ):
        row = scoped_model.objects.create(email="a@b.c", label="first")
        row.label = "second"
        row.save(update_fields=["label"])
        assert scoped_model.objects.filter(email="a@b.c").count() == 1

    def test_a_full_save_needs_no_help(self, scoped_model, keyset):
        row = scoped_model.objects.create(email="a@b.c", label="first")
        row.email = "moved@example.org"
        row.save()
        assert scoped_model.objects.filter(email="moved@example.org").count() == 1

    def test_a_plain_django_model_does_not_get_this(self, member_model, keyset):
        """Pinned as a known limit, so it is a documented edge and not a shock.

        The hook is ``SnapModel.save()``; a plain ``models.Model`` that merely
        uses the field types has no such override, and Django offers no
        field-level say in which columns a partial save writes. Such a model
        must list the sibling itself, or be repaired with
        ``snapadmin_encrypt_fields --reindex``.
        """
        import datetime

        row = member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1",
            joined=datetime.date(2026, 1, 1),
        )
        row.email = "moved@example.org"
        row.save(update_fields=["email"])
        assert member_model.objects.filter(email="moved@example.org").count() == 0

        row.save(update_fields=["email", "email_bi"])
        assert member_model.objects.filter(email="moved@example.org").count() == 1


@pytest.mark.django_db(transaction=True)
class TestSubqueryLookups:
    def test_an_in_over_a_queryset_explains_itself(self, member_model, keyset):
        """The index has to be computed in Python, so a subquery cannot work.

        Left alone this surfaced as `TypeError: 'Query' object is not iterable`
        from inside SQL compilation, which says nothing about encryption.
        """
        inner = member_model.objects.values_list("secret", flat=True)
        with pytest.raises(FieldError) as exc:
            list(member_model.objects.filter(email__in=inner))
        message = str(exc.value)
        assert "encrypted" in message
        assert "subquery" in message

    def test_a_plain_list_still_works(self, member_model, keyset):
        import datetime

        member_model.objects.create(
            email="a@b.c", secret="s", badge=1, passport="X1",
            joined=datetime.date(2026, 1, 1),
        )
        assert member_model.objects.filter(email__in=["a@b.c"]).count() == 1

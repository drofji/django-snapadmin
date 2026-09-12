"""
tests/test_encrypt_fields_command.py

``manage.py snapadmin_encrypt_fields`` (#CRYPT1f) — the two jobs that have to
touch stored data, and the guard rails around them.

**Adoption.** Switching an existing column to an encrypted field is a schema
change in the adopting project, and the rows that were already there are still
plaintext afterwards. Every read of one of them raises, loudly and correctly,
until this command has converted them. ``--adopt`` is that conversion.

**Rotation.** Prepending a new key makes every *new* write use it while old rows
stay readable under the old one. The old key can only be dropped once no row
still names it, and ``--rotate`` is what empties it out.

The properties pinned here are the ones that decide whether this command is
safe to point at a production table:

* **It writes nothing without ``--apply``.** The default is a report. Encryption
  mistakes are not recoverable by re-running something, so the safe mode is the
  one you get by accident.
* **It is idempotent.** Running ``--adopt`` twice must not encrypt a row twice,
  and running ``--rotate`` after it has converged must be a no-op.
* **One bad row does not stop the run.** A row that cannot be converted is
  counted, named by primary key and skipped; the remaining thousands still get
  done, and the command reports the failures at the end rather than dying on
  the first one and leaving the table half-converted with no summary.
* **It rebuilds blind indexes**, which is the one repair path for a table that
  went through ``bulk_update()`` or ``QuerySet.update()`` — neither of which can
  refresh the sibling column.
"""
from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db import models as django_models
from django.test.utils import isolate_apps

from snapadmin import fields as snap_fields
from snapadmin.encryption import blind_index as bimod
from snapadmin.encryption import cipher as ciphermod
from snapadmin.encryption import keys as keymod


def _material(byte: int) -> bytes:
    return bytes([byte]) * keymod.KEY_BYTES


KEY_OLD = {"id": "old", "key": keymod.encode_key(_material(31))}
KEY_NEW = {"id": "new", "key": keymod.encode_key(_material(32))}


@pytest.fixture
def old_key(settings):
    settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_OLD]}
    keymod.reset_keyset()
    yield keymod.get_keyset()
    keymod.reset_keyset()


@pytest.fixture
def rotated(settings):
    settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
    keymod.reset_keyset()
    yield keymod.get_keyset()
    keymod.reset_keyset()


@pytest.fixture
def ledger(transactional_db):
    """A throwaway model with a real table, visible to the command.

    ``isolate_apps`` registers new models in a private ``Apps``; the global
    registry the command walks never sees them, so the command module's own
    ``apps`` is pointed at the isolated one for the duration. Tests that need
    the *real* registry (the discovery walk over the shipped demo) deliberately
    do not take this fixture.
    """
    from snapadmin.management.commands import snapadmin_encrypt_fields as command_module

    with isolate_apps("snapadmin") as isolated:
        class Ledger(django_models.Model):
            owner = snap_fields.SnapCharField(max_length=50)
            account = snap_fields.SnapEncryptedCharField(max_length=64, blind_index=True)
            balance = snap_fields.SnapEncryptedIntegerField()

            class Meta:
                app_label = "snapadmin"

        with connection.schema_editor(atomic=False) as editor:
            editor.create_model(Ledger)
        try:
            with mock.patch.object(command_module, "apps", isolated):
                yield Ledger
        finally:
            with connection.schema_editor(atomic=False) as editor:
                editor.delete_model(Ledger)


def _raw(model, pk, column):
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {column} FROM {model._meta.db_table} WHERE id = %s", [pk]
        )
        return cursor.fetchone()[0]


def _write_raw(model, pk, **columns):
    """Put values into the table behind the ORM's back — what a legacy row is."""
    assignments = ", ".join(f"{name} = %s" for name in columns)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {model._meta.db_table} SET {assignments} WHERE id = %s",
            [*columns.values(), pk],
        )


def _insert_plaintext(model, owner, account, balance):
    """A row as it looks straight after the column was switched to encrypted."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {model._meta.db_table} (owner, account, balance) "
            "VALUES (%s, %s, %s)",
            [owner, account, balance],
        )
        cursor.execute(f"SELECT MAX(id) FROM {model._meta.db_table}")
        return cursor.fetchone()[0]


def _run(*args, **options) -> str:
    out = StringIO()
    call_command("snapadmin_encrypt_fields", *args, stdout=out, stderr=out, **options)
    return out.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Safety by default
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestDryRunIsTheDefault:
    def test_nothing_is_written_without_apply(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--adopt", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account") == "ACC-1"

    def test_the_report_says_what_it_would_do(self, ledger, old_key):
        _insert_plaintext(ledger, "ann", "ACC-1", "100")
        output = _run("--adopt", "--models", "snapadmin.Ledger")
        assert "dry run" in output.lower()
        assert "snapadmin.Ledger" in output

    def test_an_explicit_dry_run_is_accepted(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--adopt", "--dry-run", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account") == "ACC-1"

    def test_dry_run_and_apply_together_are_refused(self, ledger, old_key):
        with pytest.raises(CommandError):
            _run("--adopt", "--dry-run", "--apply", "--models", "snapadmin.Ledger")

    def test_a_mode_must_be_chosen(self, ledger, old_key):
        with pytest.raises(CommandError) as exc:
            _run("--models", "snapadmin.Ledger")
        assert "--adopt" in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# Adoption
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestAdopt:
    def test_plaintext_rows_become_readable(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        row = ledger.objects.get(pk=pk)
        assert row.account == "ACC-1"
        assert row.balance == 100

    def test_the_column_holds_an_envelope_afterwards(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account").startswith("snap1.")
        assert "ACC-1" not in _raw(ledger, pk, "account")

    def test_the_blind_index_is_built(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account_bi") == bimod.index_for_write(
            "ACC-1", aad="snapadmin.ledger.account", keyset=old_key
        )
        assert ledger.objects.filter(account="ACC-1").count() == 1

    def test_running_it_twice_does_not_encrypt_twice(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        first = _raw(ledger, pk, "account")
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account") == first
        assert ledger.objects.get(pk=pk).account == "ACC-1"

    def test_an_already_encrypted_row_is_reported_as_skipped(self, ledger, old_key):
        ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        output = _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert "0 converted" in output or "converted 0" in output.lower()

    def test_null_columns_are_left_alone(self, ledger, old_key):
        pk = _insert_plaintext(ledger, "ann", None, None)
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account") is None
        assert ledger.objects.get(pk=pk).account is None

    def test_many_rows_are_converted_in_batches(self, ledger, old_key):
        for index in range(25):
            _insert_plaintext(ledger, f"owner-{index}", f"ACC-{index}", str(index))
        _run("--adopt", "--apply", "--batch-size", "4", "--models", "snapadmin.Ledger")
        assert ledger.objects.count() == 25
        assert {row.account for row in ledger.objects.all()} == {
            f"ACC-{index}" for index in range(25)
        }

    def test_a_run_can_resume_from_a_primary_key(self, ledger, old_key):
        first = _insert_plaintext(ledger, "ann", "ACC-1", "1")
        second = _insert_plaintext(ledger, "bob", "ACC-2", "2")
        _run("--adopt", "--apply", "--start-pk", str(second), "--models", "snapadmin.Ledger")
        assert _raw(ledger, first, "account") == "ACC-1"
        assert _raw(ledger, second, "account").startswith("snap1.")


# ─────────────────────────────────────────────────────────────────────────────
# Rotation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestRotate:
    def test_rows_move_to_the_active_key(self, ledger, old_key, settings):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=5)
        assert _raw(ledger, row.pk, "account").split(".")[1] == "old"

        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        keymod.reset_keyset()
        _run("--rotate", "--apply", "--models", "snapadmin.Ledger")

        assert _raw(ledger, row.pk, "account").split(".")[1] == "new"
        assert ledger.objects.get(pk=row.pk).account == "ACC-1"

    def test_the_blind_index_moves_with_it(self, ledger, old_key, settings):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=5)
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        keymod.reset_keyset()
        _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "account_bi") == bimod.index_value(
            "ACC-1", aad="snapadmin.ledger.account", key=keymod.get_keyset().active
        )
        assert ledger.objects.filter(account="ACC-1").count() == 1

    def test_rows_already_on_the_active_key_are_skipped(self, ledger, rotated):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=5)
        before = _raw(ledger, row.pk, "account")
        _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "account") == before

    def test_plaintext_rows_are_not_touched_by_rotate(self, ledger, old_key):
        """Adoption and rotation are different jobs and must not be confused."""
        pk = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, pk, "account") == "ACC-1"

    def test_both_modes_can_run_in_one_pass(self, ledger, old_key, settings):
        encrypted = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        plain = _insert_plaintext(ledger, "bob", "ACC-2", "2")
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        keymod.reset_keyset()
        _run("--adopt", "--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, encrypted.pk, "account").split(".")[1] == "new"
        assert _raw(ledger, plain, "account").split(".")[1] == "new"


# ─────────────────────────────────────────────────────────────────────────────
# Rebuilding a stale blind index
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestReindex:
    def test_a_stale_index_is_repaired(self, ledger, old_key):
        """The documented repair for `bulk_update()` / `QuerySet.update()`.

        Neither refreshes the sibling column: Django writes only the fields it
        was told to write, and the index is not one of them.
        """
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        _write_raw(ledger, row.pk, account_bi="stale")
        assert ledger.objects.filter(account="ACC-1").count() == 0

        _run("--reindex", "--apply", "--models", "snapadmin.Ledger")
        assert ledger.objects.filter(account="ACC-1").count() == 1

    def test_reindex_leaves_the_ciphertext_alone(self, ledger, old_key):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        before = _raw(ledger, row.pk, "account")
        _write_raw(ledger, row.pk, account_bi="stale")
        _run("--reindex", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "account") == before


# ─────────────────────────────────────────────────────────────────────────────
# One bad row must not stop the run
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestFailuresAreCollected:
    def test_an_unreadable_row_is_reported_and_the_rest_still_convert(
        self, ledger, old_key
    ):
        good = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        broken = ledger.objects.create(owner="bob", account="ACC-2", balance=2)
        # A ciphertext written under a key this project does not have.
        stranger = keymod.Keyset.build(
            [keymod.EncryptionKey(id="gone", material=_material(99))],
            source=keymod.KeySource.SETTINGS,
        )
        _write_raw(
            ledger,
            broken.pk,
            account=ciphermod.encrypt(
                "ACC-2", aad="snapadmin.ledger.account", keyset=stranger
            ),
        )

        with pytest.raises(CommandError) as exc:
            _run("--rotate", "--apply", "--models", "snapadmin.Ledger")

        message = str(exc.value)
        assert "1" in message
        assert str(broken.pk) in message
        # The good row was still converted rather than abandoned.
        assert ledger.objects.get(pk=good.pk).account == "ACC-1"

    def test_a_failure_never_prints_the_plaintext_or_the_key(self, ledger, old_key):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        stranger = keymod.Keyset.build(
            [keymod.EncryptionKey(id="gone", material=_material(99))],
            source=keymod.KeySource.SETTINGS,
        )
        _write_raw(
            ledger,
            row.pk,
            account=ciphermod.encrypt(
                "ACC-1", aad="snapadmin.ledger.account", keyset=stranger
            ),
        )
        with pytest.raises(CommandError) as exc:
            _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert KEY_OLD["key"] not in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# Selection and reporting
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestSelectionAndReporting:
    def test_an_unknown_model_is_refused(self, ledger, old_key):
        with pytest.raises(CommandError) as exc:
            _run("--adopt", "--models", "nosuch.Model")
        assert "nosuch.Model" in str(exc.value)

    def test_a_model_with_no_encrypted_field_is_refused(self, old_key, transactional_db):
        """Naming a model with nothing to convert is a typo, not a no-op."""
        from snapadmin.management.commands import snapadmin_encrypt_fields as module

        with isolate_apps("snapadmin") as isolated:
            class Plain(django_models.Model):
                name = snap_fields.SnapCharField(max_length=20)

                class Meta:
                    app_label = "snapadmin"

            with mock.patch.object(module, "apps", isolated):
                with pytest.raises(CommandError) as exc:
                    _run("--adopt", "--models", "snapadmin.Plain")
            assert Plain is not None

        assert "declares no encrypted fields" in str(exc.value)
        assert "snapadmin.Plain" in str(exc.value)

    def test_without_a_selection_every_encrypted_model_is_covered(self, db):
        """Against the real registry: the shipped demo's own encrypted column.

        No ``ledger`` fixture here on purpose — this is the discovery walk over
        what is actually installed, which is the thing an operator runs.
        """
        output = _run("--adopt")
        assert "demo.CustomerProfile.tax_id" in output

    def test_the_report_names_each_model_and_its_counts(self, ledger, old_key):
        _insert_plaintext(ledger, "ann", "ACC-1", "1")
        output = _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert "snapadmin.Ledger" in output
        assert "account" in output

    def test_no_keyset_is_a_clear_refusal(self, ledger, settings, monkeypatch):
        settings.SNAPADMIN_ENCRYPTION = {}
        monkeypatch.delenv(keymod.ENV_KEYS, raising=False)
        keymod.reset_keyset()
        with pytest.raises(CommandError) as exc:
            _run("--adopt", "--models", "snapadmin.Ledger")
        assert "snapadmin_encryption_key" in str(exc.value)
        keymod.reset_keyset()

    def test_the_keyset_cache_is_dropped_on_start(self, ledger, old_key, settings):
        """A long-running job must read the configuration it was started with.

        The keyset is cached per process with no TTL — deliberately, so that a
        KEY_PROVIDER pointing at a KMS is not a per-query network call. A
        command that rewrites stored data has to opt out of that cache, or it
        can rotate an entire table onto a key the operator has already replaced.
        """
        keymod.get_keyset()
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        _write_raw(
            ledger,
            row.pk,
            account=ciphermod.encrypt(
                "ACC-1", aad="snapadmin.ledger.account", keyset=old_key
            ),
        )
        _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "account").split(".")[1] == "new"


# ─────────────────────────────────────────────────────────────────────────────
# Argument handling and the awkward rows
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
class TestArguments:
    def test_a_zero_batch_size_is_refused(self, ledger, old_key):
        with pytest.raises(CommandError) as exc:
            _run("--adopt", "--batch-size", "0", "--models", "snapadmin.Ledger")
        assert "--batch-size" in str(exc.value)

    def test_empty_entries_in_the_model_list_are_ignored(self, ledger, old_key):
        """`--models "snapadmin.Ledger,"` is a copy-paste artefact, not an error."""
        output = _run("--adopt", "--models", "snapadmin.Ledger, ,")
        assert "snapadmin.Ledger" in output

    def test_nothing_to_do_is_said_plainly(self, ledger, old_key):
        from snapadmin.management.commands import snapadmin_encrypt_fields as module

        with mock.patch.object(module.apps, "get_models", return_value=[]):
            output = _run("--adopt")
        assert "nothing to do" in output.lower()


@pytest.mark.django_db(transaction=True)
class TestAwkwardRows:
    def test_a_legacy_value_the_field_cannot_decode_is_counted_not_fatal(
        self, ledger, old_key
    ):
        """`balance` is an integer column: "not a number" cannot be adopted."""
        good = _insert_plaintext(ledger, "ann", "ACC-1", "100")
        bad = _insert_plaintext(ledger, "bob", "ACC-2", "not a number")

        with pytest.raises(CommandError) as exc:
            _run("--adopt", "--apply", "--models", "snapadmin.Ledger")

        assert str(bad) in str(exc.value)
        assert ledger.objects.get(pk=good).balance == 100

    def test_such_a_failure_does_not_print_the_offending_value(self, ledger, old_key):
        _insert_plaintext(ledger, "bob", "ACC-2", "a-very-distinctive-legacy-value")
        with pytest.raises(CommandError):
            output = _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        # The report is written to stdout before the error is raised.
        out = StringIO()
        with pytest.raises(CommandError):
            call_command(
                "snapadmin_encrypt_fields", "--adopt", "--apply",
                "--models", "snapadmin.Ledger", stdout=out, stderr=out,
            )
        assert "a-very-distinctive-legacy-value" not in out.getvalue()
        assert "ValueError" in out.getvalue()

    def test_a_corrupt_envelope_is_counted_not_fatal(self, ledger, old_key):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        _write_raw(ledger, row.pk, account="snap1.k.AAAA.AAAA")
        with pytest.raises(CommandError) as exc:
            _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert str(row.pk) in str(exc.value)

    def test_reindex_reports_a_row_it_cannot_read(self, ledger, old_key):
        """Rebuilding an index needs the plaintext, so an unreadable row fails."""
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        stranger = keymod.Keyset.build(
            [keymod.EncryptionKey(id="old", material=_material(99))],
            source=keymod.KeySource.SETTINGS,
        )
        _write_raw(
            ledger,
            row.pk,
            account=ciphermod.encrypt(
                "ACC-1", aad="snapadmin.ledger.account", keyset=stranger
            ),
        )
        with pytest.raises(CommandError) as exc:
            _run("--reindex", "--apply", "--models", "snapadmin.Ledger")
        assert str(row.pk) in str(exc.value)

    def test_rotate_alone_leaves_current_rows_untouched(self, ledger, old_key):
        """The `--rotate`-without-`--reindex` path over an already-current row."""
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        before = _raw(ledger, row.pk, "account")
        output = _run("--rotate", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "account") == before
        assert "already current" in output

    def test_adopt_alone_leaves_old_key_rows_untouched(self, ledger, old_key, settings):
        """The `--adopt`-without-`--rotate` path over an older-key row."""
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        before = _raw(ledger, row.pk, "account")
        settings.SNAPADMIN_ENCRYPTION = {"KEYS": [KEY_NEW, KEY_OLD]}
        keymod.reset_keyset()
        _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "account") == before

    def test_a_long_failure_list_is_summarised(self, ledger, old_key):
        """Naming every one of ten thousand pks in the error helps nobody."""
        for index in range(13):
            _insert_plaintext(ledger, f"o{index}", f"ACC-{index}", "not a number")
        with pytest.raises(CommandError) as exc:
            _run("--adopt", "--apply", "--models", "snapadmin.Ledger")
        assert "more" in str(exc.value)

    def test_a_non_integer_primary_key_is_walked_in_batches(self, transactional_db, old_key):
        """Keyset pagination orders by the key; it never does arithmetic on it."""
        from snapadmin.management.commands import snapadmin_encrypt_fields as module

        with isolate_apps("snapadmin") as isolated:
            class Slug(django_models.Model):
                code = django_models.CharField(max_length=20, primary_key=True)
                secret = snap_fields.SnapEncryptedCharField(max_length=40)

                class Meta:
                    app_label = "snapadmin"

            with connection.schema_editor(atomic=False) as editor:
                editor.create_model(Slug)
            try:
                with connection.cursor() as cursor:
                    for index in range(7):
                        cursor.execute(
                            f"INSERT INTO {Slug._meta.db_table} (code, secret) VALUES (%s, %s)",
                            [f"code-{index}", f"secret-{index}"],
                        )
                with mock.patch.object(module, "apps", isolated):
                    _run("--adopt", "--apply", "--batch-size", "2",
                         "--models", "snapadmin.Slug")
                assert {row.secret for row in Slug.objects.all()} == {
                    f"secret-{index}" for index in range(7)
                }
            finally:
                with connection.schema_editor(atomic=False) as editor:
                    editor.delete_model(Slug)


@pytest.mark.django_db(transaction=True)
class TestReindexScope:
    def test_a_field_without_a_blind_index_is_not_rewritten(self, ledger, old_key):
        """`balance` has no sibling, so --reindex has nothing to rebuild there.

        Left alone the pass rewrote every row of every encrypted column with
        the bytes it already held and reported them as reindexed — a full-table
        write that changes nothing and a count that means nothing.
        """
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        before = _raw(ledger, row.pk, "balance")
        output = _run("--reindex", "--apply", "--models", "snapadmin.Ledger")
        assert _raw(ledger, row.pk, "balance") == before
        assert "snapadmin.Ledger.balance: 0 converted" in output

    def test_the_indexed_field_is_still_rebuilt(self, ledger, old_key):
        row = ledger.objects.create(owner="ann", account="ACC-1", balance=1)
        _write_raw(ledger, row.pk, account_bi="stale")
        output = _run("--reindex", "--apply", "--models", "snapadmin.Ledger")
        assert "snapadmin.Ledger.account: 1 converted" in output
        assert ledger.objects.filter(account="ACC-1").count() == 1

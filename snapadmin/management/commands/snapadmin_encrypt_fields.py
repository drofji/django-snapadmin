"""
Convert stored data for SnapAdmin's encrypted model fields (#CRYPT1f).

    python manage.py snapadmin_encrypt_fields --adopt            # report only
    python manage.py snapadmin_encrypt_fields --adopt --apply    # convert
    python manage.py snapadmin_encrypt_fields --rotate --apply   # re-key
    python manage.py snapadmin_encrypt_fields --reindex --apply  # rebuild indexes

Three jobs, each for a moment the ORM cannot handle on its own because it
operates on rows that are already in the table:

``--adopt``
    Switching an existing column to a ``SnapEncrypted*Field`` is a schema change
    in the adopting project, and the rows that were already there stay
    plaintext. Every read of one raises until it has been converted — correctly,
    since the alternative is a column that silently holds a mixture. This is the
    conversion.

``--rotate``
    Prepending a key makes every new write use it while old rows stay readable
    under the old one. The old key can only be dropped once no row still names
    it; this empties it out.

``--reindex``
    Rebuilds the ``<field>_bi`` blind-index columns, and skips any encrypted
    field that does not have one. The repair path for a table changed by
    ``bulk_update()`` or ``QuerySet.update()``, neither of which refreshes the
    sibling column — Django writes the fields it was told to write, and the
    index is not one of them.

**It writes nothing without ``--apply``.** An encryption mistake on stored data
is not recoverable by re-running something, so the mode you get by accident is
the one that only reports.

Everything here goes through raw SQL rather than the ORM, deliberately: both
directions need to see the column *as stored*, and the whole point of the field
layer is that it never lets you do that. It also keeps the pass off the
instance-construction path, so a table with an unreadable row can still be
walked to find out how many there are.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, router

from snapadmin.encryption import blind_index, cipher
from snapadmin.encryption import keys as encryption_keys
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: Rows read per round trip. Large enough that a million-row table does not
#: become a million queries, small enough that one batch fits comfortably in
#: memory alongside its decrypted values.
DEFAULT_BATCH_SIZE = 500


#: How many failing primary keys the final error names before it gives up and
#: points at the per-row lines above it. A wrapper script or a CI log often
#: captures only the exception, so naming the first few there is worth the
#: noise; naming ten thousand is not.
FAILURES_NAMED_IN_SUMMARY = 10


@dataclass
class FieldReport:
    """What happened to one column."""

    label: str
    adopted: int = 0
    rotated: int = 0
    reindexed: int = 0
    skipped: int = 0
    failures: list[str] = dataclass_field(default_factory=list)
    failed_pks: list[str] = dataclass_field(default_factory=list)

    @property
    def converted(self) -> int:
        return self.adopted + self.rotated + self.reindexed


class Command(BaseCommand):
    help = (
        "Encrypt existing plaintext, re-key ciphertext onto the active encryption "
        "key, or rebuild blind-index columns. Reports only unless --apply is given."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--adopt",
            action="store_true",
            help="Encrypt rows whose column still holds plaintext (after switching "
                 "an existing column to an encrypted field).",
        )
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Re-encrypt rows written under an older key onto the active one, "
                 "so the old key can be dropped from the keyset.",
        )
        parser.add_argument(
            "--reindex",
            action="store_true",
            help="Rebuild the <field>_bi blind-index columns without touching the "
                 "ciphertext. The repair for a table changed by bulk_update() or "
                 "QuerySet.update().",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write. Without it the command reports what it would do "
                 "and changes nothing.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report only — the default. Accepted so a script can say so out loud.",
        )
        parser.add_argument(
            "--models",
            default="",
            help="Comma-separated app_label.ModelName list to restrict the pass to. "
                 "Default: every model with an encrypted field.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Rows per round trip (default: {DEFAULT_BATCH_SIZE}).",
        )
        parser.add_argument(
            "--start-pk",
            default=None,
            help="Resume from this primary key. Rows are walked in ascending pk "
                 "order, so a killed run continues from the last pk it reported.",
        )
        parser.add_argument(
            "--database",
            default=None,
            help="Database alias to work on (default: the write database the "
                 "router picks for each model).",
        )

    # ── entry point ─────────────────────────────────────────────────────────

    def handle(self, *args, **options) -> None:
        adopt = bool(options["adopt"])
        rotate = bool(options["rotate"])
        reindex = bool(options["reindex"])
        apply_changes = bool(options["apply"])
        dry_run = bool(options["dry_run"])

        if not (adopt or rotate or reindex):
            raise CommandError(
                "Nothing to do — choose at least one of --adopt (encrypt existing "
                "plaintext), --rotate (move rows onto the active key) or --reindex "
                "(rebuild blind-index columns)."
            )
        if dry_run and apply_changes:
            raise CommandError(
                "--dry-run and --apply contradict each other. Leave both off to "
                "report, or pass --apply to write."
            )
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be at least 1.")

        # The keyset is cached per process with no TTL so that a KEY_PROVIDER
        # pointing at a KMS is not a per-query network call. A command that
        # rewrites stored data must opt out of that: it may run for an hour, and
        # rotating a whole table onto a key the operator has already replaced is
        # exactly the kind of mistake there is no undo for.
        encryption_keys.reset_keyset()
        try:
            keyset = encryption_keys.require_keyset()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        targets = self._targets(options["models"])
        self.stdout.write(
            f"Keyset: {len(keyset)} key(s), active {keyset.active.id!r}, "
            f"fingerprint {keyset.fingerprint}"
        )
        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "Dry run — nothing will be written. Re-run with --apply to convert."
            ))

        reports: list[FieldReport] = []
        for model, fields in targets:
            for model_field in fields:
                reports.append(self._process_field(
                    model,
                    model_field,
                    adopt=adopt,
                    rotate=rotate,
                    reindex=reindex,
                    apply_changes=apply_changes,
                    batch_size=options["batch_size"],
                    start_pk=options["start_pk"],
                    alias=options["database"],
                ))

        self._report(reports)

    # ── target selection ────────────────────────────────────────────────────

    def _encrypted_fields(self, model) -> list:
        return [f for f in model._meta.get_fields() if getattr(f, "is_snap_encrypted", False)]

    def _targets(self, selection: str) -> list[tuple[type, list]]:
        """The (model, encrypted fields) pairs this run covers."""
        if not selection.strip():
            found = [
                (model, self._encrypted_fields(model))
                for model in apps.get_models()
            ]
            return [(model, fields) for model, fields in found if fields]

        targets: list[tuple[type, list]] = []
        for raw in selection.split(","):
            label = raw.strip()
            if not label:
                continue
            try:
                model = apps.get_model(label)
            except (LookupError, ValueError) as exc:
                raise CommandError(
                    f"{label!r} is not an installed model (expected app_label.ModelName)."
                ) from exc
            fields = self._encrypted_fields(model)
            if not fields:
                raise CommandError(
                    f"{label} declares no encrypted fields, so there is nothing here to "
                    "convert. Remove it from --models."
                )
            targets.append((model, fields))
        return targets

    # ── the pass itself ─────────────────────────────────────────────────────

    def _process_field(
        self,
        model,
        model_field,
        *,
        adopt: bool,
        rotate: bool,
        reindex: bool,
        apply_changes: bool,
        batch_size: int,
        start_pk,
        alias: str | None,
    ) -> FieldReport:
        label = f"{model._meta.label}.{model_field.name}"
        report = FieldReport(label=label)

        using = alias or router.db_for_write(model) or "default"
        connection = connections[using]
        quote = connection.ops.quote_name

        table = quote(model._meta.db_table)
        pk_column = quote(model._meta.pk.column)
        value_column = quote(model_field.column)
        index_name = getattr(model_field, "blind_index_name", None)
        index_column = (
            quote(model._meta.get_field(index_name).column) if index_name else None
        )

        select = f"SELECT {pk_column}, {value_column} FROM {table}"

        # Keyset pagination on the primary key: each batch asks for the rows
        # after the last one it saw. No OFFSET (which re-reads the whole prefix
        # on every batch), no arithmetic on the key (which would restrict this
        # to integer pks — a UUID or a natural key orders perfectly well and
        # cannot be incremented), and a killed run resumes with --start-pk
        # naming the last pk it reported.
        cursor_pk = start_pk
        inclusive = start_pk is not None

        while True:
            with connection.cursor() as cursor:
                if cursor_pk is None:
                    cursor.execute(
                        f"{select} ORDER BY {pk_column} LIMIT %s", [batch_size]
                    )
                else:
                    comparison = ">=" if inclusive else ">"
                    cursor.execute(
                        f"{select} WHERE {pk_column} {comparison} %s "
                        f"ORDER BY {pk_column} LIMIT %s",
                        [cursor_pk, batch_size],
                    )
                rows = cursor.fetchall()
            inclusive = False
            if not rows:
                break

            updates: list[tuple] = []
            for pk, stored in rows:
                plan = self._plan(
                    model_field, stored, adopt=adopt, rotate=rotate, reindex=reindex
                )
                if plan is None:
                    report.skipped += 1
                    continue
                if isinstance(plan, str):
                    # A failure message, already free of secrets.
                    report.failures.append(f"pk={pk}: {plan}")
                    report.failed_pks.append(str(pk))
                    continue

                new_value, action = plan
                report_attr = {"adopt": "adopted", "rotate": "rotated", "reindex": "reindexed"}
                setattr(report, report_attr[action], getattr(report, report_attr[action]) + 1)
                updates.append((pk, new_value))

            if apply_changes and updates:
                self._write(
                    connection, table, pk_column, value_column, index_column,
                    model_field, updates,
                )

            if len(rows) < batch_size:
                break
            cursor_pk = rows[-1][0]

        return report

    def _plan(self, model_field, stored, *, adopt: bool, rotate: bool, reindex: bool):
        """What to do with one stored value.

        Returns ``None`` to skip, a ``str`` describing a failure, or a
        ``(new value, action)`` pair to write.
        """
        if stored is None:
            return None

        aad = model_field.encryption_aad()
        encrypted = cipher.looks_encrypted(stored)

        if not encrypted:
            if not adopt:
                return None
            try:
                canonical = model_field.encode_plaintext(
                    model_field.decode_plaintext(stored)
                )
                return cipher.encrypt(canonical, aad=aad), "adopt"
            except Exception as exc:
                return self._describe(exc)

        try:
            envelope = cipher.Envelope.parse(stored)
        except cipher.DecryptionError as exc:
            return self._describe(exc)

        active = encryption_keys.require_keyset().active.id
        if envelope.key_id == active:
            if not reindex or not getattr(model_field, "blind_index_name", None):
                # Nothing to do: the row is on the active key, and a field with
                # no blind index has no sibling to rebuild. Without the second
                # half, --reindex would rewrite every row of every encrypted
                # column with the bytes it already holds and report them as
                # reindexed — a full table write that changes nothing and a
                # count that means nothing.
                return None
            # Nothing to re-encrypt, but the index may still be stale.
            try:
                cipher.decrypt(stored, aad=aad)
            except (cipher.DecryptionError, ImproperlyConfigured) as exc:
                return self._describe(exc)
            return stored, "reindex"

        if not rotate:
            return None
        try:
            plaintext = cipher.decrypt(stored, aad=aad)
            return cipher.encrypt(plaintext, aad=aad), "rotate"
        except (cipher.DecryptionError, ImproperlyConfigured) as exc:
            return self._describe(exc)

    @staticmethod
    def _describe(exc: Exception) -> str:
        """One line about a failure, with nothing sensitive in it.

        The cipher's own errors are written to carry a key id and a column and
        never key material or a value, so they pass through. Anything else is
        reduced to its type: a ``ValueError`` from a malformed legacy value
        would otherwise put that value straight into the log.
        """
        if isinstance(exc, (cipher.EncryptionError, ImproperlyConfigured)):
            return str(exc)
        return f"{type(exc).__name__} while converting this row (value not shown)"

    def _write(
        self, connection, table, pk_column, value_column, index_column,
        model_field, updates,
    ) -> None:
        """Write one batch, row by row, each in its own statement."""
        with connection.cursor() as cursor:
            for pk, new_value in updates:
                if index_column is None:
                    cursor.execute(
                        f"UPDATE {table} SET {value_column} = %s WHERE {pk_column} = %s",
                        [new_value, pk],
                    )
                    continue
                plaintext = cipher.decrypt(new_value, aad=model_field.encryption_aad())
                index = blind_index.index_for_write(
                    plaintext, aad=model_field.encryption_aad()
                )
                cursor.execute(
                    f"UPDATE {table} SET {value_column} = %s, {index_column} = %s "
                    f"WHERE {pk_column} = %s",
                    [new_value, index, pk],
                )

    # ── reporting ───────────────────────────────────────────────────────────

    def _report(self, reports: list[FieldReport]) -> None:
        if not reports:
            self.stdout.write("No model declares an encrypted field — nothing to do.")
            return

        failures = 0
        failed_pks: list[str] = []
        for report in reports:
            parts = [f"{report.converted} converted"]
            if report.adopted:
                parts.append(f"{report.adopted} adopted")
            if report.rotated:
                parts.append(f"{report.rotated} rotated")
            if report.reindexed:
                parts.append(f"{report.reindexed} reindexed")
            parts.append(f"{report.skipped} already current")
            line = f"  {report.label}: " + ", ".join(parts)
            if report.failures:
                failures += len(report.failures)
                failed_pks.extend(f"{report.label}#{pk}" for pk in report.failed_pks)
                line += f", {len(report.failures)} FAILED"
                self.stdout.write(self.style.ERROR(line))
                for failure in report.failures:
                    self.stdout.write(self.style.ERROR(f"      {failure}"))
            else:
                self.stdout.write(line)

        if failures:
            named = ", ".join(failed_pks[:FAILURES_NAMED_IN_SUMMARY])
            if len(failed_pks) > FAILURES_NAMED_IN_SUMMARY:
                named += f" and {len(failed_pks) - FAILURES_NAMED_IN_SUMMARY} more"
            raise CommandError(
                f"{failures} row(s) could not be converted; every other row was. "
                f"Failed: {named}. Each is explained above — a row naming a key id that "
                "is not in the keyset needs that key put back before it can be moved."
            )

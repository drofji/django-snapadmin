"""
management/commands/snapadmin_subject_request.py

GDPR subject-access request (SAR): export or delete everything SnapAdmin
knows about one data subject, across every model that declares a
``subject_path`` reaching them (#FUT4).

    python manage.py snapadmin_subject_request export \\
        --model demo.Customer --identifier alice@example.com --user admin_username \\
        [--format json|csv] [--recipient AGE_KEY]...

    python manage.py snapadmin_subject_request delete \\
        --model demo.Customer --identifier alice@example.com --user admin_username \\
        [--confirm]

``--model`` names a model with ``is_data_subject = True`` (e.g. ``demo.Customer``);
``--identifier`` is the raw value of that model's ``subject_identifier`` field
(e.g. an email address). Every registered model whose ``subject_path`` reaches
that value — including the subject model itself, since a subject's own path
equals its identifier (#FUT4a) — is swept.

**Both actions are gated on raw-PII access** — ``snapadmin.view_raw_pii``, or the
project's ``SNAPADMIN_PII_ACCESS_CHECK`` when set — the same decision that already
unlocks unmasked PII elsewhere in the admin/API: a SAR export is
unmasked by design (it goes to the subject), so the operator running it must
already be trusted with raw PII. ``--user`` names the operator for the audit
trail and the permission check — running this command is not a bypass of
Django's own permission model, it is a CLI door onto the same one.

**Export** reuses the existing async-export machinery
(:mod:`snapadmin.exporting`) rather than a second exporter: one
``SnapExportJob`` per matched model, ``filters={subject_path: identifier}``,
run synchronously in this process. ``requested_by`` is set to the resolved
operator, so the existing masking bypass for a PII-privileged requester
already produces an unmasked export — no separate "skip masking" branch to
get wrong. A ``manifest.json`` alongside the per-model files lists what was
exported. Pass one or more ``--recipient`` age/SSH public keys to AGE-encrypt
the bundle in place (see :mod:`snapadmin.crypto`) — recommended, since an
unmasked SAR bundle is exactly the kind of artefact that should not sit in
plaintext once written.

**Deletion is dry-run by default** — pass ``--confirm`` to actually delete.
Both modes run the identical pre-flight (a Django ``Collector`` walk over
every matched row, including cascade spillover it discovers on its own — a
more complete picture than the subject_path declarations alone), so the dry
run is a genuine preview of what ``--confirm`` would do, not a separate,
weaker check. If any row is protected (``on_delete=PROTECT`` — the demo's
``Order.customer`` uses it), the whole run refuses up front and deletes
nothing, rather than deleting in dependency order to route around it — safer,
and consistent with dry-run-by-default. A deletion audit entry is written to
``SnapadminAuditLog`` after a successful run; that log is never itself
subject-scoped (it is deliberately outside the general registry, see
``SnapadminAuditLog``'s docstring), so the entry recording a deletion cannot
be swept away by a later one for the same subject.

**Honest limits, stated here and in the docs, not just implied:** this command
cannot reach a backup bundle, an Elasticsearch copy it does not mirror
directly, or any third-party store a project integrates outside SnapAdmin. It
is only as complete as the SnapAdmin registry and every model's own
``subject_path`` declaration.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError


def _identifier_fingerprint(value: str) -> str:
    """A short, stable, non-reversible stand-in for the raw identifier.

    The raw value (an email address, typically) is personal data in its own
    right — it must not be written into the audit trail's object_repr, which
    is not masked the way ``changes`` is. Mirrors ``crypto.fingerprint``'s
    shape for the same reason: enough to correlate two runs, never enough to
    recover the original value.
    """
    return hashlib.sha256(value.strip().encode()).hexdigest()[:12]


class Command(BaseCommand):
    help = (
        "GDPR subject-access request: export or delete everything reachable from one data subject."
    )

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["export", "delete"])
        parser.add_argument(
            "--model",
            required=True,
            help="The subject model, 'app_label.ModelName' (is_data_subject=True), e.g. demo.Customer",
        )
        parser.add_argument(
            "--identifier",
            required=True,
            help="The raw value of the subject model's subject_identifier field (e.g. an email address)",
        )
        parser.add_argument(
            "--user",
            required=True,
            help="Username of the operator running this request — checked for raw-PII access "
            "(snapadmin.view_raw_pii, or SNAPADMIN_PII_ACCESS_CHECK) and recorded in the audit trail",
        )
        parser.add_argument(
            "--format",
            choices=["json", "csv"],
            default="json",
            help="Export file format (default: json). Ignored for 'delete'.",
        )
        parser.add_argument(
            "--recipient",
            action="append",
            default=[],
            help="An age or SSH public key to encrypt the export bundle to. May be "
            "repeated; any one matching identity decrypts independently. "
            "Ignored for 'delete'.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete. Without it, 'delete' only previews what would "
            "be removed. Ignored for 'export'.",
        )

    def handle(self, *args, **options):
        from django.apps import apps

        from snapadmin.registry import get_model_meta, is_registered

        app_label, _, model_name = options["model"].partition(".")
        if not model_name:
            raise CommandError("--model must be 'app_label.ModelName', e.g. demo.Customer")
        try:
            subject_model = apps.get_model(app_label, model_name)
        except LookupError as exc:
            raise CommandError(
                f"--model {options['model']!r} does not resolve to an installed model."
            ) from exc
        if not (
            is_registered(subject_model) and get_model_meta(subject_model, "is_data_subject", False)
        ):
            raise CommandError(
                f"{options['model']} does not declare is_data_subject = True — it is not a "
                "valid subject-access entry point."
            )

        operator = self._resolve_operator(options["user"])
        identifier = options["identifier"]

        self.stderr.write(
            self.style.WARNING(
                "This command reaches only the SnapAdmin registry — it cannot see or touch a "
                "backup bundle, an Elasticsearch copy this model does not mirror, or any "
                "third-party store outside SnapAdmin."
            )
        )

        matches = self._collect_matches(identifier)
        if not matches:
            self.stdout.write("No rows matched this identifier in any subject-scoped model.")
            return

        if options["action"] == "export":
            self._run_export(
                matches,
                identifier=identifier,
                operator=operator,
                export_format=options["format"],
                recipients=options["recipient"],
            )
        else:
            from contextlib import ExitStack

            from snapadmin.tenancy import _background_context

            # The erasure runs inside every matched model's cross-tenant context, not
            # only the lookup: a project's context (row-level security) must cover the
            # DELETEs too, or they remove nothing while the report says they did.
            with ExitStack() as stack:
                for model in matches:
                    stack.enter_context(_background_context(model, "subject_request"))
                self._run_delete(
                    matches, identifier=identifier, operator=operator, confirm=options["confirm"]
                )

    # ------------------------------------------------------------------

    def _resolve_operator(self, username: str):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        try:
            operator = User.objects.get(**{User.USERNAME_FIELD: username})
        except User.DoesNotExist as exc:
            raise CommandError(f"No user named {username!r}.") from exc
        from snapadmin.masking import user_can_view_pii

        # The one decision point every masking surface asks, so a project's own
        # SNAPADMIN_PII_ACCESS_CHECK applies to the operator too (#EXT3t).
        if not user_can_view_pii(operator):
            raise CommandError(
                f"{username!r} may not see raw PII (snapadmin.view_raw_pii, or "
                "SNAPADMIN_PII_ACCESS_CHECK when set) — a subject-access export is unmasked by "
                "design, so the operator running it must already be trusted with raw PII."
            )
        return operator

    def _collect_matches(self, identifier: str) -> dict:
        """Every registered model's rows matching ``identifier`` via its own
        ``subject_path`` — {model: [instance, ...]}. Includes the subject
        model itself (its subject_path always equals its subject_identifier,
        the zero-hop case, #FUT4a)."""
        from django.apps import apps

        from snapadmin.registry import get_model_meta, is_registered
        from snapadmin.tenancy import _background_context

        matches = {}
        for model in apps.get_models():
            if not is_registered(model):
                continue
            path = get_model_meta(model, "subject_path", None)
            if not path:
                continue
            # Every tenant's rows: a subject's data is theirs whatever tenant holds
            # it, and with no request there is no tenant bound — a tenant-scoped
            # model answered nothing, so the export and the erasure missed it (#EXT3r).
            with _background_context(model, "subject_request"):
                rows = list(model._default_manager.filter(**{path: identifier}))
            if rows:
                matches[model] = rows
        return matches

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _run_export(
        self, matches: dict, *, identifier: str, operator, export_format: str, recipients: list[str]
    ) -> None:
        from django.utils import timezone

        from snapadmin.exporting import _run_export_job, export_file_name, get_export_storage
        from snapadmin.models import SnapExportJob, SnapadminAuditLog
        from snapadmin.tenancy import _background_context

        storage = get_export_storage()
        manifest = {
            "identifier_fingerprint": _identifier_fingerprint(identifier),
            "requested_by": operator.get_username(),
            "generated_at": timezone.now().isoformat(),
            "unmasked": True,
            "files": [],
        }

        for model, rows in matches.items():
            path = _subject_path_for(model)
            job = SnapExportJob.objects.create(
                app_label=model._meta.app_label,
                model=model._meta.model_name,
                export_format=export_format,
                filters={path: identifier},
                requested_by=operator,
            )
            # Inside the same cross-tenant context the rows were collected in: the
            # job's own tenant is empty here, and default-deny would export nothing.
            _run_export_job(job.pk, _background_context(model, "subject_request"))
            job.refresh_from_db()
            if job.status != SnapExportJob.Status.COMPLETED:
                raise CommandError(f"Export of {model._meta.label} failed: {job.error}")
            if job.total_rows != len(rows):
                # A subject's export that silently leaves rows out is worse than none.
                raise CommandError(
                    f"Export of {model._meta.label}: found {len(rows)} row(s) for the subject "
                    f"but exported {job.total_rows} — refusing an incomplete subject export."
                )
            manifest["files"].append(
                {
                    "model": model._meta.label,
                    "row_count": len(rows),
                    "file": export_file_name(job),
                    "format": export_format,
                }
            )
            self.stdout.write(
                self.style.SUCCESS(f"  EXPORTED {model._meta.label}: {len(rows)} row(s)")
            )

        manifest_name = (
            f"sar_manifest_{_identifier_fingerprint(identifier)}_{uuid.uuid4().hex}.json"
        )
        actual_name = storage.save(
            manifest_name, _as_content_file(json.dumps(manifest, indent=2, sort_keys=True))
        )
        manifest["files"].append(
            {"model": None, "row_count": None, "file": actual_name, "format": "json"}
        )

        if recipients:
            self._encrypt_bundle(storage, manifest, recipients)

        SnapadminAuditLog.objects.create(
            action=SnapadminAuditLog.Action.CREATE,
            actor=operator,
            actor_repr=str(operator)[:255],
            app_label="snapadmin",
            model="subject_access_export",
            object_repr=f"SAR export for subject {_identifier_fingerprint(identifier)[:255]}",
            changes={"files": {"old": None, "new": [f["file"] for f in manifest["files"]]}},
        )
        self.stdout.write(self.style.SUCCESS(f"\nManifest: {actual_name}"))

    def _encrypt_bundle(self, storage, manifest: dict, recipients: list[str]) -> None:
        from snapadmin.crypto import encrypt_stream

        for entry in manifest["files"]:
            name = entry["file"]
            encrypted_name = f"{name}.age"
            with storage.open(name, "rb") as reader, storage.open(encrypted_name, "wb") as writer:
                # Some storage backends need bytes returned from .open(), not
                # a lazy file wrapper — encrypt_stream only ever reads/writes,
                # so any binary file-like object works either way.
                encrypt_stream(reader, writer, recipients)
            storage.delete(name)
            entry["file"] = encrypted_name
        self.stdout.write(
            self.style.SUCCESS(
                f"  Encrypted to {len(recipients)} recipient(s) — plaintext removed."
            )
        )

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def _run_delete(self, matches: dict, *, identifier: str, operator, confirm: bool) -> None:
        from django.db import router
        from django.db.models.deletion import Collector, ProtectedError

        from snapadmin.models import EsQuerySet, EsStorageMode

        db_matches = {}
        es_matches = {}
        for model, rows in matches.items():
            if getattr(model, "es_storage_mode", EsStorageMode.DB_ONLY) == EsStorageMode.ES_ONLY:
                es_matches[model] = rows
            else:
                db_matches[model] = rows

        report: dict[str, int] = {
            model._meta.label: len(rows) for model, rows in es_matches.items()
        }
        collector = None
        if db_matches:
            first_model = next(iter(db_matches))
            collector = Collector(using=router.db_for_write(first_model))
            try:
                # collect() requires a homogeneous (single-model) iterable per
                # call — one call per matched model, accumulating into the
                # same collector, mirrors how QuerySet.delete() itself walks
                # a mixed deletion graph internally.
                for rows in db_matches.values():
                    collector.collect(rows)
            except ProtectedError as exc:
                blockers = sorted({obj._meta.label for obj in exc.protected_objects})
                self.stdout.write(
                    self.style.ERROR(
                        "REFUSED — deleting these rows is blocked by a protected relation: "
                        + ", ".join(blockers)
                    )
                )
                self.stdout.write(
                    "Nothing was deleted. Resolve the blocking rows manually (delete or "
                    "reassign them) before retrying."
                )
                return
            for model, objs in collector.data.items():
                report[model._meta.label] = report.get(model._meta.label, 0) + len(objs)
        stored_files = _stored_files_of(collector) if collector is not None else []

        mode = "DRY RUN" if not confirm else "DELETED"
        for label, count in sorted(report.items()):
            self.stdout.write(f"  {mode} {label}: {count} row(s)")

        if not confirm:
            # A count only: a stored file's name is often personal data itself.
            self.stdout.write(f"  DRY RUN files: {len(stored_files)}")
            self.stdout.write("\nDry run complete — no data was deleted. Pass --confirm to delete.")
            return

        if collector is not None:
            collector.delete()
        deleted_files, failed_files = _delete_stored_files(stored_files)
        self.stdout.write(f"  DELETED files: {deleted_files}")
        if failed_files:
            self.stdout.write(
                self.style.ERROR(
                    f"  FAILED files: {len(failed_files)} — the rows are gone, these stored files "
                    "are not; remove them by hand: " + "; ".join(failed_files)
                )
            )
        for model, rows in es_matches.items():
            # EsQuerySet.filter() only ever matches flat field=value (no
            # __in support), so the already-resolved instances are wrapped
            # directly rather than re-filtered — see its own docstring.
            EsQuerySet(model, rows).delete()

        self._record_deletion_audit(operator, identifier, report, files_deleted=deleted_files)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeleted {sum(report.values())} row(s) across {len(report)} model(s)."
            )
        )

    def _record_deletion_audit(
        self, operator, identifier: str, report: dict[str, int], *, files_deleted: int = 0
    ) -> None:
        from snapadmin.models import SnapadminAuditLog

        # SnapadminAuditLog is deliberately outside the general registry (see
        # its own class docstring) and therefore never subject-scoped — this
        # entry cannot itself be swept away by a later deletion for the same
        # subject. That is the whole answer to "the entry recording the
        # deletion must not be deleted by it": it structurally cannot be.
        SnapadminAuditLog.objects.create(
            action=SnapadminAuditLog.Action.DELETE,
            actor=operator,
            actor_repr=str(operator)[:255],
            app_label="snapadmin",
            model="subject_access_deletion",
            object_repr=f"SAR deletion for subject {_identifier_fingerprint(identifier)}"[:255],
            changes={
                "models_deleted": {"old": None, "new": report},
                "files_deleted": {"old": None, "new": files_deleted},
            },
        )


def _stored_files_of(collector) -> list[tuple[type, object, str]]:
    """Every stored file the collected rows name that no surviving row names.

    All file fields, whatever their cleanup flags: an erasure that leaves the
    subject's uploads in storage is not an erasure (decision 2026-10-17). A file
    another row still names is left alone, and counted nowhere.
    """
    from django.db import models

    from snapadmin.fields import _file_name, _file_referenced

    found: dict[tuple[str, str, str], tuple[type, object, str]] = {}
    for model, rows in collector.data.items():
        doomed_pks = {row.pk for row in rows}
        for field in model._meta.concrete_fields:
            if not isinstance(field, models.FileField):
                continue
            for row in rows:
                name = _file_name(row.__dict__.get(field.attname))
                key = (model._meta.label, field.name, name)
                if (
                    name
                    and key not in found
                    and not _file_referenced(model, field.name, name, exclude_pks=doomed_pks)
                ):
                    found[key] = (model, field, name)
    return list(found.values())


def _delete_stored_files(stored_files) -> tuple[int, list[str]]:
    """Remove each file after its rows are gone; ``(deleted, failures)``.

    Re-checked after the delete, so a row that came to name the file in the
    meantime keeps it. A storage error is reported, never raised — the rows are
    already deleted, and the operator must see which files are left.
    """
    from snapadmin.fields import _file_referenced

    deleted, failures = 0, []
    for model, field, name in stored_files:
        try:
            if _file_referenced(model, field.name, name):
                continue
            if field.storage.exists(name):
                field.storage.delete(name)
                deleted += 1
        except Exception as exc:
            failures.append(f"{model._meta.label}.{field.name}: {exc}")
    return deleted, failures


def _subject_path_for(model) -> str:
    from snapadmin.registry import get_model_meta

    path = get_model_meta(model, "subject_path", None)
    if not path:  # _collect_matches filters these out first; kept as a loud guard
        raise ImproperlyConfigured(f"{model._meta.label} has no subject_path.")
    return path


def _as_content_file(text: str):
    from django.core.files.base import ContentFile

    return ContentFile(text.encode("utf-8"))

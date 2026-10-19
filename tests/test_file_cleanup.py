"""
tests/test_file_cleanup.py — replaced and deleted files leave storage (#EXT3d).

Before 0.1.0b11 only the retention purge ever deleted a stored file. Replacing a
``SnapFileField``'s file (admin change, REST ``PATCH``) left the old one behind —
with a dated ``upload_to``, one orphan per row per reload — and deleting a row,
singly, in bulk or through a GDPR subject-request delete, left all of its files.
Storage kept personal data the database no longer pointed at.

Opt-in per field: ``delete_replaced=True`` and ``delete_on_delete=True`` (the
demo's ``Showcase.file_field`` carries both). A file is removed only after the
transaction commits, and only when no other row of the model still names it.
A GDPR subject-request delete removes the files of every file field of the rows
it erases, flags or not.

The class guard is the **storage-orphan invariant** — after any write surface,
the files in storage are exactly the files rows still reference — run over every
surface that can replace or remove a row.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib import admin
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import RequestFactory

FILES_DIR = "showcase/files"


def _stored() -> set[str]:
    storage = storages["default"]
    if not storage.exists(FILES_DIR):
        return set()
    return {f"{FILES_DIR}/{name}" for name in storage.listdir(FILES_DIR)[1]}


def _referenced() -> set[str]:
    from demo.apps.shop.models import Showcase

    return {name for name in Showcase._base_manager.values_list("file_field", flat=True) if name}


def _showcase_with_file(content_name: str):
    from demo.apps.shop.models import Showcase

    row = Showcase.objects.create(char_field=content_name)
    row.file_field.save(f"{content_name}.txt", ContentFile(content_name.encode()), save=True)
    return row


@pytest.fixture(autouse=True)
def empty_files_dir():
    storage = storages["default"]
    for name in _stored():
        storage.delete(name)
    yield
    for name in _stored():
        storage.delete(name)


class TestTheFlagsAreNotASchemaChange:
    def test_neither_flag_reaches_deconstruct(self):
        from snapadmin.fields import SnapFileField, SnapImageField

        for field_class in (SnapFileField, SnapImageField):
            field = field_class(delete_replaced=True, delete_on_delete=True, upload_to="x/")
            _name, _path, _args, kwargs = field.deconstruct()

            assert "delete_replaced" not in kwargs
            assert "delete_on_delete" not in kwargs
            assert field.delete_replaced is True
            assert field.delete_on_delete is True

    def test_both_flags_default_to_off(self):
        from snapadmin.fields import SnapFileField

        field = SnapFileField(upload_to="x/")

        assert (field.delete_replaced, field.delete_on_delete) == (False, False)


@pytest.mark.django_db
class TestReplacedFile:
    def test_the_old_file_leaves_storage_once_the_replacement_is_committed(
        self, django_capture_on_commit_callbacks
    ):
        row = _showcase_with_file("first")
        old_name = row.file_field.name

        with django_capture_on_commit_callbacks(execute=True):
            row.file_field.save("second.txt", ContentFile(b"second"), save=True)

        assert _stored() == {row.file_field.name}
        assert old_name not in _stored()

    def test_a_file_another_row_still_names_is_kept(self, django_capture_on_commit_callbacks):
        from demo.apps.shop.models import Showcase

        row = _showcase_with_file("shared")
        shared_name = row.file_field.name
        Showcase.objects.create(char_field="twin", file_field=shared_name)

        with django_capture_on_commit_callbacks(execute=True):
            row.file_field.save("own.txt", ContentFile(b"own"), save=True)

        assert shared_name in _stored()

    def test_nothing_is_deleted_when_the_transaction_rolls_back(self, django_capture_on_commit_callbacks):
        row = _showcase_with_file("kept")
        old_name = row.file_field.name

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    row.file_field.save("discarded.txt", ContentFile(b"x"), save=True)
                    raise RuntimeError("roll back")
            except RuntimeError:
                pass

        assert callbacks == []
        assert old_name in _stored()

    def test_a_new_row_never_deletes_anything(self, django_capture_on_commit_callbacks):
        from demo.apps.shop.models import Showcase

        unrelated = _showcase_with_file("unrelated")

        with django_capture_on_commit_callbacks(execute=True):
            Showcase.objects.create(char_field="new", file_field=ContentFile(b"n", name="unrelated.txt"))

        assert unrelated.file_field.name in _stored()

    def test_a_field_without_the_flag_keeps_its_old_file(self, django_capture_on_commit_callbacks):
        from demo.apps.shop.models import Showcase

        row = Showcase.objects.create(char_field="img")
        row.image_field.save("a.png", ContentFile(b"\x89PNG"), save=True)
        old_image = row.image_field.name

        with django_capture_on_commit_callbacks(execute=True):
            row.image_field.save("b.png", ContentFile(b"\x89PNG"), save=True)

        assert storages["default"].exists(old_image)
        storages["default"].delete(old_image)
        storages["default"].delete(row.image_field.name)


@pytest.mark.django_db
class TestDeletedRow:
    def test_deleting_a_row_removes_its_file(self, django_capture_on_commit_callbacks):
        row = _showcase_with_file("gone")

        with django_capture_on_commit_callbacks(execute=True):
            row.delete()

        assert _stored() == set()

    def test_a_bulk_delete_removes_every_file_including_one_the_rows_shared(
        self, django_capture_on_commit_callbacks
    ):
        from demo.apps.shop.models import Showcase

        first = _showcase_with_file("bulk")
        Showcase.objects.create(char_field="bulk-twin", file_field=first.file_field.name)
        _showcase_with_file("bulk-other")

        with django_capture_on_commit_callbacks(execute=True):
            Showcase.objects.filter(char_field__startswith="bulk").delete()

        assert _stored() == set()

    def test_a_file_a_surviving_row_names_is_kept(self, django_capture_on_commit_callbacks):
        from demo.apps.shop.models import Showcase

        doomed = _showcase_with_file("survivor")
        survivor = Showcase.objects.create(char_field="keeps", file_field=doomed.file_field.name)

        with django_capture_on_commit_callbacks(execute=True):
            doomed.delete()

        assert _stored() == {survivor.file_field.name}

    def test_a_storage_error_is_logged_and_never_raised_into_the_caller(
        self, django_capture_on_commit_callbacks, monkeypatch
    ):
        from structlog.testing import capture_logs

        from demo.apps.shop.models import Showcase

        row = _showcase_with_file("stuck")
        stuck_name = row.file_field.name

        def refuse(self, name):
            raise OSError("read-only volume")

        with monkeypatch.context() as patched, capture_logs() as logs:
            patched.setattr(type(storages["default"]), "delete", refuse)
            with django_capture_on_commit_callbacks(execute=True):
                row.delete()

        assert not Showcase.objects.filter(pk=row.pk).exists()
        assert [entry for entry in logs if entry["event"] == "snapadmin.files.delete_failed"] == [
            {
                "event": "snapadmin.files.delete_failed",
                "log_level": "error",
                "model": "Showcase",
                "field": "file_field",
                "path": stuck_name,
                "error": "read-only volume",
            }
        ]


def _patch_file(client, row, name: str):
    return client.patch(
        f"/api/models/demo/Showcase/{row.pk}/",
        {"file_field": SimpleUploadedFile(name, b"replacement")},
        format="multipart",
    )


def _admin_bulk_delete(admin_user, rows):
    from demo.apps.shop.models import Showcase

    request = RequestFactory().post("/admin/")
    request.user = admin_user
    admin.site._registry[Showcase].delete_queryset(request, Showcase.objects.filter(pk__in=[r.pk for r in rows]))


def _subject_request_delete(operator, rows):
    from demo.apps.shop.models import Showcase
    from snapadmin.management.commands.snapadmin_subject_request import Command

    command = Command(stdout=StringIO(), stderr=StringIO())
    command._run_delete({Showcase: list(rows)}, identifier="subject@example.com", operator=operator, confirm=True)
    return command.stdout.getvalue()


SURFACES = [
    "orm_replace",
    "api_patch_replace",
    "orm_delete",
    "queryset_delete",
    "api_delete",
    "admin_bulk_delete",
    "subject_request_delete",
]


@pytest.mark.django_db
class TestStorageOrphanInvariant:
    """The class guard: whatever surface replaces or removes a row, the files in
    storage afterwards are exactly the files rows still reference."""

    @pytest.mark.parametrize("surface", SURFACES)
    def test_storage_holds_exactly_what_rows_reference(
        self, surface, auth_client, admin_user, django_capture_on_commit_callbacks
    ):
        first = _showcase_with_file("alpha")
        second = _showcase_with_file("beta")

        with django_capture_on_commit_callbacks(execute=True):
            if surface == "orm_replace":
                first.file_field.save("alpha-2.txt", ContentFile(b"2"), save=True)
            elif surface == "api_patch_replace":
                response = _patch_file(auth_client, first, "alpha-3.txt")
                assert response.status_code == 200, response.content
            elif surface == "orm_delete":
                first.delete()
            elif surface == "queryset_delete":
                type(first).objects.filter(pk=first.pk).delete()
            elif surface == "api_delete":
                response = auth_client.delete(f"/api/models/demo/Showcase/{first.pk}/")
                assert response.status_code == 204, response.content
            elif surface == "admin_bulk_delete":
                _admin_bulk_delete(admin_user, [first])
            elif surface == "subject_request_delete":
                _subject_request_delete(admin_user, [first])

        assert _stored() == _referenced(), (
            f"review: after {surface}, storage holds {sorted(_stored())} but rows reference "
            f"{sorted(_referenced())} — a write surface leaves files behind (or deletes live ones)"
        )
        assert second.file_field.name in _stored()


@pytest.mark.django_db
class TestSubjectRequestDeletesEveryFile:
    """A GDPR erasure removes the files of every file field of the erased rows,
    whether or not the field opted into cleanup (decision 2026-10-17)."""

    def test_an_unflagged_image_of_an_erased_row_is_removed_too(self, admin_user):
        from demo.apps.shop.models import Showcase

        row = Showcase.objects.create(char_field="subject")
        row.image_field.save("face.png", ContentFile(b"\x89PNG"), save=True)
        row.file_field.save("passport.txt", ContentFile(b"p"), save=True)
        image_name, file_name = row.image_field.name, row.file_field.name

        output = _subject_request_delete(admin_user, [row])

        assert not storages["default"].exists(image_name)
        assert not storages["default"].exists(file_name)
        assert "DELETED files: 2" in output

    def test_the_dry_run_counts_the_files_and_removes_none(self, admin_user):
        from demo.apps.shop.models import Showcase
        from snapadmin.management.commands.snapadmin_subject_request import Command

        row = _showcase_with_file("dry")
        command = Command(stdout=StringIO(), stderr=StringIO())

        command._run_delete({Showcase: [row]}, identifier="s@example.com", operator=admin_user, confirm=False)

        assert "DRY RUN files: 1" in command.stdout.getvalue()
        assert row.file_field.name in _stored()

    def test_a_file_another_row_still_names_survives_the_erasure(self, admin_user):
        from demo.apps.shop.models import Showcase

        erased = _showcase_with_file("erased")
        survivor = Showcase.objects.create(char_field="other", file_field=erased.file_field.name)

        output = _subject_request_delete(admin_user, [erased])

        assert survivor.file_field.name in _stored()
        assert "DELETED files: 0" in output


@pytest.mark.django_db
class TestSubjectRequestFileEdgeCases:
    def test_a_file_storage_refuses_to_delete_is_reported_not_raised(self, admin_user, monkeypatch):
        row = _showcase_with_file("refused")

        def refuse(self, name):
            raise OSError("read-only volume")

        with monkeypatch.context() as patched:
            patched.setattr(type(storages["default"]), "delete", refuse)
            output = _subject_request_delete(admin_user, [row])

        assert "DELETED files: 0" in output
        assert "FAILED files: 1 — the rows are gone, these stored files are not; remove them by hand: demo.Showcase.file_field: read-only volume" in output

    def test_a_file_already_gone_from_storage_is_not_counted(self, admin_user):
        row = _showcase_with_file("vanished")
        storages["default"].delete(row.file_field.name)

        output = _subject_request_delete(admin_user, [row])

        assert "DELETED files: 0" in output
        assert "FAILED" not in output

    def test_a_row_naming_the_file_by_the_time_of_removal_keeps_it(self):
        from demo.apps.shop.models import Showcase
        from snapadmin.management.commands.snapadmin_subject_request import _delete_stored_files

        row = _showcase_with_file("late")
        field = Showcase._meta.get_field("file_field")

        deleted, failures = _delete_stored_files([(Showcase, field, row.file_field.name)])

        assert (deleted, failures) == (0, [])
        assert row.file_field.name in _stored()

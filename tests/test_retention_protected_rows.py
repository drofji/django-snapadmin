"""Retention purge on a model whose rows can be ``PROTECT``-ed (#EXT2a).

A tree whose retention is decided per row — ``parent = ForeignKey("self",
on_delete=PROTECT)`` — used to defeat ``SnapModel.purge_expired`` outright:
one due row with a not-yet-due child made the single ``QuerySet.delete()``
raise ``ProtectedError``, so **nothing** was purged for that model, run after
run. Worse, the ``data_retention_files`` pass runs before the delete, so every
failing run removed the files of all due rows while the rows stayed —
rows pointing at files that no longer exist.

The contract pinned here:

* a protected row is skipped, everything else that is due is purged;
* a due parent whose children are all due goes in the same run (leaves first);
* a file is deleted only for a row that is actually deleted;
* the skipped count is reported, not swallowed — on the return value
  (``skipped_protected``), in the Celery summary and in the command output.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.db import connection, models
from django.test.utils import isolate_apps
from django.utils import timezone

from snapadmin.models import SnapModel, SnapPurgeResult


def _make_node_model(storage: FileSystemStorage) -> type[SnapModel]:
    with isolate_apps("snapadmin"):
        class RetentionNode(SnapModel):
            name = models.CharField(max_length=20)
            parent = models.ForeignKey(
                "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
            )
            created_at = models.DateTimeField()
            attachment = models.FileField(storage=storage, blank=True)

            data_retention_days = 30
            data_retention_field = "created_at"
            data_retention_files = ["attachment"]

            class Meta:
                app_label = "snapadmin"

        return RetentionNode


@pytest.fixture
def node_model(transactional_db, tmp_path):
    model = _make_node_model(FileSystemStorage(location=str(tmp_path)))
    with connection.schema_editor(atomic=False) as editor:
        editor.create_model(model)
    try:
        yield model
    finally:
        with connection.schema_editor(atomic=False) as editor:
            editor.delete_model(model)


def _node(model, name: str, *, age_days: int, parent=None, with_file: bool = False):
    node = model(name=name, parent=parent, created_at=timezone.now() - timedelta(days=age_days))
    if with_file:
        node.attachment.save(f"{name}.txt", ContentFile(name.encode()), save=False)
    node.save()
    return node


def _names(model) -> set[str]:
    return set(model.objects.values_list("name", flat=True))


class TestProtectedRowsAreSkipped:
    def test_a_due_parent_with_a_live_child_is_kept_and_the_rest_is_purged(self, node_model):
        parent = _node(node_model, "old-parent", age_days=90)
        _node(node_model, "young-child", age_days=1, parent=parent)
        _node(node_model, "old-unrelated", age_days=90)

        result = node_model.purge_expired()

        assert result == 1
        assert result.skipped_protected == 1
        assert _names(node_model) == {"old-parent", "young-child"}

    def test_a_fully_due_subtree_goes_in_one_run_leaves_first(self, node_model):
        root = _node(node_model, "root", age_days=90)
        middle = _node(node_model, "middle", age_days=90, parent=root)
        _node(node_model, "leaf", age_days=90, parent=middle)

        result = node_model.purge_expired()

        assert result == 3
        assert result.skipped_protected == 0
        assert _names(node_model) == set()

    def test_only_the_branch_above_a_live_node_is_kept(self, node_model):
        root = _node(node_model, "root", age_days=90)
        kept_branch = _node(node_model, "kept-branch", age_days=90, parent=root)
        _node(node_model, "live-leaf", age_days=1, parent=kept_branch)
        gone_branch = _node(node_model, "gone-branch", age_days=90, parent=root)
        _node(node_model, "gone-leaf", age_days=90, parent=gone_branch)

        result = node_model.purge_expired()

        assert result == 2
        assert result.skipped_protected == 2
        assert _names(node_model) == {"root", "kept-branch", "live-leaf"}

    def test_nothing_protected_takes_the_bulk_path_and_reports_zero(self, node_model):
        _node(node_model, "a", age_days=90)
        _node(node_model, "b", age_days=90)
        _node(node_model, "young", age_days=1)

        result = node_model.purge_expired()

        assert result == 2
        assert result.skipped_protected == 0
        assert _names(node_model) == {"young"}

    def test_dry_run_counts_every_due_row_and_touches_nothing(self, node_model):
        parent = _node(node_model, "old-parent", age_days=90)
        _node(node_model, "young-child", age_days=1, parent=parent)

        result = node_model.purge_expired(dry_run=True)

        assert result == 1
        assert _names(node_model) == {"old-parent", "young-child"}

    def test_the_result_is_still_an_int(self, node_model):
        _node(node_model, "a", age_days=90)

        result = node_model.purge_expired()

        assert isinstance(result, int)
        assert isinstance(result, SnapPurgeResult)
        assert result + 1 == 2


def test_the_result_reads_as_a_number_and_reprs_with_the_skip():
    result = SnapPurgeResult(4, skipped_protected=2)

    assert str(result) == "4"
    assert f"{result} rows" == "4 rows"
    assert repr(result) == "SnapPurgeResult(4, skipped_protected=2)"
    assert SnapPurgeResult(3).skipped_protected == 0


class TestFilesFollowTheRows:
    def test_a_protected_row_keeps_its_file(self, node_model):
        parent = _node(node_model, "old-parent", age_days=90, with_file=True)
        _node(node_model, "young-child", age_days=1, parent=parent)
        storage = node_model._meta.get_field("attachment").storage

        node_model.purge_expired()

        parent.refresh_from_db()
        assert storage.exists(parent.attachment.name)

    def test_a_purged_row_loses_its_file(self, node_model):
        parent = _node(node_model, "old-parent", age_days=90)
        _node(node_model, "young-child", age_days=1, parent=parent)
        doomed = _node(node_model, "old-unrelated", age_days=90, with_file=True)
        storage = node_model._meta.get_field("attachment").storage
        path = doomed.attachment.name

        node_model.purge_expired()

        assert not storage.exists(path)

    def test_a_file_shared_with_a_protected_row_survives(self, node_model):
        """The shared-file rule must count a kept row as a live reference."""
        parent = _node(node_model, "old-parent", age_days=90, with_file=True)
        _node(node_model, "young-child", age_days=1, parent=parent)
        sibling = _node(node_model, "old-sibling", age_days=90)
        node_model.objects.filter(pk=sibling.pk).update(attachment=parent.attachment.name)
        storage = node_model._meta.get_field("attachment").storage

        node_model.purge_expired()

        assert _names(node_model) == {"old-parent", "young-child"}
        assert storage.exists(parent.attachment.name)


class TestTheSkipIsReported:
    @pytest.fixture
    def registered(self, node_model, monkeypatch):
        """Make the isolated model visible to the sweep's registry walk."""
        from django.apps import apps as django_apps

        real_get_models = django_apps.get_models
        monkeypatch.setattr(
            django_apps, "get_models", lambda *a, **k: [*real_get_models(*a, **k), node_model]
        )
        monkeypatch.setattr("snapadmin.registry.is_registered", lambda model: model is node_model)
        return node_model

    def _protected_tree(self, model):
        parent = _node(model, "old-parent", age_days=90)
        _node(model, "young-child", age_days=1, parent=parent)
        _node(model, "old-unrelated", age_days=90)

    def test_the_command_prints_the_skipped_rows(self, registered):
        self._protected_tree(registered)
        out = StringIO()

        call_command("snapadmin_purge_expired_data", stdout=out)

        text = out.getvalue()
        assert "DELETED snapadmin.RetentionNode: 1 records" in text
        assert "SKIPPED snapadmin.RetentionNode: 1 records kept" in text
        assert "ERROR snapadmin.RetentionNode" not in text

    def test_the_celery_summary_carries_the_skipped_rows(self, registered):
        from snapadmin.tasks import purge_expired_data

        self._protected_tree(registered)

        result = purge_expired_data.apply().get()

        assert result["purged"]["snapadmin.RetentionNode"] == 1
        assert result["skipped_protected"] == {"snapadmin.RetentionNode": 1}
        assert "snapadmin.RetentionNode" not in result["errors"]


@pytest.mark.django_db
def test_the_command_skips_the_audit_log_when_its_retention_is_off(settings):
    """#QA1d — ``SNAPADMIN_AUDIT_RETENTION_DAYS = 0`` turns the audit sweep off;
    the command must not print a line for it."""
    settings.SNAPADMIN_AUDIT_RETENTION_DAYS = 0
    out = StringIO()

    call_command("snapadmin_purge_expired_data", stdout=out)

    assert "SnapadminAuditLog" not in out.getvalue()
    assert "Total deleted:" in out.getvalue()

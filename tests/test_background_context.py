"""
tests/test_background_context.py — cross-tenant background jobs run inside a model's own
context (#EXT3r).

The retention purge, the Elasticsearch reindex and the GDPR subject request read every
tenant's rows, and they did it by binding SnapAdmin's ``use_all_tenants()`` — which only
SnapAdmin's own scoping understands. A project whose manager refuses to run without *its*
tenant context (a context variable of its own, or PostgreSQL row-level security that needs
``SET LOCAL`` first) saw the beat task abort, or zero rows. Two jobs did not bind even that:
``es_reindex_all()`` (the admin reindex API and the ``run_es_reindex`` task) indexed only the
calling request's tenant — or nothing, from a worker — and the subject request collected no
row of a tenant-scoped model, so an export or an erasure was silently incomplete.

``SnapModel.snap_background_context(purpose)`` is the one hook every such job enters, with
``purpose`` one of ``"retention"``, ``"reindex"``, ``"subject_request"``. The default is
``use_all_tenants()``; a project returns its own context instead.

Missed because: every test used SnapModel's own manager, and none modelled a manager that
refuses without its context. The class guard: such a model runs through every job, and an
``ast`` scan keeps ``use_all_tenants()`` out of every other place in the package.
"""

from __future__ import annotations

import ast
import contextvars
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection, models
from django.test import override_settings
from django.test.utils import isolate_apps
from django.utils import timezone

from snapadmin import tenancy
from snapadmin.models import EsManager, SnapModel

_project_scope = contextvars.ContextVar("project_scope", default=None)


class ProjectScopedManager(EsManager):
    """What an adopter's own tenancy looks like: no context of its own, no rows at all."""

    def get_queryset(self):
        if _project_scope.get() is None:
            raise RuntimeError("ProjectScopedManager used outside the project's tenant context")
        return super().get_queryset()


@contextmanager
def _project_context():
    token = _project_scope.set("everyone")
    try:
        yield
    finally:
        _project_scope.reset(token)


def _build():
    entered: list[str] = []

    with isolate_apps("snapadmin"):

        class ScopedJournal(SnapModel):
            # The project's own tenancy, not SnapAdmin's tenant_scoped.
            tenant_id = models.CharField(max_length=20)
            email = models.EmailField()
            created_at = models.DateTimeField()
            objects = ProjectScopedManager()

            data_retention_days = 30
            data_retention_field = "created_at"
            es_index_enabled = True
            es_mapping = {"properties": {"email": {"type": "keyword"}}}
            subject_path = "email"
            is_data_subject = True
            subject_identifier = "email"

            class Meta:
                app_label = "snapadmin"

            @classmethod
            def snap_background_context(cls, purpose):
                entered.append(purpose)
                return _project_context()

    return ScopedJournal, entered


@pytest.fixture
def journal(transactional_db):
    model, entered = _build()
    with connection.schema_editor(atomic=False) as editor:
        editor.create_model(model)
    try:
        yield model, entered
    finally:
        with connection.schema_editor(atomic=False) as editor:
            editor.delete_model(model)


def _rows(model, *specs):
    old = timezone.now() - timedelta(days=60)
    for tenant, email, created_at in specs:
        model._base_manager.create(tenant_id=tenant, email=email, created_at=created_at or old)


class TestTheDefaultHook:
    def test_it_is_use_all_tenants(self):
        with SnapModel.snap_background_context("retention"):
            assert tenancy.get_current_tenant() is tenancy.ALL_TENANTS

    def test_the_purposes_are_the_three_jobs(self):
        assert SnapModel.BACKGROUND_PURPOSES == ("retention", "reindex", "subject_request")

    def test_an_unknown_purpose_is_refused(self):
        with pytest.raises(ValueError, match="'export'"):
            tenancy._background_context(SnapModel, "export")

    def test_a_plain_model_without_the_hook_gets_the_default(self):
        with isolate_apps("snapadmin"):
            plain = type("PlainThing", (models.Model,), {
                "__module__": __name__, "Meta": type("Meta", (), {"app_label": "snapadmin"}),
            })

        with tenancy._background_context(plain, "subject_request"):
            assert tenancy.get_current_tenant() is tenancy.ALL_TENANTS


class TestEveryJobRunsInsideTheProjectsHook:
    def test_the_retention_purge(self, journal):
        model, entered = journal
        _rows(model, ("a", "x@example.com", None), ("b", "y@example.com", None))

        result = model.purge_expired()

        assert int(result) == 2
        assert entered == ["retention"]
        assert model._base_manager.count() == 0

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_es_reindex_all_covers_every_tenant(self, journal):
        model, entered = journal
        _rows(model, ("a", "x@example.com", None), ("b", "y@example.com", None))
        indexed: list = []

        def bulk(client, actions, **kwargs):
            indexed.extend(actions)
            return len(indexed), []

        with patch.object(model, "get_es_client", return_value=MagicMock()), \
                patch.object(model, "_ensure_es_index_and_mapping"), \
                patch("elasticsearch.helpers.bulk", side_effect=bulk), \
                tenancy.use_tenant("a"):
            result = model.es_reindex_all()

        assert result == {"indexed": 2}
        assert entered == ["reindex"]
        assert sorted(action["_id"] for action in indexed) == sorted(
            model._base_manager.values_list("pk", flat=True)
        )

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_the_reindex_job(self, journal, monkeypatch):
        from snapadmin import reindexing

        model, entered = journal
        _rows(model, ("a", "x@example.com", None), ("b", "y@example.com", None))
        job = reindexing.start_reindex(model)
        # The job names its model by label; an isolated model is not in the app registry.
        monkeypatch.setattr(type(job), "target_model", lambda self: model)
        client = MagicMock()
        client.count.return_value = {"count": 2}

        with patch.object(model, "get_es_client", return_value=client), \
                patch.object(model, "_ensure_es_index_and_mapping"), \
                patch("elasticsearch.helpers.bulk", side_effect=lambda c, a, **k: (len(list(a)), [])):
            summary = reindexing.run_reindex_job(job)

        assert summary.get("indexed") == 2, summary
        assert entered == ["reindex"]

    def test_the_subject_request_finds_every_tenant_s_rows(self, journal, monkeypatch):
        from django.apps import apps

        from snapadmin.management.commands.snapadmin_subject_request import Command

        model, entered = journal
        _rows(model, ("a", "x@example.com", None), ("b", "x@example.com", None))
        monkeypatch.setattr(apps, "get_models", lambda: [model])

        matches = Command()._collect_matches("x@example.com")

        assert len(matches[model]) == 2
        assert "subject_request" in entered


# ── The default hook: SnapAdmin's own tenant_scoped models (demo.Order) ─────


@pytest.fixture
def orders_of_two_tenants(customer):
    from decimal import Decimal

    from demo.apps.shop.models import Order

    for tenant in ("acme.example", "globex.example"):
        Order._base_manager.create(customer=customer, total=Decimal("1.00"), tenant_id=tenant)
    return Order


@pytest.mark.django_db
class TestTheDefaultHookCoversTenantScopedModels:
    """Two jobs never bound even ``use_all_tenants()`` — regressions for both."""

    def test_the_subject_request_collects_every_tenant_s_rows(self, orders_of_two_tenants, customer):
        from snapadmin.management.commands.snapadmin_subject_request import Command

        matches = Command()._collect_matches(customer.email)

        assert len(matches[orders_of_two_tenants]) == 2

    @override_settings(ELASTICSEARCH_ENABLED=True)
    def test_es_reindex_all_indexes_every_tenant_whatever_the_caller_s_tenant(
        self, orders_of_two_tenants
    ):
        Order = orders_of_two_tenants
        indexed: list = []

        def bulk(client, actions, **kwargs):
            indexed.extend(actions)
            return len(indexed), []

        with patch.object(Order, "get_es_client", return_value=MagicMock()), \
                patch.object(Order, "_ensure_es_index_and_mapping"), \
                patch("elasticsearch.helpers.bulk", side_effect=bulk), \
                tenancy.use_tenant("acme.example"):
            result = Order.es_reindex_all()

        assert result == {"indexed": 2}
        assert sorted(action["_id"] for action in indexed) == sorted(
            Order._base_manager.values_list("pk", flat=True)
        )


# ── The class guard: no job binds use_all_tenants() on its own ──────────────

PACKAGE = Path(__file__).resolve().parent.parent / "snapadmin"

#: The only places a direct use_all_tenants() call may stay: the default hook
#: and the helper every job goes through.
ALLOWED_CALLERS = {
    ("snapadmin/models.py", "snap_background_context"),
    ("snapadmin/tenancy.py", "_background_context"),
}


def _direct_callers() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", None)) == "use_all_tenants"
                ):
                    found.add((str(path.relative_to(PACKAGE.parent)), function.name))
    return found


def test_only_the_hook_binds_use_all_tenants():
    unexpected = _direct_callers() - ALLOWED_CALLERS

    assert not unexpected, (
        f"review: {sorted(unexpected)} call use_all_tenants() directly — a cross-tenant job must "
        "enter tenancy._background_context(model, purpose) so a project's own tenancy applies"
    )

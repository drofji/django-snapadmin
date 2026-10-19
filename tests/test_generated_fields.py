"""
tests/test_generated_fields.py — a database-generated column on every surface (#EXT3h).

graphene-django has no converter for ``models.GeneratedField``, and SnapAdmin's
schema builder caught every exception per model and logged it: a model with one
generated column vanished from the GraphQL schema entirely, with a warning
nobody read. No model in the demo or the tests had such a column.

The demo's ``Showcase`` now carries one (``char_length``), and this module walks
it through every surface: GraphQL, REST read and write, the admin form, the
importer. The class guard is **schema completeness** — every registered,
API-exposed model is in the GraphQL schema — plus a per-field fallback: a field
graphene cannot convert costs that one field (logged), never the model.
"""

from __future__ import annotations

import csv
from decimal import Decimal

import pytest
from django.apps import apps
from snapadmin.registry import is_registered


def _graphql_type_names() -> set[str]:
    from snapadmin.api.graphql import get_dynamic_graphql_schema

    return set(get_dynamic_graphql_schema().graphql_schema.type_map)


class TestSchemaCompleteness:
    """The class guard: no registered model silently drops out of the schema."""

    def test_every_registered_model_has_a_graphql_type(self):
        type_names = _graphql_type_names()
        missing = sorted(
            model._meta.label
            for model in apps.get_models()
            if is_registered(model)
            and f"{model._meta.app_label.capitalize()}{model.__name__}Type" not in type_names
        )

        assert not missing, (
            f"review: {missing} are registered but absent from the GraphQL schema — a field "
            "graphene could not convert took the whole model with it (see graphql_model_skipped)"
        )

    def test_the_generated_column_is_in_the_showcase_type_as_its_output_type(self):
        from snapadmin.api.graphql import get_dynamic_graphql_schema

        showcase = get_dynamic_graphql_schema().graphql_schema.type_map["DemoShowcaseType"]

        assert str(showcase.fields["charLength"].type) == "Int"


class TestOneUnconvertibleFieldCostsOnlyItself:
    def test_the_model_stays_and_the_field_is_skipped_and_logged(self, monkeypatch):
        from graphene_django import converter
        from structlog.testing import capture_logs

        from snapadmin.api import graphql as graphql_module

        real = converter.convert_django_field

        def refuse_one_field(field, registry=None):
            if getattr(field, "model", None) is not None and (field.model.__name__, field.name) == (
                "Showcase",
                "slug_field",
            ):
                raise Exception(f"Don't know how to convert the Django field {field!r}")
            return real(field, registry)

        monkeypatch.setattr(converter, "convert_django_field", refuse_one_field)
        # graphene caches converted fields in its global registry; a fresh one makes
        # this build convert every field again.
        from graphene_django import registry as graphene_registry

        monkeypatch.setattr(graphene_registry, "registry", graphene_registry.Registry())
        with capture_logs() as logs:
            schema = graphql_module.get_dynamic_graphql_schema()

        type_map = schema.graphql_schema.type_map
        assert "DemoShowcaseType" in type_map
        assert "slugField" not in type_map["DemoShowcaseType"].fields
        assert "charField" in type_map["DemoShowcaseType"].fields
        skipped = [entry for entry in logs if entry["event"] == "graphql_field_skipped"]
        assert {"model": "Showcase", "field": "slug_field"} in [
            {"model": entry["model"], "field": entry["field"]} for entry in skipped
        ]
        assert not [entry for entry in logs if entry["event"] == "graphql_model_skipped"]


@pytest.mark.django_db
class TestTheGeneratedColumnOnEverySurface:
    def test_rest_reads_it_and_never_accepts_it(self, auth_client):
        created = auth_client.post(
            "/api/models/demo/Showcase/", {"char_field": "hello", "char_length": 99}, format="json"
        )
        assert created.status_code == 201, created.content
        assert created.json()["char_length"] == 5

        read = auth_client.get(f"/api/models/demo/Showcase/{created.json()['id']}/")
        assert read.json()["char_length"] == 5

    def test_the_admin_form_never_offers_it_for_editing(self, client, admin_user):
        from django.contrib import admin
        from django.test import RequestFactory

        from demo.apps.shop.models import Showcase

        request = RequestFactory().get("/")
        request.user = admin_user

        assert "char_length" not in admin.site._registry[Showcase].get_form(request)().fields

    def test_the_importer_refuses_a_column_mapped_onto_it(self, tmp_path):
        from demo.apps.shop.models import Showcase
        from snapadmin.importing import run_import_job, start_import

        path = tmp_path / "in.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["char_field", "char_length"])
            writer.writeheader()
            writer.writerow({"char_field": "abc", "char_length": "40"})

        summary = run_import_job(start_import(Showcase, file_path=str(path)), file_path=str(path))

        assert summary["outcome"] == "failed"
        assert "char_length" in summary["errors"][0]
        assert not Showcase.objects.filter(char_field="abc").exists()

    def test_graphql_answers_the_computed_value(self, admin_user):
        from types import SimpleNamespace

        from demo.apps.shop.models import Showcase
        from snapadmin.api.graphql import get_dynamic_graphql_schema

        Showcase.objects.create(char_field="four", decimal_field=Decimal("1"))

        result = get_dynamic_graphql_schema().execute(
            "{ allDemoShowcases { charField charLength } }",
            context_value=SimpleNamespace(user=admin_user, auth=None),
        )

        assert result.errors is None, result.errors
        assert {"charField": "four", "charLength": 4} in result.data["allDemoShowcases"]

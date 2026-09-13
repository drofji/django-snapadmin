"""
tests/test_api_validation_errors.py

A Django ``ValidationError`` raised on an API write must come back as a 400
naming the field, never as an uncaught 500 (#EXT1k).
"""

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import override_settings
from rest_framework import exceptions as drf_exceptions
from rest_framework.test import APIClient

from snapadmin.api.exceptions import (
    DjangoValidationErrorMixin,
    as_drf_validation_error,
    snap_exception_handler,
)


def _reject_save(message):
    """A ``save()`` that raises the way a model with a ``clean()`` rule does."""

    def guarded(self, *args, **kwargs):
        raise DjangoValidationError(message)

    return guarded


# ── The translation helper ────────────────────────────────────────────────────

class TestAsDrfValidationError:
    def test_field_dict_keeps_its_field_names(self):
        converted = as_drf_validation_error(
            DjangoValidationError({"name": ["Too short."]})
        )
        assert isinstance(converted, drf_exceptions.ValidationError)
        assert converted.detail["name"][0] == "Too short."

    def test_plain_message_becomes_a_non_field_error(self):
        converted = as_drf_validation_error(DjangoValidationError("Nope."))
        assert converted.detail["non_field_errors"][0] == "Nope."

    def test_several_fields_are_all_reported(self):
        converted = as_drf_validation_error(
            DjangoValidationError({"name": ["Required."], "price": ["Too low."]})
        )
        assert set(converted.detail) == {"name", "price"}


# ── The drop-in EXCEPTION_HANDLER ─────────────────────────────────────────────

class TestSnapExceptionHandler:
    def test_django_validation_error_becomes_a_400(self):
        response = snap_exception_handler(
            DjangoValidationError({"name": ["Too short."]}), {}
        )
        assert response.status_code == 400
        assert response.data["name"][0] == "Too short."

    def test_drf_exception_is_left_to_drf(self):
        response = snap_exception_handler(drf_exceptions.NotFound("Gone."), {})
        assert response.status_code == 404

    def test_unknown_exception_still_returns_none(self):
        assert snap_exception_handler(RuntimeError("boom"), {}) is None


# ── The mixin on the generated endpoints ──────────────────────────────────────

@pytest.mark.django_db
class TestGeneratedEndpointReturns400:
    def test_create_returns_400_not_500(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Product

        monkeypatch.setattr(
            Product, "save", _reject_save({"name": ["Completed rows need a file."]})
        )
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Bad", "price": "1.00", "available": True},
            format="json",
        )
        assert response.status_code == 400

    def test_create_names_the_offending_field(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Product

        monkeypatch.setattr(
            Product, "save", _reject_save({"name": ["Completed rows need a file."]})
        )
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Bad", "price": "1.00", "available": True},
            format="json",
        )
        assert response.json()["name"] == ["Completed rows need a file."]

    def test_response_is_json_not_an_html_page(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Product

        monkeypatch.setattr(Product, "save", _reject_save("Cross-field rule failed."))
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Bad", "price": "1.00", "available": True},
            format="json",
        )
        assert response["Content-Type"].startswith("application/json")
        assert response.json()["non_field_errors"] == ["Cross-field rule failed."]

    def test_update_returns_400_too(self, auth_client, product, monkeypatch):
        from demo.apps.shop.models import Product

        monkeypatch.setattr(Product, "save", _reject_save({"price": ["Too low."]}))
        response = auth_client.patch(
            f"/api/models/demo/Product/{product.pk}/",
            {"price": "0.01"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["price"] == ["Too low."]

    def test_a_successful_write_is_untouched(self, auth_client):
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Fine", "price": "9.99", "available": True},
            format="json",
        )
        assert response.status_code == 201

    def test_other_errors_keep_their_own_status(self, auth_client):
        response = auth_client.get("/api/models/demo/NoSuchModel/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestTokenEndpointReturns400:
    """The mixin rides on every SnapAdmin API view, not only the generated one."""

    def test_token_create_returns_400_not_500(self, auth_client, monkeypatch):
        from snapadmin.models import APIToken

        monkeypatch.setattr(
            APIToken,
            "create_for_user",
            classmethod(
                lambda cls, **kwargs: (_ for _ in ()).throw(
                    DjangoValidationError({"token_name": ["Reserved."]})
                )
            ),
        )
        response = auth_client.post(
            "/api/tokens/", {"token_name": "root"}, format="json"
        )
        assert response.status_code == 400
        assert response.json()["token_name"] == ["Reserved."]


# ── A project's own EXCEPTION_HANDLER keeps first refusal ─────────────────────

_HANDLED = []


def _project_handler(exc, context):
    """A project handler that claims the error before SnapAdmin sees it."""
    from rest_framework.response import Response

    _HANDLED.append(type(exc))
    if isinstance(exc, DjangoValidationError):
        return Response({"handled_by": "project"}, status=422)
    from rest_framework.views import exception_handler

    return exception_handler(exc, context)


@pytest.mark.django_db
class TestProjectHandlerWins:
    def test_custom_handler_sees_the_django_error_first(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Product

        _HANDLED.clear()
        monkeypatch.setattr(Product, "save", _reject_save("Nope."))
        with override_settings(
            REST_FRAMEWORK={
                "EXCEPTION_HANDLER": f"{__name__}._project_handler",
                "DEFAULT_AUTHENTICATION_CLASSES": [
                    "snapadmin.api.authentication.APITokenAuthentication",
                    "rest_framework.authentication.SessionAuthentication",
                ],
            }
        ):
            response = auth_client.post(
                "/api/models/demo/Product/",
                {"name": "Bad", "price": "1.00", "available": True},
                format="json",
            )
        assert response.status_code == 422
        assert DjangoValidationError in _HANDLED


class TestMixinOrdering:
    def test_mixin_is_mixed_into_every_snapadmin_api_view(self):
        from snapadmin.api.authentication import SnapAPIAuthMixin

        assert issubclass(SnapAPIAuthMixin, DjangoValidationErrorMixin)

    def test_mixin_passes_a_non_validation_error_straight_through(self):
        class Base:
            def handle_exception(self, exc):
                return ("delegated", exc)

        class View(DjangoValidationErrorMixin, Base):
            pass

        error = drf_exceptions.NotFound("Gone.")
        assert View().handle_exception(error) == ("delegated", error)

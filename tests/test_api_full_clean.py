"""
tests/test_api_full_clean.py

``api_full_clean`` — run the model's own ``clean()``/``full_clean()`` on the API
write path, so a cross-field rule holds in the API as it does in the admin
(#EXT1k).
"""

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import override_settings

from snapadmin.api.serializers import ModelCleanSerializerMixin, build_model_serializer


@pytest.fixture
def category(db):
    from demo.apps.shop.models import Category

    return Category.objects.create(name="Gear", slug="gear", is_active=True)


@pytest.fixture
def tag(db):
    from demo.apps.shop.models import Tag

    return Tag.objects.create(name="sale")


# ── The flag is off unless asked for ──────────────────────────────────────────

@pytest.mark.django_db
class TestDefaultIsOff:
    def test_mixin_is_on_every_generated_serializer(self):
        from demo.apps.shop.models import Category

        assert issubclass(build_model_serializer(Category), ModelCleanSerializerMixin)

    def test_clean_is_not_called_without_the_flag(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Category

        called = []
        monkeypatch.setattr(Category, "clean", lambda self: called.append(self))
        response = auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Books", "slug": "books", "is_active": True},
            format="json",
        )
        assert response.status_code == 201
        assert called == []

    def test_clean_runs_once_the_flag_is_on(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Category

        called = []
        monkeypatch.setattr(Category, "clean", lambda self: called.append(self))
        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        response = auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Music", "slug": "music", "is_active": True},
            format="json",
        )
        assert response.status_code == 201
        assert len(called) == 1


# ── A rejecting rule answers 400, naming the field ────────────────────────────

def _reject(field, message):
    def clean(self):
        raise DjangoValidationError({field: [message]})

    return clean


@pytest.mark.django_db
class TestCleanRejectsTheWrite:
    def test_create_returns_400(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Category

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "clean", _reject("slug", "Reserved slug."))
        response = auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Nope", "slug": "admin", "is_active": True},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["slug"] == ["Reserved slug."]

    def test_nothing_is_written(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Category

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "clean", _reject("slug", "Reserved slug."))
        auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Nope", "slug": "admin", "is_active": True},
            format="json",
        )
        assert not Category.objects.filter(slug="admin").exists()

    def test_a_message_without_a_field_is_a_non_field_error(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Category

        def clean(self):
            raise DjangoValidationError("Catalogue is frozen.")

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "clean", clean)
        response = auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Nope", "slug": "frozen", "is_active": True},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["non_field_errors"] == ["Catalogue is frozen."]

    def test_patch_is_validated_against_the_merged_row(self, auth_client, monkeypatch, category):
        """A PATCH sends one field; the rule must see the whole row, not the patch."""
        from demo.apps.shop.models import Category

        seen = {}

        def clean(self):
            seen["name"] = self.name
            seen["slug"] = self.slug

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "clean", clean)
        response = auth_client.patch(
            f"/api/models/demo/Category/{category.pk}/",
            {"slug": "patched"},
            format="json",
        )
        assert response.status_code == 200
        assert seen == {"name": category.name, "slug": "patched"}

    def test_patch_can_be_rejected(self, auth_client, monkeypatch, category):
        from demo.apps.shop.models import Category

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "clean", _reject("slug", "Slug is frozen."))
        response = auth_client.patch(
            f"/api/models/demo/Category/{category.pk}/",
            {"slug": "patched"},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["slug"] == ["Slug is frozen."]

    def test_the_stored_row_is_untouched_by_a_rejected_patch(self, auth_client, monkeypatch, category):
        from demo.apps.shop.models import Category

        original = category.slug
        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "clean", _reject("slug", "Slug is frozen."))
        auth_client.patch(
            f"/api/models/demo/Category/{category.pk}/",
            {"slug": "patched"},
            format="json",
        )
        category.refresh_from_db()
        assert category.slug == original


# ── What full_clean() is and is not allowed to complain about ─────────────────

@pytest.mark.django_db
class TestValidationScope:
    def test_a_server_assigned_field_is_not_reported_as_missing(self, auth_client, monkeypatch, customer):
        """``created_at`` is ``auto_now_add`` — unset on an unsaved row, and not
        something a client can supply. Validating it would 400 every create."""
        from demo.apps.shop.models import Order

        monkeypatch.setattr(Order, "api_full_clean", True, raising=False)
        response = auth_client.post(
            "/api/models/demo/Order/",
            {"customer": customer.pk, "total": "10.00"},
            format="json",
        )
        assert response.status_code == 201

    def test_a_field_rule_still_fires(self, auth_client, monkeypatch):
        """``clean_fields()`` runs too — a model-level validator on a writable
        field is reported under that field's name."""
        from django.core.validators import MinLengthValidator

        from demo.apps.shop.models import Category

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        field = Category._meta.get_field("slug")
        monkeypatch.setattr(
            field, "validators", [*field.validators, MinLengthValidator(6)]
        )
        response = auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Short", "slug": "ab", "is_active": True},
            format="json",
        )
        assert response.status_code == 400
        assert "slug" in response.json()

    def test_uniqueness_is_left_to_drf(self, auth_client, monkeypatch):
        """DRF's ``UniqueValidator``/``UniqueTogetherValidator`` already own
        uniqueness; running it again would report the same clash twice."""
        from demo.apps.shop.models import Category

        seen = {}

        def record(self, **kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "full_clean", record)
        auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Any", "slug": "any", "is_active": True},
            format="json",
        )
        assert seen["validate_unique"] is False

    def test_only_writable_fields_are_validated(self, auth_client, monkeypatch):
        """Validation is scoped to what the API can actually set — a field the
        client cannot supply must not 400 a create it has no way to fix."""
        from demo.apps.shop.models import Category

        seen = {}

        def record(self, **kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(Category, "api_full_clean", True, raising=False)
        monkeypatch.setattr(Category, "full_clean", record)
        auth_client.post(
            "/api/models/demo/Category/",
            {"name": "Any", "slug": "any", "is_active": True},
            format="json",
        )
        assert "id" in seen["exclude"]
        assert "name" not in seen["exclude"]

    def test_a_many_to_many_payload_does_not_break_validation(self, auth_client, monkeypatch, tag):
        """An m2m cannot be assigned to an unsaved row — it must be left out of
        the instance the rule is checked against, not crash the request."""
        from demo.apps.shop.models import Product

        monkeypatch.setattr(Product, "api_full_clean", True, raising=False)
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Tagged", "price": "1.00", "available": True, "tags": [tag.pk]},
            format="json",
        )
        assert response.status_code == 201

    def test_a_foreign_key_is_available_to_the_rule(self, auth_client, monkeypatch, customer):
        from demo.apps.shop.models import Order

        seen = {}
        monkeypatch.setattr(Order, "api_full_clean", True, raising=False)
        monkeypatch.setattr(
            Order, "clean", lambda self: seen.setdefault("customer", self.customer_id)
        )
        auth_client.post(
            "/api/models/demo/Order/",
            {"customer": customer.pk, "total": "10.00"},
            format="json",
        )
        assert seen["customer"] == customer.pk


# ── The project-wide switch ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestProjectWideSetting:
    def test_setting_turns_it_on_for_a_registered_plain_model(self, auth_client, monkeypatch):
        """The ``SNAPADMIN_*`` tier of ``get_model_meta`` is what makes one line
        in settings cover a whole project — see the accessor's precedence rule."""
        from demo.apps.shop.models import LegacyStockLevel

        called = []
        monkeypatch.setattr(
            LegacyStockLevel, "clean", lambda self: called.append(self), raising=False
        )
        with override_settings(SNAPADMIN_API_FULL_CLEAN=True):
            auth_client.post(
                "/api/models/demo/LegacyStockLevel/", {"on_hand": 3}, format="json"
            )
        assert len(called) == 1

    def test_a_model_attribute_still_wins_over_the_setting(self, auth_client, monkeypatch):
        from demo.apps.shop.models import Category

        called = []
        monkeypatch.setattr(Category, "clean", lambda self: called.append(self))
        monkeypatch.setattr(Category, "api_full_clean", False, raising=False)
        with override_settings(SNAPADMIN_API_FULL_CLEAN=True):
            auth_client.post(
                "/api/models/demo/Category/",
                {"name": "Opted out", "slug": "opted-out", "is_active": True},
                format="json",
            )
        assert called == []


# ── The demo dogfoods it ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDemoDogfood:
    def test_product_opts_in(self):
        from demo.apps.shop.models import Product

        assert Product.api_full_clean is True

    def test_the_demo_rule_rejects_an_available_product_with_no_price(self, auth_client):
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Free lunch", "price": "0.00", "available": True},
            format="json",
        )
        assert response.status_code == 400
        assert "price" in response.json()

    def test_the_same_row_is_accepted_when_not_available(self, auth_client):
        response = auth_client.post(
            "/api/models/demo/Product/",
            {"name": "Draft item", "price": "0.00", "available": False},
            format="json",
        )
        assert response.status_code == 201

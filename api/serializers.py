"""
api/serializers.py

DRF serializers for the SnapAdmin auto-generated REST API.

Provides:
  - APITokenSerializer           : Full CRUD for APIToken management.
  - APITokenCreateSerializer     : Write-only serializer for token creation.
  - DynamicModelSerializerFactory: Generates a DRF ModelSerializer for any Django model.
"""

from django.apps import apps
from django.contrib.auth.models import User
from rest_framework import serializers

from api.models import APIToken


# ─────────────────────────────────────────────────────────────────────────────
# Token serializers
# ─────────────────────────────────────────────────────────────────────────────

class APITokenSerializer(serializers.ModelSerializer):
    """
    Read serializer for listing / retrieving API tokens.

    The ``token_key`` is included here for admin/owner views but should be
    served only over HTTPS in production.
    """

    owner_username = serializers.CharField(source="user.username", read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    is_valid = serializers.BooleanField(read_only=True)

    class Meta:
        model = APIToken
        fields = [
            "id",
            "token_name",
            "token_key",
            "owner_username",
            "expiration_date",
            "allowed_models",
            "is_active",
            "is_expired",
            "is_valid",
            "created_at",
            "last_used_at",
        ]
        read_only_fields = ["token_key", "created_at", "last_used_at"]


class APITokenCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating new API tokens.

    The generated ``token_key`` is returned once in the create response
    and is NOT retrievable afterwards (treat it like a password).
    """

    expires_in_days = serializers.IntegerField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="Optional. Sets expiration N days from now. Omit for no expiry.",
    )

    class Meta:
        model = APIToken
        fields = ["token_name", "allowed_models", "expires_in_days"]

    def create(self, validated_data):
        """Create a token bound to the currently authenticated user."""
        expires_in_days = validated_data.pop("expires_in_days", None)
        request = self.context["request"]
        return APIToken.create_for_user(
            user=request.user,
            token_name=validated_data["token_name"],
            allowed_models=validated_data.get("allowed_models", []),
            expires_in_days=expires_in_days,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic model serializer factory
# ─────────────────────────────────────────────────────────────────────────────

def build_model_serializer(model_class):
    """
    Dynamically construct a DRF ModelSerializer for any Django model.

    All fields are included by default. File fields are represented as URLs.
    M2M and reverse relations are represented as primary-key lists.

    Args:
        model_class: A concrete Django model class.

    Returns:
        A new ModelSerializer subclass for ``model_class``.
    """
    meta_class = type(
        "Meta",
        (),
        {
            "model": model_class,
            "fields": "__all__",
        },
    )
    serializer_class = type(
        f"{model_class.__name__}Serializer",
        (serializers.ModelSerializer,),
        {"Meta": meta_class},
    )
    return serializer_class


# Cache to avoid rebuilding serializers on every request
_serializer_cache: dict = {}


def get_serializer_for_model(app_label: str, model_name: str):
    """
    Return a cached serializer class for the given model.

    Args:
        app_label:  Django app label (e.g. "demo").
        model_name: Model class name (e.g. "Product").

    Returns:
        A ModelSerializer subclass.

    Raises:
        LookupError: If no model is registered under that app/name.
    """
    cache_key = f"{app_label}.{model_name}"
    if cache_key not in _serializer_cache:
        model_class = apps.get_model(app_label, model_name)
        _serializer_cache[cache_key] = build_model_serializer(model_class)
    return _serializer_cache[cache_key]

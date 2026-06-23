"""
snapadmin/api/serializers.py

DRF serializers for the SnapAdmin auto-generated REST API.
"""

from django.apps import apps
from django.contrib.auth.models import User
from rest_framework import serializers

from snapadmin.models import APIToken


class APITokenSerializer(serializers.ModelSerializer):
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
    expires_in_days = serializers.IntegerField(
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = APIToken
        fields = ["token_name", "allowed_models", "expires_in_days"]

    def create(self, validated_data):
        expires_in_days = validated_data.pop("expires_in_days", None)
        request = self.context["request"]
        return APIToken.create_for_user(
            user=request.user,
            token_name=validated_data["token_name"],
            allowed_models=validated_data.get("allowed_models", []),
            expires_in_days=expires_in_days,
        )


def build_model_serializer(model_class):
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


_serializer_cache: dict = {}


def get_serializer_for_model(app_label: str, model_name: str):
    cache_key = f"{app_label}.{model_name}"
    if cache_key not in _serializer_cache:
        model_class = apps.get_model(app_label, model_name)
        _serializer_cache[cache_key] = build_model_serializer(model_class)
    return _serializer_cache[cache_key]

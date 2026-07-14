"""
snapadmin/api/serializers.py

DRF serializers for the SnapAdmin auto-generated REST API.
"""

from django.apps import apps
from rest_framework import serializers

from snapadmin.masking import get_masked_fields, mask_value, user_can_view_pii
from snapadmin.models import APIToken


class PIIMaskingSerializerMixin:
    """Masks configured PII fields in the output unless the requester is allowed.

    Reads ``SNAPADMIN_MASKED_FIELDS`` for the bound model and, when the request
    user lacks PII access (see :func:`snapadmin.masking.user_can_view_pii`),
    obfuscates those fields in ``to_representation``. With no request in context
    it masks (fail-closed).
    """

    _snap_model = None  # set by build_model_serializer

    def to_representation(self, instance):
        data = super().to_representation(instance)
        model = self._snap_model
        if model is None:  # pragma: no cover - defensive; always set in practice
            return data
        masked = get_masked_fields(model._meta.app_label, model._meta.model_name)
        if not masked:
            return data
        user = getattr(self.context.get("request"), "user", None)
        if user_can_view_pii(user):
            return data
        for field in masked:
            if field in data:
                data[field] = mask_value(data[field])
        return data


class WriteFieldAllowlistSerializerMixin:
    """Restricts which fields accept client-supplied values via ``api_write_fields``.

    When the bound model declares ``api_write_fields`` (a list, not ``None``),
    every field not named in that list is forced read-only on this serializer —
    it can still be returned in responses (read exposure stays controlled by
    ``api_exclude_fields``), but any value a client sends for it is silently
    ignored on create/update, the same way DRF already ignores unknown input
    keys. Left unset (``None``, the default), this mixin is a no-op and every
    field keeps whatever writability ``ModelSerializer`` gave it.
    """

    _snap_write_fields: list[str] | None = None  # set by build_model_serializer

    def get_fields(self):
        fields = super().get_fields()
        if self._snap_write_fields is not None:
            allowed = set(self._snap_write_fields)
            for name, field in fields.items():
                if name not in allowed:
                    field.read_only = True
                    field.required = False
        return fields


class APITokenSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="user.get_username", read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    is_valid = serializers.BooleanField(read_only=True)
    # The raw key is hashed at rest: it is only populated in the response that
    # creates the token, and is null on every subsequent list/retrieve.
    token_key = serializers.CharField(read_only=True)

    class Meta:
        model = APIToken
        fields = [
            "id",
            "token_name",
            "token_key",
            "token_prefix",
            "owner_username",
            "expiration_date",
            "allowed_models",
            "is_active",
            "is_expired",
            "is_valid",
            "created_at",
            "last_used_at",
        ]
        read_only_fields = ["token_prefix", "created_at", "last_used_at"]


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
    # Honour the model's API field exposure control: excluded fields never
    # appear in API responses nor are they writable through the API.
    excluded = list(getattr(model_class, "api_exclude_fields", []) or [])
    meta_attrs = {"model": model_class}
    if excluded:
        meta_attrs["exclude"] = excluded
    else:
        meta_attrs["fields"] = "__all__"
    meta_class = type("Meta", (), meta_attrs)
    write_fields = getattr(model_class, "api_write_fields", None)
    serializer_class = type(
        f"{model_class.__name__}Serializer",
        (WriteFieldAllowlistSerializerMixin, PIIMaskingSerializerMixin, serializers.ModelSerializer),
        {
            "Meta": meta_class,
            "_snap_model": model_class,
            "_snap_write_fields": list(write_fields) if write_fields is not None else None,
        },
    )
    return serializer_class


_serializer_cache: dict = {}


def get_serializer_for_model(app_label: str, model_name: str):
    cache_key = f"{app_label}.{model_name}"
    if cache_key not in _serializer_cache:
        model_class = apps.get_model(app_label, model_name)
        _serializer_cache[cache_key] = build_model_serializer(model_class)
    return _serializer_cache[cache_key]

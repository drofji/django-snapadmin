"""
snapadmin/api/serializers.py

DRF serializers for the SnapAdmin auto-generated REST API.
"""

import copy
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model
from rest_framework import serializers
from rest_framework.utils import model_meta

from snapadmin.api.exceptions import as_drf_validation_error
from snapadmin.masking import (
    get_masked_fields,
    mask_field,
    user_can_access_field,
    user_can_view_pii,
)
from snapadmin.models import APIToken
from snapadmin.registry import get_model_meta


def _bound_model(serializer: object) -> type | None:
    """The model a serializer is for: ``_snap_model`` when SnapAdmin built the class,
    otherwise ``Meta.model``.

    The mixins below are public and a project's own ``ModelSerializer`` mixes
    them in without ever setting ``_snap_model``. Reading only that attribute
    turned masking and the field-permission gate silently off for such a class
    (F8) — the one case where "no model" meant "fail open".
    """
    model = getattr(serializer, "_snap_model", None)
    if model is None:
        model = getattr(getattr(serializer, "Meta", None), "model", None)
    return model


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
        model = _bound_model(self)
        if model is None:  # a plain Serializer: no model, nothing configured for it
            return data
        masked = get_masked_fields(model._meta.app_label, model._meta.model_name)
        if not masked:
            return data
        user = getattr(self.context.get("request"), "user", None)
        if user_can_view_pii(user):
            return data
        for field in masked:
            if field in data:
                data[field] = mask_field(
                    model._meta.app_label, model._meta.model_name, field, data[field], user
                )
        return data


class FieldPermissionSerializerMixin:
    """Enforces ``api_field_permissions`` reads and writes (#FUT3b).

    A **third**, orthogonal guard alongside the two above, with a deliberately
    different (louder) contract. Precedence, relative to the other two on this
    same serializer:

    * ``api_exclude_fields`` (``Meta.exclude``, unchanged) removes a field from
      the serializer entirely and wins absolutely — this mixin never even sees
      an excluded field.
    * ``api_write_fields`` (:class:`WriteFieldAllowlistSerializerMixin`,
      unchanged) *silently* forces a non-allowlisted field read-only; that
      older contract is untouched by this mixin.
    * ``api_field_permissions`` (this mixin): a field named here whose caller
      lacks the declared permission is **absent** from a read response (never
      ``null``, never an error — see ``to_representation``) and rejected with
      an explicit ``400`` naming the field on a write (see ``validate``).
      Silence on read (nothing leaked about the field's existence), noise on
      write (a dropped write is a data-loss bug the caller cannot detect
      otherwise) — a deliberate asymmetry, not an inconsistency.

    Composes with :class:`PIIMaskingSerializerMixin` in a fixed order (base
    class order in ``build_model_serializer``): the permission gate removes
    denied fields first: masking then only ever runs on what survives.
    """

    _snap_model = None  # set by build_model_serializer

    def to_representation(self, instance):
        data = super().to_representation(instance)
        model = _bound_model(self)
        if model is None:  # a plain Serializer: no model, nothing configured for it
            return data
        user = getattr(self.context.get("request"), "user", None)
        for field in list(data):
            if not user_can_access_field(user, model, field, write=False):
                data.pop(field, None)
        return data

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = _bound_model(self)
        if model is None:  # a plain Serializer: no model, nothing configured for it
            return attrs
        user = getattr(self.context.get("request"), "user", None)
        denied = [
            field for field in attrs if not user_can_access_field(user, model, field, write=True)
        ]
        if denied:
            raise serializers.ValidationError(
                {field: "You do not have permission to set this field." for field in denied}
            )
        return attrs


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


if TYPE_CHECKING:  # pragma: no cover - typing only
    from rest_framework.serializers import Serializer as _SerializerBase
else:
    _SerializerBase = object


#: What the mixin below is mixed *into*. At runtime it stays a plain mixin
#: (``object``); for the type checker it is the DRF class whose hooks it calls
#: through ``super()``, which is what makes those calls checkable instead of
#: silently untyped.
class ModelCleanSerializerMixin(_SerializerBase):
    """Runs the model's own ``full_clean()`` on the API write path (#EXT1k).

    Off by default, opted into per model with ``api_full_clean = True`` or
    project-wide with ``SNAPADMIN_API_FULL_CLEAN`` — both resolved by
    :func:`~snapadmin.registry.get_model_meta`, read per request rather than
    when the serializer class is built, so a settings override applies to the
    cached class too.

    **Why it is off by default.** A project upgrading into this would see writes
    the API used to accept start answering 400. That is the *correct* answer —
    the admin was already refusing them — but it is a behaviour change, and a
    behaviour change that lands silently on someone else's production API is not
    a bug fix. Turning it on is one line; discovering it turned itself on is an
    outage.

    **What it validates.** ``clean_fields()``, then ``clean()``, then the
    model's constraints — scoped with ``exclude`` to the fields this serializer
    can actually write. That scoping is not a nicety: an ``auto_now_add``
    column is ``None`` on an unsaved row, and validating it would reject every
    single create with an error no client could act on. It mirrors what a
    ``ModelForm`` excludes for exactly the same reason.

    **What it leaves alone.** Uniqueness — ``validate_unique=False``, because
    DRF's ``UniqueValidator`` and ``UniqueTogetherValidator`` are already on the
    generated serializer and a second pass would report the same clash twice.

    A ``ValidationError`` raised here is translated by
    :func:`~snapadmin.api.exceptions.as_drf_validation_error`, so field names
    survive into the 400 and a bare message lands under ``non_field_errors``.
    """

    _snap_model: type[Model] | None = None

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        model = self._snap_model
        if model is None or not get_model_meta(model, "api_full_clean", False):
            return attrs
        instance = self._snap_instance_for_validation(model, attrs)
        try:
            instance.full_clean(
                exclude=self._snap_validation_exclusions(model),
                validate_unique=False,
            )
        except DjangoValidationError as exc:
            raise as_drf_validation_error(exc) from exc
        return attrs

    def _snap_instance_for_validation(self, model: type[Model], attrs: dict[str, Any]) -> Model:
        """The row the rule is checked against: the merged result of the write.

        On a create that is a fresh unsaved instance; on a PATCH it is a copy of
        the stored row with the submitted fields applied, so a cross-field rule
        sees the whole row rather than the handful of fields the request
        happened to carry. The copy is what keeps a rejected write from leaving
        the in-memory instance half-updated.

        To-many relations are dropped: they cannot be assigned before the row
        has a primary key, and Django's own ``full_clean()`` does not look at
        them either.
        """
        info = model_meta.get_field_info(model)
        assignable = {
            name: value
            for name, value in attrs.items()
            if (name in info.fields_and_pk or name in info.relations)
            and not (name in info.relations and info.relations[name].to_many)
        }
        if self.instance is None:
            return model(**assignable)
        instance = copy.copy(self.instance)
        for name, value in assignable.items():
            setattr(instance, name, value)
        return instance

    def _snap_validation_exclusions(self, model: type[Model]) -> list[str]:
        """Model fields this serializer cannot write, and so must not judge."""
        writable = {
            field.source or name for name, field in self.fields.items() if not field.read_only
        }
        return [f.name for f in model._meta.fields if f.name not in writable]


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
            "allowed_scopes",
            "is_active",
            "is_expired",
            "is_valid",
            "created_at",
            "last_used_at",
        ]
        read_only_fields = ["token_prefix", "created_at", "last_used_at"]


class APITokenRenameSerializer(serializers.ModelSerializer):
    """The one edit a token accepts after creation: its name (#EXT1p).

    Any other key in the body is rejected by name rather than ignored, so a
    client cannot believe it changed a token's scope or expiry through a
    request that only renamed it. Scope and expiry changes mean a new token.
    """

    class Meta:
        model = APIToken
        fields = ["token_name"]
        extra_kwargs = {"token_name": {"required": True, "allow_blank": False}}

    def validate(self, attrs):
        unexpected = sorted(set(self.initial_data) - {"token_name"})
        if unexpected:
            raise serializers.ValidationError(
                {
                    name: "Only token_name can be changed; create a new token instead."
                    for name in unexpected
                }
            )
        return attrs


class APITokenCreateSerializer(serializers.ModelSerializer):
    expires_in_days = serializers.IntegerField(
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = APIToken
        fields = ["token_name", "allowed_models", "allowed_scopes", "expires_in_days"]

    def create(self, validated_data):
        expires_in_days = validated_data.pop("expires_in_days", None)
        request = self.context["request"]
        return APIToken.create_for_user(
            user=request.user,
            token_name=validated_data["token_name"],
            allowed_models=validated_data.get("allowed_models", []),
            allowed_scopes=validated_data.get("allowed_scopes", []),
            expires_in_days=expires_in_days,
        )


def build_model_serializer(model_class):
    # Honour the model's API field exposure control: excluded fields never
    # appear in API responses nor are they writable through the API.
    excluded = list(get_model_meta(model_class, "api_exclude_fields", []) or [])
    meta_attrs = {"model": model_class}
    if excluded:
        meta_attrs["exclude"] = excluded
    else:
        meta_attrs["fields"] = "__all__"
    meta_class = type("Meta", (), meta_attrs)
    write_fields = get_model_meta(model_class, "api_write_fields", None)
    serializer_class = type(
        f"{model_class.__name__}Serializer",
        (
            WriteFieldAllowlistSerializerMixin,
            PIIMaskingSerializerMixin,
            FieldPermissionSerializerMixin,
            ModelCleanSerializerMixin,
            serializers.ModelSerializer,
        ),
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

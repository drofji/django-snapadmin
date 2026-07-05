"""
snapadmin/api/users.py

Optional, admin-only user-management API.

Off by default — enable with ``SNAPADMIN_USER_API_ENABLED = True``. Routes
(mounted under the SnapAdmin API prefix, e.g. ``/api/``):

    GET/POST      users/                     list / create users
    GET/PATCH/PUT/DELETE users/<pk>/         manage one user
    POST          users/<pk>/set-password/   {"password": "..."}
    POST          users/<pk>/permissions/    {"permissions": ["app.codename", ...]}
    GET           permissions/               all assignable permissions (for pickers)

Every endpoint requires an authenticated **staff** user (``IsAdminUser``) on
top of whatever authentication ``SNAPADMIN_API_AUTHENTICATION_CLASSES``
provides. Works with a custom ``AUTH_USER_MODEL``.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from snapadmin.api.authentication import SnapAPIAuthMixin

UserModel = get_user_model()

# Fields exposed when the (possibly custom) user model actually defines them.
_OPTIONAL_USER_FIELDS = ("email", "first_name", "last_name", "date_joined", "last_login")


def _user_fields() -> list[str]:
    model_fields = {f.name for f in UserModel._meta.get_fields()}
    fields = ["id", UserModel.USERNAME_FIELD, "is_active", "is_staff", "is_superuser"]
    fields += [f for f in _OPTIONAL_USER_FIELDS if f in model_fields]
    return fields


class SnapUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = UserModel
        # fields + read_only_fields are set below — they depend on which
        # optional fields the concrete (possibly custom) user model defines.
        fields = None
        read_only_fields = None

    def get_permissions(self, obj) -> list[str]:
        return sorted(
            f"{p.content_type.app_label}.{p.codename}"
            for p in obj.user_permissions.select_related("content_type")
        )

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = UserModel(**validated_data)
        user.set_password(password) if password else user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


_RESOLVED_USER_FIELDS = _user_fields()
SnapUserSerializer.Meta.fields = _RESOLVED_USER_FIELDS + ["password", "permissions"]
# Only mark auto-managed timestamps read-only when the model actually has them.
SnapUserSerializer.Meta.read_only_fields = [
    f for f in ("date_joined", "last_login") if f in _RESOLVED_USER_FIELDS
]


class PermissionSerializer(serializers.ModelSerializer):
    app_label = serializers.CharField(source="content_type.app_label", read_only=True)
    full_codename = serializers.SerializerMethodField()

    class Meta:
        model = Permission
        fields = ["id", "app_label", "codename", "full_codename", "name"]

    def get_full_codename(self, obj) -> str:
        return f"{obj.content_type.app_label}.{obj.codename}"


class SnapUserViewSet(SnapAPIAuthMixin, viewsets.ModelViewSet):
    """Admin-only CRUD over the project's user model."""

    permission_classes = [permissions.IsAdminUser]
    serializer_class = SnapUserSerializer

    def get_queryset(self):
        return UserModel._default_manager.all().order_by("pk")

    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        password = request.data.get("password", "")
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            return Response({"password": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(password)
        user.save(update_fields=["password"])
        return Response({"status": "password set"})

    @action(detail=True, methods=["post"], url_path="permissions")
    def set_permissions(self, request, pk=None):
        """Replace the user's direct permissions with the given codename list."""
        user = self.get_object()
        wanted = request.data.get("permissions", None)
        if not isinstance(wanted, list):
            return Response(
                {"permissions": ["Expected a list of 'app_label.codename' strings."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolved = []
        for entry in wanted:
            app_label, _, codename = str(entry).partition(".")
            try:
                resolved.append(
                    Permission.objects.get(
                        content_type__app_label=app_label, codename=codename
                    )
                )
            except Permission.DoesNotExist:
                return Response(
                    {"permissions": [f"Unknown permission: {entry}"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        user.user_permissions.set(resolved)
        return Response({"status": "permissions set", "count": len(resolved)})


class PermissionListView(SnapAPIAuthMixin, APIView):
    """All assignable permissions — feeds frontend permission pickers."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        queryset = Permission.objects.select_related("content_type").order_by(
            "content_type__app_label", "codename"
        )
        return Response(
            {
                "permissions": PermissionSerializer(queryset, many=True).data,
                "count": queryset.count(),
            }
        )

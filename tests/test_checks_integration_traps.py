"""System checks for integration traps from the second field report (#EXT2b/c/g/h).

Each check here turns a failure that used to be silent into a ``manage.py
check`` message:

* ``snapadmin.E027`` — ``SnapModel.objects`` hides a base class's own manager;
* ``snapadmin.W023`` — backups configured, but switched off;
* ``snapadmin.W024`` — ``env`` requested, but there is no env file to bundle;
* ``snapadmin.W025`` — masked fields served by an admin that does not mask;
* ``snapadmin.W026`` — ``admin_sections`` set, though nothing reads it.
"""

import pytest
from celery.schedules import crontab
from django.contrib import admin
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from snapadmin import checks
from snapadmin.es import EsManager
from snapadmin.models import PIIMaskingAdminMixin, SnapModel


def _ids(messages) -> list[str]:
    return [m.id for m in messages]


# ─────────────────────────────────────────────────────────────────────────────
# E027 — manager shadowing
# ─────────────────────────────────────────────────────────────────────────────

class OwnerScopedManager(models.Manager):
    """Stands in for a scoping manager: filters rows by owner in real projects."""


@pytest.fixture
def isolated_models():
    """Model classes declared inside a test stay out of the real app registry."""
    with isolate_apps("demo"):
        yield


@pytest.mark.usefixtures("isolated_models")
class TestManagerShadowing:
    def _scoped_mixin(self):
        class OwnerScopedMixin(models.Model):
            objects = OwnerScopedManager()

            class Meta:
                abstract = True
                app_label = "demo"

        return OwnerScopedMixin

    def test_the_reported_shape_is_detected(self):
        """``class X(BaseModel(SnapModel), ScopedMixin)`` — the reporter's MRO."""
        mixin = self._scoped_mixin()

        class BaseModel(SnapModel):
            class Meta:
                abstract = True
                app_label = "demo"

        class Order(BaseModel, mixin):
            class Meta:
                app_label = "demo"

        assert type(Order._default_manager) is EsManager
        base, manager = checks._shadowed_objects_manager(Order)
        assert base is mixin
        assert isinstance(manager, OwnerScopedManager)

    def test_mixin_first_in_the_mro_is_fine(self):
        mixin = self._scoped_mixin()

        class Order(mixin, SnapModel):
            class Meta:
                app_label = "demo"

        assert isinstance(Order._default_manager, OwnerScopedManager)
        assert checks._shadowed_objects_manager(Order) is None

    def test_a_manager_declared_on_the_model_itself_is_a_deliberate_choice(self):
        mixin = self._scoped_mixin()

        class Order(SnapModel, mixin):
            objects = EsManager()

            class Meta:
                app_label = "demo"

        assert checks._shadowed_objects_manager(Order) is None

    def test_a_combined_manager_on_the_model_clears_it(self):
        mixin = self._scoped_mixin()

        class OrderManager(OwnerScopedManager, EsManager):
            pass

        class Order(SnapModel, mixin):
            objects = OrderManager()

            class Meta:
                app_label = "demo"

        assert isinstance(Order._default_manager, OwnerScopedManager)
        assert checks._shadowed_objects_manager(Order) is None

    def test_a_base_declaring_another_es_manager_is_not_reported(self):
        class EsMixin(models.Model):
            objects = EsManager()

            class Meta:
                abstract = True
                app_label = "demo"

        class Order(SnapModel, EsMixin):
            class Meta:
                app_label = "demo"

        assert checks._shadowed_objects_manager(Order) is None

    def test_a_plain_django_model_is_out_of_scope(self):
        mixin = self._scoped_mixin()

        class Plain(mixin):
            class Meta:
                app_label = "demo"

        assert checks._shadowed_objects_manager(Plain) is None

    def test_the_check_reports_an_error_naming_model_base_and_manager(self, monkeypatch):
        mixin = self._scoped_mixin()

        class Order(SnapModel, mixin):
            class Meta:
                app_label = "demo"

        monkeypatch.setattr(checks.apps, "get_models", lambda: [Order])

        [message] = checks.check_snap_model_manager_shadowing(None)

        assert message.id == "snapadmin.E027"
        assert message.level == 40  # ERROR
        assert "demo.Order" in message.msg
        assert "OwnerScopedMixin" in message.msg
        assert "OwnerScopedManager" in message.msg
        assert "SILENCED_SYSTEM_CHECKS" in message.hint
        assert message.obj is Order


@pytest.mark.usefixtures("isolated_models")
class TestTenantScopedModelKeepsTheScopingManager:
    """The reverse of the shadowing case: tenant scoping is enforced in
    ``EsManager.get_queryset``, so a ``tenant_scoped`` SnapModel whose ``objects``
    resolves to any other manager — a mixin listed first, or one declared on the
    model — silently serves every tenant's rows."""

    def _tenant_model(self, *bases, **attrs):
        class Meta:
            app_label = "demo"

        namespace = {
            "__module__": __name__, "Meta": Meta, "tenant_scoped": True,
            "tenant_id": models.IntegerField(null=True), **attrs,
        }
        return type("TenantOrder", bases, namespace)

    def test_a_plain_manager_on_a_tenant_scoped_model_is_reported(self):
        model = self._tenant_model(SnapModel, objects=OwnerScopedManager())

        assert checks._tenant_scoping_manager_lost(model) is True

    def test_a_mixin_listed_first_is_reported(self):
        class OwnerScopedMixin(models.Model):
            objects = OwnerScopedManager()

            class Meta:
                abstract = True
                app_label = "demo"

        model = self._tenant_model(OwnerScopedMixin, SnapModel)

        assert checks._tenant_scoping_manager_lost(model) is True

    def test_a_combined_manager_keeps_scoping(self):
        class Combined(OwnerScopedManager, EsManager):
            pass

        model = self._tenant_model(SnapModel, objects=Combined())

        assert checks._tenant_scoping_manager_lost(model) is False

    def test_the_default_es_manager_keeps_scoping(self):
        assert checks._tenant_scoping_manager_lost(self._tenant_model(SnapModel)) is False

    def test_a_model_that_is_not_tenant_scoped_is_out_of_scope(self):
        model = self._tenant_model(SnapModel, tenant_scoped=False, objects=OwnerScopedManager())

        assert checks._tenant_scoping_manager_lost(model) is False

    def test_the_check_reports_it_as_e027(self, monkeypatch):
        model = self._tenant_model(SnapModel, objects=OwnerScopedManager())
        monkeypatch.setattr(checks.apps, "get_models", lambda: [model])

        [message] = checks.check_snap_model_manager_shadowing(None)

        assert message.id == "snapadmin.E027"
        assert "tenant_scoped" in message.msg
        assert "OwnerScopedManager" in message.msg
        assert "every tenant" in message.msg


@pytest.mark.django_db
def test_the_demo_project_has_no_shadowed_manager():
    assert checks.check_snap_model_manager_shadowing(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# W023 / W024 — backups
# ─────────────────────────────────────────────────────────────────────────────

_BACKUP_BEAT = {"backups": {"task": "snapadmin.run_db_backups", "schedule": crontab(minute=30)}}
_OTHER_BEAT = {"digest": {"task": "snapadmin.send_error_digest", "schedule": crontab(minute=0)}}


class TestBackupConfiguredButDisabled:
    @override_settings(SNAPADMIN_BACKUP_ENABLED=False, CELERY_BEAT_SCHEDULE=_BACKUP_BEAT)
    def test_a_beat_entry_alone_is_evidence(self):
        [message] = checks.check_backup_configured_but_disabled(None)

        assert message.id == "snapadmin.W023"
        assert "snapadmin.run_db_backups" in message.msg

    @override_settings(
        SNAPADMIN_BACKUP_ENABLED=False, CELERY_BEAT_SCHEDULE={},
        SNAPADMIN_BACKUP_SFTP_HOST="backup.invalid",
        SNAPADMIN_BACKUP_AGE_RECIPIENTS=["age1example"],
    )
    def test_destinations_and_recipients_are_evidence(self):
        [message] = checks.check_backup_configured_but_disabled(None)

        assert "SNAPADMIN_BACKUP_SFTP_HOST" in message.msg
        assert "SNAPADMIN_BACKUP_AGE_RECIPIENTS" in message.msg
        assert "Celery Beat" not in message.msg

    @override_settings(SNAPADMIN_BACKUP_ENABLED=False, CELERY_BEAT_SCHEDULE=_OTHER_BEAT)
    def test_a_project_that_never_configured_backups_hears_nothing(self):
        assert checks.check_backup_configured_but_disabled(None) == []

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True, CELERY_BEAT_SCHEDULE=_BACKUP_BEAT)
    def test_enabled_backups_hear_nothing(self):
        assert checks.check_backup_configured_but_disabled(None) == []


class TestBackupEnvFilePresent:
    @override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_INCLUDE=["db", "env"],
                       SNAPADMIN_BACKUP_ENV_FILE="")
    def test_env_requested_with_no_file_setting(self):
        [message] = checks.check_backup_env_file_present(None)

        assert message.id == "snapadmin.W024"
        assert "SNAPADMIN_BACKUP_ENV_FILE is not set" in message.msg

    def test_env_requested_with_a_path_that_is_not_a_file(self, tmp_path):
        missing = tmp_path / "missing.env"
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_INCLUDE=["env"],
                               SNAPADMIN_BACKUP_ENV_FILE=str(missing)):
            [message] = checks.check_backup_env_file_present(None)

        assert str(missing) in message.msg

    def test_a_directory_is_not_an_env_file(self, tmp_path):
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_INCLUDE=["env"],
                               SNAPADMIN_BACKUP_ENV_FILE=str(tmp_path)):
            assert _ids(checks.check_backup_env_file_present(None)) == ["snapadmin.W024"]

    def test_an_existing_env_file_is_fine(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=x\n")
        with override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_INCLUDE=["env"],
                               SNAPADMIN_BACKUP_ENV_FILE=str(env_file)):
            assert checks.check_backup_env_file_present(None) == []

    @override_settings(SNAPADMIN_BACKUP_ENABLED=True, SNAPADMIN_BACKUP_INCLUDE=["db"],
                       SNAPADMIN_BACKUP_ENV_FILE="")
    def test_env_not_requested(self):
        assert checks.check_backup_env_file_present(None) == []

    @override_settings(SNAPADMIN_BACKUP_ENABLED=False, SNAPADMIN_BACKUP_INCLUDE=["env"],
                       SNAPADMIN_BACKUP_ENV_FILE="")
    def test_disabled_backups_are_w023_s_business(self):
        assert checks.check_backup_env_file_present(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# W025 — masking on hand-written admins
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMaskedModelsUseMaskingAdmin:
    @pytest.fixture
    def user_model_admin(self, monkeypatch):
        """Swap the registered admin for ``auth.User``; restored by monkeypatch."""
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        def install(admin_cls):
            monkeypatch.setitem(admin.site._registry, user_model, admin_cls(user_model, admin.site))
            return user_model

        return install

    @override_settings(SNAPADMIN_MASKED_FIELDS={"auth.user": ["email"]})
    def test_a_hand_written_admin_without_the_mixin_is_reported(self, user_model_admin):
        class PlainUserAdmin(admin.ModelAdmin):
            pass

        user_model = user_model_admin(PlainUserAdmin)

        [message] = checks.check_masked_models_use_masking_admin(None)

        assert message.id == "snapadmin.W025"
        assert user_model._meta.label in message.msg
        assert "PIIMaskingAdminMixin" in message.hint

    @override_settings(SNAPADMIN_MASKED_FIELDS={"auth.user": ["email"]})
    def test_a_hand_written_admin_with_the_mixin_is_fine(self, user_model_admin):
        class MaskingUserAdmin(PIIMaskingAdminMixin, admin.ModelAdmin):
            pass

        user_model_admin(MaskingUserAdmin)

        assert checks.check_masked_models_use_masking_admin(None) == []

    @override_settings(SNAPADMIN_MASKED_FIELDS={})
    def test_no_masked_fields_no_warning(self, user_model_admin):
        class PlainUserAdmin(admin.ModelAdmin):
            pass

        user_model_admin(PlainUserAdmin)

        assert checks.check_masked_models_use_masking_admin(None) == []

    def test_the_demo_project_is_clean(self):
        assert checks.check_masked_models_use_masking_admin(None) == []


# ─────────────────────────────────────────────────────────────────────────────
# W026 — admin_sections
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminSectionsDeprecated:
    def test_a_registered_model_setting_it_is_warned(self, monkeypatch):
        from demo.apps.shop.models import Product

        monkeypatch.setattr(Product, "admin_sections", ["General"])

        [message] = checks.check_admin_sections_deprecated(None)

        assert message.id == "snapadmin.W026"
        assert Product._meta.label in message.msg
        assert "deprecated" in message.msg

    def test_the_default_empty_list_is_silent(self):
        assert checks.check_admin_sections_deprecated(None) == []


def test_every_new_check_is_registered():
    for check in (
        checks.check_snap_model_manager_shadowing,
        checks.check_backup_configured_but_disabled,
        checks.check_backup_env_file_present,
        checks.check_masked_models_use_masking_admin,
        checks.check_admin_sections_deprecated,
    ):
        assert check in checks.ALL_CHECKS

"""
tests/test_pii_access_check.py — who may see raw PII is the project's call (#EXT3t).

``snapadmin.masking.user_can_view_pii`` — the one decision every masking surface asks
(admin, REST, GraphQL, exports, audit, the subject request) — was hard-wired to
``user.has_perm("snapadmin.view_raw_pii")``. A project whose access model is not
Django permissions (an SSO role claim, a membership table) had to mirror it into a
permission. ``SNAPADMIN_PII_ACCESS_CHECK`` — a callable ``(user) -> bool`` or its dotted
path — now replaces that one step. Unset keeps the permission. Anonymous and inactive
users stay masked and superusers stay unmasked whatever the callable says; a callable
that raises fails **closed** (masked) and is logged; one that cannot be imported is
reported at startup (``snapadmin.W035``).

The class guard: an ``ast`` scan keeps the permission name inside the decision
function — the subject-request command checked it on its own, so a configured check
would have been ignored there.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Permission

from snapadmin import checks
from snapadmin.masking import user_can_view_pii


def dpo_only(user) -> bool:
    """A project's rule: data-protection officers, by username."""
    return user.username.startswith("dpo")


def broken(user) -> bool:
    raise RuntimeError("role service unreachable")


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user("clerk", "clerk@example.com", "pw", is_staff=True)


@pytest.fixture
def dpo(django_user_model):
    return django_user_model.objects.create_user("dpo_ann", "ann@example.com", "pw", is_staff=True)


@pytest.fixture
def permission_holder(django_user_model):
    user = django_user_model.objects.create_user("holder", "h@example.com", "pw", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_raw_pii"))
    return django_user_model.objects.get(pk=user.pk)


@pytest.mark.django_db
class TestUnsetKeepsThePermission:
    def test_a_permission_holder_sees_raw(self, permission_holder):
        assert user_can_view_pii(permission_holder) is True

    def test_anyone_else_is_masked(self, staff):
        assert user_can_view_pii(staff) is False


@pytest.mark.django_db
class TestAConfiguredCheckDecides:
    def test_the_callable_unmasks_whom_it_names(self, settings, dpo, staff):
        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only

        assert user_can_view_pii(dpo) is True
        assert user_can_view_pii(staff) is False

    def test_it_replaces_the_permission(self, settings, permission_holder):
        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only

        assert user_can_view_pii(permission_holder) is False

    def test_a_dotted_path_works_too(self, settings, dpo):
        settings.SNAPADMIN_PII_ACCESS_CHECK = "tests.test_pii_access_check.dpo_only"

        assert user_can_view_pii(dpo) is True

    def test_a_superuser_stays_unmasked(self, settings, admin_user):
        settings.SNAPADMIN_PII_ACCESS_CHECK = lambda user: False

        assert user_can_view_pii(admin_user) is True

    def test_an_inactive_user_stays_masked(self, settings, dpo):
        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only
        dpo.is_active = False

        assert user_can_view_pii(dpo) is False

    def test_an_anonymous_user_stays_masked(self, settings):
        settings.SNAPADMIN_PII_ACCESS_CHECK = lambda user: True

        assert user_can_view_pii(SimpleNamespace(is_authenticated=False)) is False

    def test_a_per_field_rule_permission_still_unlocks_its_field(self, settings, staff):
        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only
        settings.SNAPADMIN_MASKING_RULES = {
            "demo.CustomerProfile": {"bio": {"permission": "demo.view_customerprofile"}},
        }
        staff.user_permissions.add(Permission.objects.get(codename="view_customerprofile"))
        staff = type(staff).objects.get(pk=staff.pk)

        assert user_can_view_pii(staff, "demo.CustomerProfile.bio") is True
        assert user_can_view_pii(staff) is False

    def test_a_raising_check_fails_closed_and_is_logged(self, settings, dpo):
        from structlog.testing import capture_logs

        settings.SNAPADMIN_PII_ACCESS_CHECK = broken

        with capture_logs() as emitted:
            allowed = user_can_view_pii(dpo)

        assert allowed is False
        assert [(e["event"], e["log_level"], e["error"]) for e in emitted] == [
            ("snapadmin.pii_access_check_failed", "warning", "role service unreachable"),
        ]

    def test_a_blank_value_from_the_environment_means_unset(self, settings, permission_holder):
        settings.SNAPADMIN_PII_ACCESS_CHECK = ""

        assert user_can_view_pii(permission_holder) is True

    def test_an_unimportable_path_fails_closed(self, settings, dpo):
        settings.SNAPADMIN_PII_ACCESS_CHECK = "tests.no_such_module.check"

        assert user_can_view_pii(dpo) is False

    def test_a_truthy_non_bool_answer_counts_as_true(self, settings, dpo):
        settings.SNAPADMIN_PII_ACCESS_CHECK = lambda user: "yes"

        assert user_can_view_pii(dpo) is True


@pytest.mark.django_db
class TestTheSurfacesFollowIt:
    def test_the_rest_api_unmasks_for_the_configured_user(self, client, settings, dpo, staff):
        from demo.apps.shop.models import CustomerProfile

        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only
        for user in (dpo, staff):
            user.user_permissions.add(Permission.objects.get(codename="view_customerprofile"))
        CustomerProfile._base_manager.create(bio="Allergic to penicillin")
        settings.SNAPADMIN_MASKING_RULES = {"demo.CustomerProfile": {"bio": {"replacement": "[redacted]"}}}

        bios = {}
        for user in (dpo, staff):
            client.force_login(user)
            response = client.get("/api/models/demo/CustomerProfile/")
            assert response.status_code == 200, response.content
            bios[user.username] = response.json()["results"][0]["bio"]

        assert bios == {"dpo_ann": "Allergic to penicillin", "clerk": "[redacted]"}

    def test_the_subject_request_operator_gate_follows_it(self, settings, dpo, customer):
        from django.core.management import call_command

        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only

        from io import StringIO

        output = StringIO()
        call_command(
            "snapadmin_subject_request", "export", "--model", "demo.Customer",
            "--identifier", customer.email, "--user", dpo.username, stdout=output,
        )

        assert "EXPORTED demo.Customer: 1 row(s)" in output.getvalue()

    def test_the_subject_request_refuses_whom_the_check_refuses(
        self, settings, permission_holder, customer
    ):
        from django.core.management import CommandError, call_command

        settings.SNAPADMIN_PII_ACCESS_CHECK = dpo_only

        with pytest.raises(CommandError, match="raw PII"):
            call_command(
                "snapadmin_subject_request", "export", "--model", "demo.Customer",
                "--identifier", customer.email, "--user", permission_holder.username,
            )


class TestTheStartupCheck:
    def test_unset_is_quiet(self):
        assert checks.check_pii_access_check(None) == []

    def test_an_importable_callable_is_quiet(self, settings):
        settings.SNAPADMIN_PII_ACCESS_CHECK = "tests.test_pii_access_check.dpo_only"

        assert checks.check_pii_access_check(None) == []

    def test_an_unimportable_path_warns_that_everyone_is_masked(self, settings):
        settings.SNAPADMIN_PII_ACCESS_CHECK = "tests.no_such_module.check"

        (message,) = checks.check_pii_access_check(None)

        assert message.id == "snapadmin.W035"
        assert "masked for everyone but superusers" in message.msg

    def test_a_value_that_is_not_callable_warns(self, settings):
        settings.SNAPADMIN_PII_ACCESS_CHECK = 42

        assert [m.id for m in checks.check_pii_access_check(None)] == ["snapadmin.W035"]

    def test_it_is_registered(self):
        assert checks.check_pii_access_check in checks.ALL_CHECKS


# ── The class guard: the permission name lives in the decision function only ─

PACKAGE = Path(__file__).resolve().parent.parent / "snapadmin"
NAMES = {"snapadmin.view_raw_pii", "view_raw_pii"}

#: Where the permission may be named: the constant, the decision function, and the
#: model's Meta that declares the permission.
ALLOWED = {
    ("snapadmin/masking.py", "<module>"),
    ("snapadmin/masking.py", "user_can_view_pii"),
    ("snapadmin/models.py", "Meta"),
}


def _references() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in PACKAGE.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        relative = str(path.relative_to(PACKAGE.parent))

        def visit(node, scope: str) -> None:
            for child in ast.iter_child_nodes(node):
                inner = (
                    child.name
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    else scope
                )
                if (isinstance(child, ast.Constant) and child.value in NAMES) or (
                    isinstance(child, (ast.Name, ast.Attribute))
                    and getattr(child, "id", getattr(child, "attr", None)) == "PII_PERMISSION"
                ):
                    found.add((relative, scope))
                visit(child, inner)

        visit(tree, "<module>")
    return found


def test_only_the_decision_function_names_the_permission():
    unexpected = _references() - ALLOWED

    assert not unexpected, (
        f"review: {sorted(unexpected)} name the raw-PII permission directly — ask "
        "snapadmin.masking.user_can_view_pii() so SNAPADMIN_PII_ACCESS_CHECK applies there too"
    )

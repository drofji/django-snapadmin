"""
tests/test_masking_pending_apps.py — masking an app that is not installed yet (#EXT3s).

``SNAPADMIN_MASKED_FIELDS`` / ``SNAPADMIN_MASKING_RULES`` refuse a key that does not
resolve to an installed model (``snapadmin.E001`` / ``E003``), on purpose: a typo fails
*open* — nothing is masked and nothing says so. A project rolling out module by
module, though, writes the masking for an app before the app is installed, and the
Error stopped ``manage.py check`` (and every deploy that runs it) until then.

``SNAPADMIN_MASKING_PENDING_APPS`` lists the app labels whose masking is written
ahead: a key of a listed app that is **not installed** is reported as Info
``snapadmin.I002`` ("masked once installed"); a listed app that **is** installed gets
Warning ``snapadmin.W034`` (take it off the list, so a later typo is an Error
again). Nothing is downgraded without the list: a typo'd app label would otherwise
silently unmask the app it meant.

Missed because: working as designed (fail-closed); the module-by-module rollout was
not a modelled case. The class guard is the table below: every label-keyed setting ×
every case, with the id and the level each must produce.
"""

from __future__ import annotations

import pytest
from django.core import checks as django_checks

from snapadmin import checks

#: Label-keyed settings and an entry each of them accepts for one field.
SETTINGS = {
    "SNAPADMIN_MASKED_FIELDS": lambda field: [field],
    "SNAPADMIN_MASKING_RULES": lambda field: {field: {"replacement": "***"}},
}
UNRESOLVED_ID = {"SNAPADMIN_MASKED_FIELDS": "snapadmin.E001", "SNAPADMIN_MASKING_RULES": "snapadmin.E003"}

#: case → (setting key, field, pending apps, expected (id, level) per setting —
#: ``"unresolved"`` stands for that setting's E001/E003).
CASES = {
    "installed, valid": ("demo.Customer", "email", [], []),
    "installed, model typo": ("demo.Custmer", "email", [], [("unresolved", django_checks.ERROR)]),
    "not installed, unlisted": ("billing.Invoice", "iban", [], [("unresolved", django_checks.ERROR)]),
    "not installed, listed": (
        "billing.Invoice", "iban", ["billing"], [("snapadmin.I002", django_checks.INFO)],
    ),
    "listed case-insensitively": (
        "Billing.Invoice", "iban", ["billing"], [("snapadmin.I002", django_checks.INFO)],
    ),
    "listed with capitals": (
        "billing.Invoice", "iban", ["Billing"], [("snapadmin.I002", django_checks.INFO)],
    ),
    "installed, listed": ("demo.Customer", "email", ["demo"], [("snapadmin.W034", django_checks.WARNING)]),
    "installed, listed, model typo": (
        "demo.Custmer", "email", ["demo"],
        [("unresolved", django_checks.ERROR), ("snapadmin.W034", django_checks.WARNING)],
    ),
    # A typo in the *app label* is exactly what the list must never hide.
    "app label typo, the right app listed": (
        "biling.Invoice", "iban", ["billing"], [("unresolved", django_checks.ERROR)],
    ),
}


def _run(settings, setting: str, key: str, field: str, pending: list[str]):
    for name in SETTINGS:
        setattr(settings, name, {})
    setattr(settings, setting, {key: SETTINGS[setting](field)})
    settings.SNAPADMIN_MASKING_PENDING_APPS = pending
    messages = [
        *checks.check_masked_fields(None),
        *checks.check_masking_rules(None),
        *checks.check_masking_pending_apps(None),
    ]
    return sorted((message.id, message.level) for message in messages)


def test_every_label_keyed_setting_is_in_the_table():
    """A new 'app.Model'-keyed setting joins SETTINGS (and its unresolved id)."""
    assert set(SETTINGS) == set(UNRESOLVED_ID) == {
        "SNAPADMIN_MASKED_FIELDS", "SNAPADMIN_MASKING_RULES",
    }


@pytest.mark.parametrize("setting", sorted(SETTINGS))
@pytest.mark.parametrize("case", sorted(CASES))
def test_each_case_reports_its_id_and_level(settings, setting, case):
    key, field, pending, expected = CASES[case]
    wanted = sorted(
        (UNRESOLVED_ID[setting] if check_id == "unresolved" else check_id, level)
        for check_id, level in expected
    )

    assert _run(settings, setting, key, field, pending) == wanted, (
        f"review: {setting} with {case!r} — the pending-app rule in snapadmin.checks"
    )


class TestTheMessages:
    def test_the_info_names_the_key_and_says_when_it_applies(self, settings):
        settings.SNAPADMIN_MASKED_FIELDS = {"billing.Invoice": ["iban"]}
        settings.SNAPADMIN_MASKING_PENDING_APPS = ["billing"]

        (message,) = checks.check_masked_fields(None)

        assert message.msg == (
            "SNAPADMIN_MASKED_FIELDS key 'billing.Invoice' belongs to 'billing', which is listed "
            "in SNAPADMIN_MASKING_PENDING_APPS and not installed yet — it is masked once the app "
            "is installed."
        )

    def test_the_warning_names_the_installed_app(self, settings):
        settings.SNAPADMIN_MASKING_PENDING_APPS = ["demo"]

        (message,) = checks.check_masking_pending_apps(None)

        assert message.msg == (
            "SNAPADMIN_MASKING_PENDING_APPS lists 'demo', which is installed — remove it."
        )
        assert "typo" in message.hint

    def test_a_pending_list_in_the_wrong_shape_is_a_shape_error(self, settings):
        settings.SNAPADMIN_MASKING_PENDING_APPS = "billing"

        assert [m.id for m in checks.check_masking_pending_apps(None)] == ["snapadmin.E028"]

    def test_unset_is_quiet(self, settings):
        assert checks.check_masking_pending_apps(None) == []

    def test_the_checks_are_registered(self):
        assert checks.check_masking_pending_apps in checks.ALL_CHECKS

"""
tests/test_migrations_complete.py

Every model change ships with its migration.

``makemigrations --check`` was a step of the local pre-commit checklist and of
nothing else: no CI job ran it, so a model change whose migration was forgotten
would pass all of CI and reach adopters as a ``makemigrations`` that writes a
migration into *their* site-packages copy of this package — or, for the demo,
a schema the code no longer matches. As an ordinary test it runs in every
matrix job (both Django majors, whose autodetectors can disagree) and against
PostgreSQL.

It checks the package's own app and the demo's apps, by label: the settings
also install third-party apps (Celery Beat, CKEditor, Unfold's contrib apps …)
whose migrations are theirs to ship, and whose state at the old versions the
lowest-deps job installs is not this package's defect.
"""

from __future__ import annotations

import re
from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command


def _own_app_labels() -> list[str]:
    return sorted(
        config.label
        for config in apps.get_app_configs()
        if config.name == "snapadmin" or config.name.startswith("demo.")
    )


def test_the_checked_apps_are_the_package_and_the_demo():
    labels = _own_app_labels()

    assert "snapadmin" in labels and "demo" in labels
    assert not any(label in {"django_celery_beat", "unfold", "admin"} for label in labels)


@pytest.mark.django_db
def test_no_model_change_is_missing_its_migration():
    output = StringIO()

    try:
        call_command(
            "makemigrations", *_own_app_labels(), "--check", "--dry-run",
            stdout=output, stderr=output,
        )
    except SystemExit as exited:  # --check exits 1 when a migration is missing
        pytest.fail(f"makemigrations --check exited {exited.code}:\n{output.getvalue()}")

    reported = output.getvalue().strip()
    assert reported.startswith("No changes detected in apps "), reported
    assert sorted(re.findall(r"'([^']+)'", reported)) == _own_app_labels()

"""
tests/test_settings_hygiene.py — the test settings do what their comments say.

``demo/core/settings_test.py`` set ``DEFAULT_FILE_STORAGE`` to the in-memory
storage, under a comment promising "no filesystem needed". Django removed that
setting in 5.1 and ignores it silently, so the suite wrote every saved file into
``MEDIA_ROOT`` inside the repository — 1,875 of them had piled up there before
anyone looked. A setting the framework no longer reads is the same class of bug
as a filter parameter nobody reads: it fails silently.
"""

from __future__ import annotations

from django.conf import settings
from django.core.files.storage import InMemoryStorage, default_storage, storages

#: Settings Django has removed and now ignores without a word; naming one in the
#: test settings means a comment there promises something that does not happen.
REMOVED_DJANGO_SETTINGS = ("DEFAULT_FILE_STORAGE", "STATICFILES_STORAGE")


def test_saved_files_stay_in_memory_during_the_test_run():
    assert isinstance(storages["default"], InMemoryStorage), (
        "review: the test run's default storage is "
        f"{type(storages['default']).__name__} — tests would write files into MEDIA_ROOT "
        "inside the repository; set STORAGES['default'] in demo/core/settings_test.py"
    )
    assert default_storage.exists("never-written-by-any-test.txt") is False


def test_the_test_settings_name_no_setting_django_has_removed():
    import demo.core.settings_test as test_settings

    named = [name for name in REMOVED_DJANGO_SETTINGS if hasattr(test_settings, name)]

    assert not named, (
        f"review: demo/core/settings_test.py sets {named}, which Django no longer reads — "
        "the behaviour its comment promises does not happen; use STORAGES instead"
    )
    assert not [name for name in REMOVED_DJANGO_SETTINGS if hasattr(settings, name)]

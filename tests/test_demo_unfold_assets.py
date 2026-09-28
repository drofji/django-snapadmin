"""
tests/test_demo_unfold_assets.py

Every static file the demo's ``UNFOLD`` configuration points at exists.

Unfold takes these as callables that return a URL, so nothing checks them until
a browser asks: the demo shipped from its first commit with a login background
(``sample/login-bg.jpg``) and a favicon (``favicon.svg``) that were never in the
repository, and every visit to the login page — every page, for the favicon —
requested a 404 (found by the browser suite, #QA1e). The demo is the package's
live documentation, so what it configures must resolve.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders


def _static_urls(request) -> dict[str, str]:
    """``{"where in UNFOLD": url}`` for every static reference it carries."""
    unfold = settings.UNFOLD
    urls = {}
    for key in ("SITE_ICON", "SITE_LOGO"):
        for variant, url in unfold.get(key, {}).items():
            urls[f"{key}.{variant}"] = url
    for index, favicon in enumerate(unfold.get("SITE_FAVICONS", [])):
        urls[f"SITE_FAVICONS[{index}].href"] = favicon["href"]
    if "image" in unfold.get("LOGIN", {}):
        urls["LOGIN.image"] = unfold["LOGIN"]["image"]
    for key in ("STYLES", "SCRIPTS"):
        for index, url in enumerate(unfold.get(key, [])):
            urls[f"{key}[{index}]"] = url
    return {where: url(request) if callable(url) else url for where, url in urls.items()}


def test_every_static_file_the_unfold_config_names_exists(rf):
    urls = _static_urls(rf.get("/admin/"))
    assert set(urls) >= {"SITE_ICON.light", "SITE_LOGO.light", "SITE_FAVICONS[0].href", "STYLES[0]"}

    static_prefix = "/" + settings.STATIC_URL.lstrip("/")
    missing = {
        where: url
        for where, url in urls.items()
        if not (url.startswith(static_prefix) and finders.find(url[len(static_prefix):]))
    }

    assert missing == {}, f"UNFOLD names static files that do not exist: {missing}"


@pytest.mark.parametrize("missing_path", ["sample/login-bg.jpg", "favicon.svg"])
def test_the_check_reports_a_file_that_does_not_exist(missing_path):
    """The two paths the demo used to name really are absent — so the test
    above would have failed on them, rather than passing for some other reason."""
    assert finders.find(missing_path) is None

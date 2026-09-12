"""
Tests that every shipped static asset only points at files the package
actually contains (#EXT1n).

Django's ``ManifestStaticFilesStorage`` — and therefore whitenoise's
``CompressedManifestStaticFilesStorage``, the standard production
recommendation — rewrites the references inside collected CSS and JS:
``url(...)`` in stylesheets and the ``sourceMappingURL`` comment in
scripts. A reference to a file that is not on disk makes ``collectstatic``
raise ``MissingFileError`` and takes the whole deploy down; neither
``WHITENOISE_MANIFEST_STRICT = False`` nor ``manifest_strict = False``
helps, because those cover missing *manifest entries*, not missing files.

The suite cannot run ``collectstatic`` against every storage backend a
project might pick, so it pins the property that backend checks: no shipped
asset may reference a path the package does not ship.
"""

import re
from pathlib import Path

import pytest

STATIC_ROOT = Path(__file__).resolve().parent.parent / "snapadmin" / "static"

#: ``url(foo.woff2)``, with or without quotes.
CSS_URL_RE = re.compile(r"""url\(\s*(?P<quote>['"]?)(?P<url>[^)'"]+)(?P=quote)\s*\)""")

#: ``//# sourceMappingURL=chart.umd.js.map`` and its CSS ``/*# ... */`` form.
SOURCE_MAP_RE = re.compile(
    r"""(?m)^\s*(?://|/\*)#\s*sourceMappingURL\s*=\s*(?P<url>\S+?)\s*(?:\*/)?\s*$"""
)

#: References the storage backend leaves alone.
EXTERNAL_PREFIXES = ("data:", "http://", "https://", "//", "#")


def _asset_files():
    return sorted(
        path
        for path in STATIC_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".css", ".js"}
    )


def _references(path):
    """Yield ``(kind, url)`` for every rewritable reference in ``path``."""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".css":
        for match in CSS_URL_RE.finditer(source):
            yield "url()", match.group("url")
    for match in SOURCE_MAP_RE.finditer(source):
        yield "sourceMappingURL", match.group("url")


def _is_local(url):
    return not url.startswith(EXTERNAL_PREFIXES)


def _strip_query(url):
    return url.split("?", 1)[0].split("#", 1)[0]


class TestShippedAssetsResolve:
    def test_the_static_tree_is_not_empty(self):
        """A silently empty scan would make every assertion below vacuous."""
        assert _asset_files(), f"no CSS/JS assets found under {STATIC_ROOT}"

    @pytest.mark.parametrize("path", _asset_files(), ids=lambda p: p.name)
    def test_every_reference_points_at_a_shipped_file(self, path):
        missing = [
            (kind, url)
            for kind, url in _references(path)
            if _is_local(url) and not (path.parent / _strip_query(url)).is_file()
        ]
        assert not missing, (
            f"{path.relative_to(STATIC_ROOT)} references files the package does not ship: "
            + ", ".join(f"{kind} -> {url}" for kind, url in missing)
        )


class TestVendoredBundlesShipNoSourceMapComment:
    """A source map is a devtools convenience; a shipped minified bundle has
    no business pointing at one it does not carry."""

    @pytest.mark.parametrize("path", _asset_files(), ids=lambda p: p.name)
    def test_no_vendored_bundle_advertises_a_source_map(self, path):
        source = path.read_text(encoding="utf-8")
        assert "sourceMappingURL" not in source, (
            f"{path.relative_to(STATIC_ROOT)} advertises a source map; strip the comment "
            "or ship the .map file"
        )

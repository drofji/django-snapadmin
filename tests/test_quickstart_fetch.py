"""Tests for :mod:`snapadmin.quickstart.fetch` (#CLI3b) — network is always mocked."""

from __future__ import annotations

import json
import urllib.error

import pytest

from snapadmin.quickstart import QuickstartError, TagNotFoundError, fetch


class TestHttpGet:
    def test_reads_response_body(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"BODY"

        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=30.0: FakeResponse())
        assert fetch._http_get("https://example.test") == b"BODY"


class TestResolveVersion:
    def test_explicit_strips_leading_v(self):
        assert fetch.resolve_version("v1.2.3") == "1.2.3"

    def test_falls_back_to_installed(self, monkeypatch):
        monkeypatch.setattr(fetch, "installed_version", lambda: "0.1.0b4")
        assert fetch.resolve_version(None) == "0.1.0b4"

    def test_errors_when_unknown(self, monkeypatch):
        monkeypatch.setattr(fetch, "installed_version", lambda: None)
        with pytest.raises(QuickstartError):
            fetch.resolve_version(None)


class TestInstalledVersion:
    def test_missing_returns_none(self):
        # django-snapadmin isn't installed as a dist in the source checkout.
        assert fetch.installed_version() is None

    def test_present(self, monkeypatch):
        monkeypatch.setattr(fetch._im, "version", lambda name: "9.9.9")
        assert fetch.installed_version() == "9.9.9"


class TestAvailableTags:
    def test_parses_names(self, monkeypatch):
        monkeypatch.setattr(
            fetch, "_http_get", lambda url: json.dumps([{"name": "v1"}, {"name": "v2"}, {"x": 1}]).encode()
        )
        assert fetch.available_tags() == ["v1", "v2"]

    def test_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fetch, "_http_get", lambda url: (_ for _ in ()).throw(OSError("boom")))
        assert fetch.available_tags() == []

    def test_non_list_returns_empty(self, monkeypatch):
        monkeypatch.setattr(fetch, "_http_get", lambda url: b"{}")
        assert fetch.available_tags() == []


class TestDownloadDemo:
    def test_downloads_and_caches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetch, "_http_get", lambda url: b"TARBALL")
        archive = fetch.download_demo("1.0.0", cache_dir=tmp_path)
        assert archive.read_bytes() == b"TARBALL"
        assert fetch._checksum_path(archive).exists()

    def test_reuses_valid_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetch, "_http_get", lambda url: b"TARBALL")
        first = fetch.download_demo("1.0.0", cache_dir=tmp_path)

        def _boom(url):
            raise AssertionError("network should not be hit for a cached archive")

        monkeypatch.setattr(fetch, "_http_get", _boom)
        second = fetch.download_demo("1.0.0", cache_dir=tmp_path)
        assert first == second

    def test_clear_cache_forces_redownload(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetch, "_http_get", lambda url: b"A")
        fetch.download_demo("1.0.0", cache_dir=tmp_path)
        calls = []
        monkeypatch.setattr(fetch, "_http_get", lambda url: calls.append(url) or b"B")
        archive = fetch.download_demo("1.0.0", cache_dir=tmp_path, clear_cache=True)
        assert calls  # re-downloaded
        assert archive.read_bytes() == b"B"

    def test_404_raises_tag_not_found(self, tmp_path, monkeypatch):
        def _404(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

        monkeypatch.setattr(fetch, "_http_get", _404)
        monkeypatch.setattr(fetch, "available_tags", lambda: ["v0.1.0b3"])
        with pytest.raises(TagNotFoundError) as exc:
            fetch.download_demo("9.9.9", cache_dir=tmp_path)
        assert "v0.1.0b3" in str(exc.value)

    def test_other_http_error(self, tmp_path, monkeypatch):
        def _500(url):
            raise urllib.error.HTTPError(url, 500, "Server Error", None, None)

        monkeypatch.setattr(fetch, "_http_get", _500)
        with pytest.raises(QuickstartError, match="HTTP 500"):
            fetch.download_demo("1.0.0", cache_dir=tmp_path)

    def test_url_error(self, tmp_path, monkeypatch):
        def _down(url):
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(fetch, "_http_get", _down)
        with pytest.raises(QuickstartError, match="Download failed"):
            fetch.download_demo("1.0.0", cache_dir=tmp_path)


class TestIsCached:
    def test_missing_checksum(self, tmp_path):
        archive = tmp_path / "v1.tar.gz"
        archive.write_bytes(b"x")
        assert fetch._is_cached(archive) is False

    def test_mismatched_checksum(self, tmp_path):
        archive = tmp_path / "v1.tar.gz"
        archive.write_bytes(b"x")
        fetch._checksum_path(archive).write_text("deadbeef")
        assert fetch._is_cached(archive) is False

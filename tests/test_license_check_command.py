"""Tests for the ``snapadmin_license_check`` management command (#CLI2c)."""

from __future__ import annotations

import json
from io import StringIO

from django.core.management import call_command

from snapadmin.licensing import CURATED, PackageStatus, Tier

CMD = "snapadmin.management.commands.snapadmin_license_check"


def _run(**kwargs):
    out = StringIO()
    call_command("snapadmin_license_check", stdout=out, **kwargs)
    return out.getvalue()


def _status(key, installed=True):
    return PackageStatus(info=CURATED[key], installed=installed, version="1.0" if installed else None)


class TestTextReport:
    def test_default_report(self):
        out = _run()
        assert "SnapAdmin licence audit" in out
        assert "Core dependencies" in out
        assert "Django" in out
        assert "Vulnerability scan:" in out

    def test_critical_only_hides_permissive(self):
        out = _run(critical_only=True)
        assert "paramiko" in out  # LGPL 🟡
        assert "django-ckeditor-5" in out  # GPL 🔴
        assert "CKEditor" in out  # bundles note is rendered
        assert "Django  " not in out  # permissive core row is hidden

    def test_verdict_ok_when_all_permissive(self, monkeypatch):
        monkeypatch.setattr(CMD + ".scan_curated", lambda: [_status("django"), _status("elasticsearch", installed=False)])
        out = _run()
        assert "✓ OK" in out

    def test_verdict_review_when_restricted_installed(self, monkeypatch):
        monkeypatch.setattr(CMD + ".scan_curated", lambda: [_status("django"), _status("django-ckeditor-5")])
        out = _run()
        assert "⚠ review" in out
        assert "django-ckeditor-5" in out

    def test_empty_filter_message(self, monkeypatch):
        monkeypatch.setattr(CMD + ".scan_curated", lambda: [_status("django")])
        out = _run(critical_only=True)
        assert "No packages match" in out

    def test_verbose_uncurated_none(self):
        out = _run(verbose=True)
        assert "Uncurated declared dependencies:" in out
        assert "none" in out

    def test_verbose_uncurated_items(self, monkeypatch):
        monkeypatch.setattr(CMD + ".audit_uncurated", lambda: [("mystery", "GPL-3.0", Tier.RESTRICTED)])
        out = _run(verbose=True)
        assert "mystery" in out


class TestJsonReport:
    def test_json_structure(self):
        payload = json.loads(_run(as_json=True))
        assert "packages" in payload
        assert "verdict" in payload
        assert "vulnerability_scan" in payload
        assert payload["vulnerability_scan"]["ran"] is False

    def test_json_verbose_includes_uncurated(self, monkeypatch):
        monkeypatch.setattr(CMD + ".audit_uncurated", lambda: [("mystery", "GPL-3.0", Tier.RESTRICTED)])
        payload = json.loads(_run(as_json=True, verbose=True))
        assert payload["uncurated"][0]["package"] == "mystery"


class TestCompatibility:
    def test_text(self):
        out = _run(compatible_with="MIT")
        assert "Compatibility with a project licensed MIT" in out
        assert "django-ckeditor-5" in out

    def test_json(self):
        payload = json.loads(_run(compatible_with="MIT", as_json=True))
        assert payload["target"] == "MIT"
        ck = next(p for p in payload["packages"] if p["package"] == "django-ckeditor-5")
        assert ck["compatible"] is False


class TestVulnerabilityNote:
    def test_scanner_present(self, monkeypatch):
        monkeypatch.setattr("importlib.util.find_spec", lambda name: object() if name == "pip_audit" else None)
        assert "pip-audit is installed" in _run()

    def test_no_scanner(self, monkeypatch):
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        assert "no vulnerability scanner installed" in _run()

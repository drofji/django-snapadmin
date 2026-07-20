"""Tests for :mod:`snapadmin.licensing` — the ``snapadmin_license_check`` data layer (#CLI2a/b/d)."""

from __future__ import annotations

import sys
from email.message import Message
from importlib import metadata

import pytest

from snapadmin import licensing
from snapadmin.licensing import (
    CURATED,
    LicenseInfo,
    PackageStatus,
    Tier,
    _license_from_metadata,
    _normalize,
    _requirement_name,
    audit_uncurated,
    classify,
    commercial_verdict,
    is_compatible_with,
    scan_curated,
)


class TestClassify:
    @pytest.mark.parametrize(
        "spdx,tier,ok",
        [
            ("MIT", Tier.PERMISSIVE, True),
            ("BSD-3-Clause", Tier.PERMISSIVE, True),
            ("Apache-2.0", Tier.PERMISSIVE, True),
            ("MIT OR Apache-2.0", Tier.PERMISSIVE, True),
            ("LGPL-3.0", Tier.WEAK_COPYLEFT, True),
            ("LGPL-2.1", Tier.WEAK_COPYLEFT, True),
            ("MPL-2.0", Tier.WEAK_COPYLEFT, True),
            ("GPL-3.0", Tier.RESTRICTED, False),
            ("GPL-2.0-or-later OR Commercial", Tier.RESTRICTED, False),
            ("AGPL-3.0", Tier.RESTRICTED, False),
            ("SSPL-1.0", Tier.RESTRICTED, False),
            ("Proprietary", Tier.UNKNOWN, None),
            ("", Tier.UNKNOWN, None),
            ("   ", Tier.UNKNOWN, None),
        ],
    )
    def test_classify(self, spdx, tier, ok):
        assert classify(spdx) == (tier, ok)

    def test_tier_icon_and_label(self):
        assert Tier.PERMISSIVE.icon == "🟢"
        assert Tier.RESTRICTED.label == "copyleft / commercial"


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,norm",
        [("Django", "django"), ("drf_spectacular", "drf-spectacular"), ("A.B_c", "a-b-c")],
    )
    def test_normalize(self, raw, norm):
        assert _normalize(raw) == norm


class TestCuratedMap:
    def test_curated_has_core_and_extras(self):
        assert len(CURATED) == 18
        assert sum(1 for i in CURATED.values() if i.is_core) == 10
        assert sum(1 for i in CURATED.values() if not i.is_core) == 8

    def test_license_info_properties(self):
        paramiko = CURATED["paramiko"]
        assert paramiko.tier is Tier.WEAK_COPYLEFT
        assert paramiko.commercial_ok is True
        assert paramiko.is_core is False
        assert paramiko.extra == "backup"
        assert CURATED["django"].is_core is True

    def test_ckeditor_bundles_note(self):
        ck = CURATED["django-ckeditor-5"]
        assert ck.tier is Tier.RESTRICTED
        assert "CKEditor" in ck.bundles

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is stdlib only on Python 3.11+")
    def test_map_matches_pyproject(self):
        # Drift guard: the curated set must equal pyproject's declared runtime deps, and each
        # package's core/extra classification must match pyproject's optional flag + extras table.
        import tomllib

        with open("pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        deps = data["tool"]["poetry"]["dependencies"]
        declared = {_normalize(name) for name in deps if name.lower() != "python"}
        assert declared == set(CURATED)

        for name, spec in deps.items():
            if name.lower() == "python":
                continue
            optional = isinstance(spec, dict) and spec.get("optional") is True
            assert CURATED[_normalize(name)].is_core == (not optional)

        for extra_name, packages in data["tool"]["poetry"]["extras"].items():
            if extra_name == "all":
                continue
            for package in packages:
                assert CURATED[_normalize(package)].extra == extra_name


class TestScan:
    def test_scan_curated_marks_installed(self):
        statuses = scan_curated()
        assert len(statuses) == 18
        django = next(s for s in statuses if s.info.package == "Django")
        assert django.installed is True
        assert django.version is not None

    def test_installed_version_missing(self):
        assert licensing._installed_version("no-such-package-xyz-123") is None


class TestLicenseFromMetadata:
    def test_missing_distribution(self):
        assert _license_from_metadata("no-such-package-xyz-123") == ""

    def test_license_expression_preferred(self, monkeypatch):
        msg = Message()
        msg["License-Expression"] = "MIT"
        monkeypatch.setattr(metadata, "metadata", lambda name: msg)
        assert _license_from_metadata("x") == "MIT"

    def test_license_classifier(self, monkeypatch):
        msg = Message()
        msg["Classifier"] = "Programming Language :: Python"
        msg["Classifier"] = "License :: OSI Approved :: BSD License"
        monkeypatch.setattr(metadata, "metadata", lambda name: msg)
        assert _license_from_metadata("x") == "BSD License"

    def test_license_field_fallback(self, monkeypatch):
        msg = Message()
        msg["License"] = "Custom-1.0"
        monkeypatch.setattr(metadata, "metadata", lambda name: msg)
        assert _license_from_metadata("x") == "Custom-1.0"


class TestRequirementName:
    @pytest.mark.parametrize(
        "req,name",
        [
            ("celery (>=5.3.0) ; extra == 'celery'", "celery"),
            ("django>=5.2", "django"),
            ("nh3", "nh3"),
        ],
    )
    def test_requirement_name(self, req, name):
        assert _requirement_name(req) == name


class TestAuditUncurated:
    def test_no_dist_metadata_returns_empty(self):
        # ``django-snapadmin`` isn't an installed dist in the source checkout.
        assert audit_uncurated() == []

    def test_surfaces_uncurated_requirement(self, monkeypatch):
        monkeypatch.setattr(
            metadata,
            "requires",
            lambda name: [
                "celery (>=5.3) ; extra == 'celery'",  # curated → skipped
                "brand-new-dep>=1.0",  # uncurated → surfaced
                "brand-new-dep>=1.0",  # duplicate → skipped
                " ; extra == 'x'",  # empty name → skipped
            ],
        )
        monkeypatch.setattr(licensing, "_license_from_metadata", lambda name: "GPL-3.0")
        result = audit_uncurated()
        assert result == [("brand-new-dep", "GPL-3.0", Tier.RESTRICTED)]


class TestVerdict:
    def _status(self, key, installed=True):
        return PackageStatus(info=CURATED[key], installed=installed, version="1.0" if installed else None)

    def test_all_permissive_ok(self):
        verdict = commercial_verdict([self._status("django"), self._status("nh3")])
        assert verdict["commercial_ok"] is True
        assert verdict["core_all_permissive"] is True
        assert verdict["concerns"] == []

    def test_installed_restricted_flags_concern(self):
        verdict = commercial_verdict([self._status("django"), self._status("django-ckeditor-5")])
        assert verdict["commercial_ok"] is False
        assert "django-ckeditor-5" in verdict["concerns"]

    def test_uninstalled_restricted_is_not_a_concern(self):
        verdict = commercial_verdict([self._status("django-ckeditor-5", installed=False)])
        assert verdict["commercial_ok"] is True


class TestCompatibility:
    def test_permissive_always_compatible(self):
        assert is_compatible_with(CURATED["django"], "MIT") is True

    def test_restricted_incompatible_with_permissive_target(self):
        assert is_compatible_with(CURATED["django-ckeditor-5"], "MIT") is False

    def test_restricted_ok_with_copyleft_target(self):
        assert is_compatible_with(CURATED["django-ckeditor-5"], "GPL-3.0") is True

    def test_weak_needs_review_for_permissive_target(self):
        assert is_compatible_with(CURATED["paramiko"], "MIT") is None

    def test_unknown_target_needs_review(self):
        assert is_compatible_with(CURATED["paramiko"], "Proprietary") is None

    def test_unknown_dep_needs_review(self):
        info = LicenseInfo("mystery", "Proprietary")
        assert is_compatible_with(info, "MIT") is None

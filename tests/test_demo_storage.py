"""
tests/test_demo_storage.py — the demo's pluggable file storage (#DEMO12)

Local disk is fine for one container. The moment there are two, media must move off the
local filesystem — a file uploaded through instance A is a 404 on instance B, and a
container restart loses it. The demo settings switch Django 5+ ``STORAGES`` between the
local default and any S3-compatible endpoint (AWS S3, Hetzner Object Storage, MinIO,
Backblaze B2) from one environment variable.

These tests load the settings module in isolation, so they check the wiring without
reconfiguring the running test project.
"""

import importlib.util
import pathlib
import sys
import types

import pytest

SETTINGS_PATH = pathlib.Path(__file__).resolve().parent.parent / "demo" / "core" / "settings.py"


def _load_settings(monkeypatch, **env) -> types.ModuleType:
    """Import demo.core.settings fresh under the given environment."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("_demo_settings_probe", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_demo_settings_probe"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("_demo_settings_probe", None)
    return module


S3_ENV = {
    "SNAPADMIN_STORAGE_BACKEND": "s3",
    "AWS_STORAGE_BUCKET_NAME": "demo-bucket",
    "AWS_S3_ENDPOINT_URL": "https://fsn1.your-objectstorage.com",
    "AWS_S3_REGION_NAME": "fsn1",
}


class TestLocalIsTheDefault:
    def test_local_filesystem_and_whitenoise(self, monkeypatch):
        monkeypatch.delenv("SNAPADMIN_STORAGE_BACKEND", raising=False)
        settings = _load_settings(monkeypatch)
        assert settings.STORAGES["default"]["BACKEND"].endswith("FileSystemStorage")
        assert "whitenoise" in settings.STORAGES["staticfiles"]["BACKEND"]

    def test_no_aws_settings_are_defined(self, monkeypatch):
        """Nothing S3-related leaks into a local deployment."""
        monkeypatch.delenv("SNAPADMIN_STORAGE_BACKEND", raising=False)
        settings = _load_settings(monkeypatch)
        assert not hasattr(settings, "AWS_STORAGE_BUCKET_NAME")

    def test_an_unknown_value_falls_back_to_local(self, monkeypatch):
        settings = _load_settings(monkeypatch, SNAPADMIN_STORAGE_BACKEND="gcs")
        assert settings.STORAGES["default"]["BACKEND"].endswith("FileSystemStorage")


class TestS3Backend:
    def test_default_storage_is_s3(self, monkeypatch):
        settings = _load_settings(monkeypatch, **S3_ENV)
        assert settings.STORAGES["default"]["BACKEND"] == "storages.backends.s3.S3Storage"

    def test_endpoint_and_region_are_passed_through(self, monkeypatch):
        settings = _load_settings(monkeypatch, **S3_ENV)
        assert settings.AWS_S3_ENDPOINT_URL == "https://fsn1.your-objectstorage.com"
        assert settings.AWS_S3_REGION_NAME == "fsn1"

    def test_empty_endpoint_becomes_none_for_real_aws(self, monkeypatch):
        """boto3 derives the AWS endpoint from the region; "" would break it."""
        settings = _load_settings(monkeypatch, **{**S3_ENV, "AWS_S3_ENDPOINT_URL": ""})
        assert settings.AWS_S3_ENDPOINT_URL is None

    def test_signed_urls_are_the_default(self, monkeypatch):
        """User uploads live in a private bucket unless you say otherwise."""
        settings = _load_settings(monkeypatch, **S3_ENV)
        assert settings.AWS_QUERYSTRING_AUTH is True

    def test_public_urls_are_opt_in(self, monkeypatch):
        settings = _load_settings(monkeypatch, **{**S3_ENV, "AWS_QUERYSTRING_AUTH": "False"})
        assert settings.AWS_QUERYSTRING_AUTH is False

    def test_overwriting_an_existing_key_is_off(self, monkeypatch):
        """A silent overwrite is how one upload destroys another's file."""
        settings = _load_settings(monkeypatch, **S3_ENV)
        assert settings.AWS_S3_FILE_OVERWRITE is False

    def test_no_acl_is_sent_by_default(self, monkeypatch):
        """Modern buckets disable ACLs; sending one fails the PUT."""
        settings = _load_settings(monkeypatch, **S3_ENV)
        assert settings.AWS_DEFAULT_ACL is None

    def test_static_stays_on_whitenoise_by_default(self, monkeypatch):
        settings = _load_settings(monkeypatch, **S3_ENV)
        assert "whitenoise" in settings.STORAGES["staticfiles"]["BACKEND"]

    def test_static_can_move_to_the_bucket(self, monkeypatch):
        settings = _load_settings(monkeypatch, **{**S3_ENV, "SNAPADMIN_STATIC_ON_S3": "True"})
        assert settings.STORAGES["staticfiles"]["BACKEND"] == "storages.backends.s3.S3StaticStorage"

    def test_custom_domain_drives_static_url(self, monkeypatch):
        settings = _load_settings(monkeypatch, **{
            **S3_ENV,
            "SNAPADMIN_STATIC_ON_S3": "True",
            "AWS_S3_CUSTOM_DOMAIN": "cdn.example.com",
        })
        assert settings.STATIC_URL == "https://cdn.example.com/static/"

    def test_no_custom_domain_leaves_static_url_alone(self, monkeypatch):
        settings = _load_settings(monkeypatch, **{**S3_ENV, "SNAPADMIN_STATIC_ON_S3": "True"})
        assert settings.STATIC_URL == "static/"


class TestDocumentedProviders:
    """dist.env is the copy-paste source, so its examples must stay present."""

    @pytest.fixture(scope="class")
    @staticmethod
    def dist_env():
        path = pathlib.Path(__file__).resolve().parent.parent / "demo" / "dist.env"
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("marker", [
        "SNAPADMIN_STORAGE_BACKEND=local",
        "your-objectstorage.com",          # Hetzner Object Storage endpoint
        "backblazeb2.com",
        "your-storagebox.de",              # Storage Box is SFTP/CIFS, not S3
        "SNAPADMIN_EXPORT_STORAGE",        # exports must share the bucket too
    ])
    def test_example_is_documented(self, dist_env, marker):
        assert marker in dist_env

    def test_storage_box_is_not_described_as_s3(self, dist_env):
        assert "Storage Box is NOT S3" in dist_env

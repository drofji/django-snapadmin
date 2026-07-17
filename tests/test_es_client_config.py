"""
Tests for configurable Elasticsearch client construction (v0.1.0a6):

  ELASTICSEARCH_KWARGS merges into Elasticsearch(...), and
  SNAPADMIN_ES_CLIENT_FACTORY (dotted path or callable) fully overrides it.
"""

import pytest
from django.test import override_settings

from demo.app.models import Product


class _RecordingES:
    last_args = None
    last_kwargs = None

    def __init__(self, hosts, **kwargs):
        _RecordingES.last_args = hosts
        _RecordingES.last_kwargs = kwargs


@pytest.fixture
def recording_es(monkeypatch):
    import elasticsearch

    _RecordingES.last_args = None
    _RecordingES.last_kwargs = None
    monkeypatch.setattr(elasticsearch, "Elasticsearch", _RecordingES)
    return _RecordingES


class TestGetEsClient:
    def test_default_timeout_applied(self, recording_es):
        Product.get_es_client()
        assert recording_es.last_kwargs == {"request_timeout": 5}

    @override_settings(ELASTICSEARCH_KWARGS={"api_key": "secret", "verify_certs": False})
    def test_kwargs_merged(self, recording_es):
        Product.get_es_client()
        assert recording_es.last_kwargs == {
            "request_timeout": 5,
            "api_key": "secret",
            "verify_certs": False,
        }

    @override_settings(ELASTICSEARCH_KWARGS={"request_timeout": 30})
    def test_kwargs_override_default_timeout(self, recording_es):
        Product.get_es_client()
        assert recording_es.last_kwargs["request_timeout"] == 30

    def test_factory_dotted_path_takes_precedence(self):
        sentinel = object()
        with override_settings(
            SNAPADMIN_ES_CLIENT_FACTORY="tests.test_es_client_config.make_fake_client"
        ):
            make_fake_client.result = sentinel
            assert Product.get_es_client() is sentinel

    def test_factory_callable_accepted(self):
        sentinel = object()
        with override_settings(SNAPADMIN_ES_CLIENT_FACTORY=lambda: sentinel):
            assert Product.get_es_client() is sentinel


def make_fake_client():
    return make_fake_client.result

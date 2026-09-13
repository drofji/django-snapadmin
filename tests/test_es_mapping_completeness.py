"""
tests/test_es_mapping_completeness.py

An Elasticsearch mirror that indexes nothing but the id (#EXT1i).

``es_auto_mapping`` is off by default and ``es_mapping`` is ``None`` by
default, so a model that opts into ``DUAL``/``ES_ONLY`` without declaring one
of the two produces documents containing a single key — ``id``. Nothing fails:
the index is created, every save is mirrored, ``es_reindex_all()`` reports the
full row count, and every search comes back empty. ``snapadmin.E026`` is what
turns that into a startup error.

The second half of the report is the documentation itself: ``searchable=True``
was described as feeding "the Elasticsearch mapping", which it never did, and
the shipped ``DUAL`` examples declared no mapping at all. Those are pinned here
too, because a check that fires on the project's own documented example would
only teach people to silence it.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

from django.test import TestCase
from django.test.utils import isolate_apps

from snapadmin import checks
from snapadmin import fields as snap_fields
from snapadmin import models as snap_models

ROOT = Path(__file__).resolve().parent.parent


def _ids(messages) -> list[str]:
    return sorted(m.id for m in messages)


def _run(check, isolated):
    """Run a check against an isolated app registry (see test_encryption_leak_surfaces)."""
    with mock.patch.object(checks, "apps", isolated):
        return check(None)


class TestEmptyEsMappingIsRefused(TestCase):
    def test_dual_without_any_mapping_errors(self):
        with isolate_apps("snapadmin") as isolated:
            class Mirrored(snap_models.SnapModel):
                name = snap_fields.SnapCharField(max_length=50, searchable=True)
                es_storage_mode = snap_models.EsStorageMode.DUAL

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_es_mapping_present, isolated)
        assert _ids(found) == ["snapadmin.E026"]
        assert "nothing but its id" in found[0].msg
        assert "es_auto_mapping = True" in found[0].hint
        assert "searchable=True" in found[0].hint
        assert Mirrored is not None

    def test_es_only_without_any_mapping_errors_and_names_the_data_loss(self):
        with isolate_apps("snapadmin") as isolated:
            class OnlyEs(snap_models.SnapModel):
                name = snap_fields.SnapCharField(max_length=50)
                es_storage_mode = snap_models.EsStorageMode.ES_ONLY

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_es_mapping_present, isolated)
        assert _ids(found) == ["snapadmin.E026"]
        assert "no database table" in found[0].msg
        assert OnlyEs is not None

    def test_es_index_enabled_on_a_db_only_model_errors_too(self):
        """``es_index_enabled`` indexes documents on its own — same empty doc."""
        with isolate_apps("snapadmin") as isolated:
            class Indexed(snap_models.SnapModel):
                name = snap_fields.SnapCharField(max_length=50)
                es_index_enabled = True

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_es_mapping_present, isolated)
        assert _ids(found) == ["snapadmin.E026"]
        assert Indexed is not None

    def test_an_explicit_mapping_is_enough(self):
        with isolate_apps("snapadmin") as isolated:
            class Declared(snap_models.SnapModel):
                name = snap_fields.SnapCharField(max_length=50)
                es_storage_mode = snap_models.EsStorageMode.DUAL
                es_mapping = {"name": {"type": "text"}}

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_es_mapping_present, isolated) == []
            assert Declared is not None

    def test_auto_mapping_is_enough(self):
        with isolate_apps("snapadmin") as isolated:
            class Derived(snap_models.SnapModel):
                name = snap_fields.SnapCharField(max_length=50)
                es_storage_mode = snap_models.EsStorageMode.DUAL
                es_auto_mapping = True

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_es_mapping_present, isolated) == []
            assert Derived is not None

    def test_a_db_only_model_is_never_reported(self):
        with isolate_apps("snapadmin") as isolated:
            class Plain(snap_models.SnapModel):
                name = snap_fields.SnapCharField(max_length=50)

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_es_mapping_present, isolated) == []
            assert Plain is not None

    def test_an_unregistered_model_is_never_reported(self):
        """The check walks every installed model; only SnapAdmin's are its business."""
        from django.db import models as dj_models

        with isolate_apps("snapadmin") as isolated:
            class Outsider(dj_models.Model):
                es_storage_mode = snap_models.EsStorageMode.DUAL

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_es_mapping_present, isolated) == []
            assert Outsider is not None

    def test_the_check_is_registered(self):
        assert checks.check_es_mapping_present in checks.ALL_CHECKS

    def test_the_shipped_demo_passes(self):
        """The live registry — the documented example must not trip its own check."""
        assert checks.check_es_mapping_present(None) == []


class TestTheEncryptedFieldGuardIsNotUndone(TestCase):
    """An encrypted field has no ES mapping by design (#CRYPT1); E026 must respect that."""

    def test_a_model_whose_only_unmapped_fields_are_encrypted_is_quiet(self):
        with isolate_apps("snapadmin") as isolated:
            class Mixed(snap_models.SnapModel):
                title = snap_fields.SnapCharField(max_length=50)
                secret = snap_fields.SnapEncryptedCharField(max_length=50)
                es_storage_mode = snap_models.EsStorageMode.DUAL
                es_auto_mapping = True

                class Meta:
                    app_label = "snapadmin"

            assert _run(checks.check_es_mapping_present, isolated) == []
            # The guard itself is untouched: the encrypted column stays out.
            assert set(Mixed.get_es_mapping()) == {"title"}

    def test_a_model_whose_every_field_is_encrypted_says_why(self):
        """Not a false positive — the documents really would be id-only — but the
        hint must name encryption rather than suggest an option already set."""
        with isolate_apps("snapadmin") as isolated:
            class AllSecret(snap_models.SnapModel):
                secret = snap_fields.SnapEncryptedCharField(max_length=50)
                es_storage_mode = snap_models.EsStorageMode.DUAL
                es_auto_mapping = True

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_es_mapping_present, isolated)
        assert _ids(found) == ["snapadmin.E026"]
        assert "encrypted" in found[0].hint
        assert "es_auto_mapping = True" not in found[0].hint
        assert AllSecret is not None

    def test_auto_mapping_with_no_indexable_field_at_all_says_so(self):
        with isolate_apps("snapadmin") as isolated:
            class Bare(snap_models.SnapModel):
                es_storage_mode = snap_models.EsStorageMode.DUAL
                es_auto_mapping = True

                class Meta:
                    app_label = "snapadmin"

            found = _run(checks.check_es_mapping_present, isolated)
        assert _ids(found) == ["snapadmin.E026"]
        assert "no concrete field" in found[0].hint
        assert Bare is not None


class TestTheDefaultIsDeliberate(TestCase):
    def test_es_auto_mapping_stays_off_by_default(self):
        """Flipping it would newly ship every column of every DUAL model to a
        second datastore — the decision is recorded, not accidental."""
        assert snap_models.SnapModel.es_auto_mapping is False
        assert snap_models.SnapModel.es_mapping is None


class TestTheDocumentedExampleWouldWork(TestCase):
    """A reader copying a shipped example must not land on an E026."""

    def test_searchable_no_longer_claims_to_build_the_es_mapping(self):
        doc = snap_fields.SnapField.__doc__ or ""
        searchable = doc.split("``searchable``", 1)[1].split("``filterable``", 1)[0]
        # The old wording promised "the search mapping"; the corrected one has to
        # say the opposite out loud and point at what does build it.
        assert "the search mapping" not in searchable
        assert "not**" in searchable
        assert "es_mapping" in searchable and "es_auto_mapping" in searchable

    def test_every_dual_or_es_only_docs_example_declares_a_mapping(self):
        """Scan the shipped docs for a code block that opts into ES without one.

        Copyable examples only — ``<pre><code>`` blocks and fenced Markdown, not
        an inline ``<code>es_storage_mode = DUAL</code>`` inside a sentence.
        """
        offenders = []
        for path in (ROOT / "README.md", ROOT / "docs" / "index.html"):
            text = path.read_text(encoding="utf-8")
            for block in re.findall(
                r"<pre><code>(.*?)</code></pre>|```python(.*?)```", text, re.S
            ):
                snippet = block[0] or block[1]
                if not re.search(r"^\s*es_storage_mode\s*=", snippet, re.M):
                    continue
                if re.search(r"^\s*es_storage_mode\s*=.*DB_ONLY", snippet, re.M):
                    continue
                if "es_mapping" in snippet or "es_auto_mapping" in snippet:
                    continue
                offenders.append((path.name, snippet.strip()[:160]))
        assert offenders == [], f"ES examples with no mapping: {offenders}"

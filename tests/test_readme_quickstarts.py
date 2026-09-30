"""
tests/test_readme_quickstarts.py

The README's quickstarts, executed rather than trusted (#DOC11).

A reader copies these snippets verbatim, so a snippet that cannot work is a
defect with the same cost as a bug in the package. Three shipped in the README
for several releases, found by review when #DOC11 rewrote it:

* neither ``Product`` declared ``subject_path``, so ``snapadmin.E011`` — an
  Error — stopped ``migrate`` and ``runserver`` right after "That is the whole
  setup";
* the 60-second try installed the bare package, while the project
  ``snapadmin-new`` generates lists the ``[api]`` and ``[graphql]`` apps in
  ``INSTALLED_APPS`` and so cannot start without them;
* a decorated plain model is searched only by the decorator's
  ``search_fields`` — ``snap_field(searchable=True)`` reaches no surface there —
  so an example relying on the flag served an unfiltered ``?search=``.

Each snippet is pulled out of ``README.md`` by what it declares, executed in an
isolated app registry (no table, no migration, nothing leaking into the sweeps
other tests run over ``apps.get_models()``), and held to the check or the
surface the README promises.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test.utils import isolate_apps

from snapadmin.api.views import DynamicModelViewSet
from snapadmin.checks import _subject_path_errors
from snapadmin.scaffold import render

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"


def _code_blocks(language: str) -> list[str]:
    text = README.read_text(encoding="utf-8")
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def _the_block_declaring(marker: str) -> str:
    matching = [block for block in _code_blocks("python") if marker in block]
    assert len(matching) == 1, (
        f"expected exactly one README python block containing {marker!r}, found {len(matching)}"
    )
    return matching[0]


def _execute_model_snippet(source: str):
    """Run a README model snippet and return the ``Product`` it declares.

    The module name puts the class inside the ``snapadmin`` app, which is how
    Django resolves an ``app_label`` for a model that declares none — exactly
    the situation of a snippet pasted into a project's ``models.py``.
    """
    namespace = {"__name__": "snapadmin.readme_quickstart"}
    with isolate_apps("snapadmin"):
        exec(compile(source, str(README), "exec"), namespace)
    return namespace["Product"]


class TestTheNewProjectQuickstart:
    """``SnapModel`` in 3 steps — step 1's model."""

    @pytest.fixture
    def product(self):
        return _execute_model_snippet(_the_block_declaring("(snap_models.SnapModel)"))

    def test_it_passes_the_subject_access_check(self, product):
        assert _subject_path_errors(product) == []

    def test_search_matches_the_field_it_marks_searchable(self, product):
        # The primary key is always searchable on a generated admin (admin_gen
        # appends it), so "a search box on name" is name plus the id.
        assert DynamicModelViewSet._db_search_fields(product) == ("name", "id")


class TestTheExistingProjectQuickstart:
    """``@snap_model`` on a plain ``models.Model``."""

    @pytest.fixture
    def product(self):
        return _execute_model_snippet(_the_block_declaring("@snap_model("))

    def test_it_passes_the_subject_access_check(self, product):
        assert _subject_path_errors(product) == []

    def test_search_matches_the_fields_the_comment_promises(self, product):
        assert DynamicModelViewSet._db_search_fields(product) == ("name",)


class TestTheSixtySecondTry:
    def test_it_installs_every_extra_the_generated_project_needs(self, tmp_path):
        """The generated ``requirements.txt`` names what the project imports; the
        README's install line has to give the reader at least that."""
        render.generate_project(
            tmp_path / "myshop", project_name="myshop", app_name="catalog", full=False
        )
        requirement = next(
            line
            for line in (tmp_path / "myshop" / "requirements.txt").read_text().splitlines()
            if line.startswith("django-snapadmin")
        )
        needed = set(re.search(r"\[([^\]]*)\]", requirement).group(1).split(","))

        try_it = next(block for block in _code_blocks("bash") if "snapadmin-new myshop" in block)
        install = re.search(r'pip install "?django-snapadmin\[([^\]]*)\]', try_it)
        assert install, "the 60-second try installs the bare package"
        assert needed <= set(install.group(1).split(","))

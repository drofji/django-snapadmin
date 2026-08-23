"""Tests for :mod:`snapadmin.scaffold.validate` (#SCAFF1b)."""

from __future__ import annotations

import pytest

from snapadmin.scaffold import ScaffoldError
from snapadmin.scaffold import validate


class TestValidateIdentifierName:
    def test_valid_unused_name_passes(self):
        validate.validate_identifier_name("myshop", kind="project")  # no raise

    def test_empty_name_rejected(self):
        with pytest.raises(ScaffoldError, match="must provide a project name"):
            validate.validate_identifier_name("", kind="project")

    @pytest.mark.parametrize("name", ["123abc", "my-app", "my app", "my.app", "1"])
    def test_non_identifier_rejected(self, name):
        with pytest.raises(ScaffoldError, match="not a valid"):
            validate.validate_identifier_name(name, kind="project")

    @pytest.mark.parametrize("name", ["os", "sys", "django", "snapadmin", "json"])
    def test_shadowing_an_existing_module_rejected(self, name):
        with pytest.raises(ScaffoldError, match="conflicts with the name of an existing"):
            validate.validate_identifier_name(name, kind="project")

    def test_error_message_names_the_kind(self):
        with pytest.raises(ScaffoldError, match="app name"):
            validate.validate_identifier_name("os", kind="app")

    def test_find_spec_error_is_not_treated_as_a_conflict(self, monkeypatch):
        """A broken/partial install can make find_spec raise; that's not evidence of a
        real conflict, so scaffolding should proceed rather than block on it."""

        def _boom(name):
            raise ImportError("broken finder")

        monkeypatch.setattr(validate, "find_spec", _boom)
        validate.validate_identifier_name("myshop", kind="project")  # no raise


class TestValidateTargetDirectory:
    def test_missing_directory_is_fine(self, tmp_path):
        validate.validate_target_directory(tmp_path / "does-not-exist")  # no raise

    def test_empty_existing_directory_is_fine(self, tmp_path):
        target = tmp_path / "empty"
        target.mkdir()
        validate.validate_target_directory(target)  # no raise

    def test_non_empty_directory_rejected(self, tmp_path):
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "file.txt").write_text("x")
        with pytest.raises(ScaffoldError, match="is not empty"):
            validate.validate_target_directory(target)

    def test_path_is_a_file_rejected(self, tmp_path):
        target = tmp_path / "afile"
        target.write_text("x")
        with pytest.raises(ScaffoldError, match="not a directory"):
            validate.validate_target_directory(target)
